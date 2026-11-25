"""M6 benchmark: hybrid fusion + change-aware retrieval on incident queries.

    uv run python scripts/run_m6_benchmark.py --split dev --apply   # tune on DEV, save config/change_retrieval.yaml
    uv run python scripts/run_m6_benchmark.py --split test          # score the frozen TEST set ONCE

The test run refuses to start if the test set changed since it was frozen, if the
parameters were not tuned on dev, or if a test run already happened (every run is
logged in data/eval/results/m6_test_runs.jsonl; --rerun overrides and the report
then says which run number it is). Reports: data/eval/results/m6_<split>_<date>.md/.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, datetime, timezone

from ragnition.core.config import PROJECT_ROOT, get_settings
from ragnition.core.logging import configure_logging
from ragnition.core.providers import ModelGateway
from ragnition.data.paths import EVAL_DIR, EVAL_SCENARIOS_PATH, EVAL_TEST_SCENARIOS_PATH, WORLD_PATH
from ragnition.data.world import load_all_eval, load_world
from ragnition.eval.benchmark import incident_query
from ragnition.eval.experiments import load_experiments, load_retrieval_setup
from ragnition.eval.freeze import FrozenSetError, verify
from ragnition.eval.m6 import TextLists, evaluate, render_report, tune_on_dev
from ragnition.retrieval.changes import CHANGE_PARAMS_PATH, load_change_params, save_change_params
from ragnition.retrieval.retriever import Retriever, rank_docs

RESULTS_DIR = EVAL_DIR / "results"
TEST_RUNS_LOG = RESULTS_DIR / "m6_test_runs.jsonl"
CHUNKS_PER_QUERY = 50


async def main(split: str, apply: bool, rerun: bool) -> int:
    configure_logging("WARNING", json_output=False)
    world = load_world(WORLD_PATH)
    evalset = load_all_eval(EVAL_SCENARIOS_PATH, EVAL_TEST_SCENARIOS_PATH)
    evalset.validate_against(world)
    scenarios = evalset.split(split)
    test_sha, run_number = None, None

    if split == "test":
        try:
            test_sha = verify()
        except FrozenSetError as exc:
            print(f"refusing to run: {exc}")
            return 1
        params = load_change_params()
        if not CHANGE_PARAMS_PATH.exists() or not params.chosen_by.startswith("m6 dev grid"):
            print("refusing to run: parameters not tuned on dev yet (run --split dev --apply first)")
            return 1
        previous = TEST_RUNS_LOG.read_text(encoding="utf-8").splitlines() if TEST_RUNS_LOG.exists() else []
        if previous and not rerun:
            print(f"refusing to run: the test set was already scored {len(previous)} time(s). "
                  "Re-running after looking at results is how test sets get overfit. Use --rerun only for a "
                  "bug fix, and say so in the report.")
            return 1
        run_number = len(previous) + 1

    setup = load_retrieval_setup()
    instruction = load_experiments().query_instruction if setup.query_instruction else ""
    print(f"{split}: {len(scenarios)} incidents ({sum(s.answerable for s in scenarios)} answerable); "
          f"text retrieval {setup.config}, query instruction {'on' if instruction else 'off'}")

    async with ModelGateway.from_settings(get_settings()) as gw:
        retriever = Retriever.open(setup.config, gw)
        try:
            answerable = [s for s in scenarios if s.answerable]
            vectors = (await gw.embed([instruction + incident_query(s) for s in answerable], input_type="query")).vectors
            text = {s.id: TextLists(bm25=rank_docs(retriever.sparse(incident_query(s), CHUNKS_PER_QUERY)),
                                    dense=rank_docs(retriever.dense_by_vector(v, CHUNKS_PER_QUERY)))
                    for s, v in zip(answerable, vectors, strict=True)}
        finally:
            retriever.close()

    exp = load_experiments().bootstrap
    today = date.today().isoformat()
    grid_rows = None
    if split == "dev":
        params, grid_rows = tune_on_dev(scenarios, text, world)
        params = params.model_copy(update={"chosen_by": f"m6 dev grid {today}"})
        print(f"dev grid: {len(grid_rows)} settings tried; best window={params.window_days:g}d "
              f"tau={params.tau_days:g}d hop1={params.proximity[1]:g} fusion_weight={params.fusion_weight:g}")
        if apply:
            save_change_params(params)
            print(f"saved {CHANGE_PARAMS_PATH.relative_to(PROJECT_ROOT)}")
    else:
        params = load_change_params()

    run = evaluate(scenarios, text, world, params)
    report = render_report(run, split, today, exp.resamples, exp.seed, test_sha=test_sha, run_number=run_number,
                           grid_top=grid_rows)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"m6_{split}_{today}" + (f"_run{run_number}" if run_number and run_number > 1 else "")
    (RESULTS_DIR / f"{stem}.md").write_text(report, encoding="utf-8")
    (RESULTS_DIR / f"{stem}.json").write_text(json.dumps({
        "split": split, "date": today, "params": params.model_dump(), "test_sha256": test_sha,
        "per_query": run.per_query, "rankings": run.rankings}, indent=1), encoding="utf-8")
    if split == "test":
        with TEST_RUNS_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"run": run_number, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                 "sha256": test_sha, "params": params.chosen_by}) + "\n")

    print("\n" + report.split("## Per incident")[0])
    print(f"full report: {(RESULTS_DIR / f'{stem}.md').relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--split", choices=["dev", "test"], required=True)
    p.add_argument("--apply", action="store_true", help="dev only: save the tuned parameters")
    p.add_argument("--rerun", action="store_true", help="test only: allow another test run (logged)")
    a = p.parse_args()
    raise SystemExit(asyncio.run(main(a.split, a.apply, a.rerun)))
