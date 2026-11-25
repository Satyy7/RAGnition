"""M7 benchmark: reranker bake-off (local bge / Jina / Cohere) x naive / blend / slot-preserving.

    uv run python scripts/run_m7_benchmark.py --split dev --apply     # tune on DEV, save config/rerank*.yaml
    uv run python scripts/run_m7_benchmark.py --split test            # score the frozen TEST set ONCE
    ... --rerankers bge-local jina-v3                                  # subset (e.g. no Cohere key)

Each reranker is smoke-tested first (one tiny call) so a wrong model id or missing
key fails clearly instead of being scored as a passthrough. Test-run guards as in
M6: hash check, dev-tuned settings required, one run (logged; --rerun overrides).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, datetime, timezone

import yaml

from ragnition.core.config import PROJECT_ROOT, get_settings
from ragnition.core.logging import configure_logging
from ragnition.core.providers import AllProvidersExhausted, ModelGateway
from ragnition.data.paths import EVAL_DIR, EVAL_SCENARIOS_PATH, EVAL_TEST_SCENARIOS_PATH, WORLD_PATH
from ragnition.data.world import load_all_eval, load_world
from ragnition.eval.benchmark import incident_query
from ragnition.eval.experiments import load_experiments, load_retrieval_setup
from ragnition.eval.freeze import FrozenSetError, verify
from ragnition.eval.m7 import RERANKERS, Prepared, evaluate, render_report, select, tune
from ragnition.retrieval.changes import ChangeRetriever, load_change_params
from ragnition.retrieval.evidence import condense_incident
from ragnition.retrieval.incident import MAX_DEPTH, RerankSetup, gather, save_rerank_setup
from ragnition.retrieval.retriever import Retriever

RESULTS_DIR = EVAL_DIR / "results"
BAKEOFF_PATH = PROJECT_ROOT / "config" / "rerank_bakeoff.yaml"
TEST_LOGS = [RESULTS_DIR / "m6_test_runs.jsonl", RESULTS_DIR / "m7_test_runs.jsonl"]


def count_lines(path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0


async def smoke(gw: ModelGateway, rerankers: list[str]) -> list[str]:
    bad = []
    for short in rerankers:
        try:
            await gw.rerank("payments failing", ["pgbouncer pool size", "cdn cache rule"], pin=RERANKERS[short],
                            degrade=False, use_cache=False)
        except (AllProvidersExhausted, ValueError) as exc:
            bad.append(f"{short}: {exc}")
    return bad


async def main(split: str, apply: bool, rerun: bool, rerankers: list[str]) -> int:
    configure_logging("WARNING", json_output=False)
    world = load_world(WORLD_PATH)
    evalset = load_all_eval(EVAL_SCENARIOS_PATH, EVAL_TEST_SCENARIOS_PATH)
    evalset.validate_against(world)
    scenarios = [s for s in evalset.split(split) if s.answerable]
    change_params = load_change_params()
    test_sha = run_number = test_use = None
    setups: dict[str, RerankSetup] = {}
    selected = None

    if split == "test":
        try:
            test_sha = verify()
        except FrozenSetError as exc:
            print(f"refusing to run: {exc}")
            return 1
        if not BAKEOFF_PATH.exists():
            print("refusing to run: no dev-tuned settings (run --split dev --apply first)")
            return 1
        saved = yaml.safe_load(BAKEOFF_PATH.read_text(encoding="utf-8"))
        setups = {k: RerankSetup.model_validate(v) for k, v in saved["setups"].items() if k in rerankers}
        selected = saved["selected"]
        if selected not in setups:
            print(f"refusing to run: the dev-selected reranker '{selected}' must be included")
            return 1
        m7_runs = count_lines(TEST_LOGS[1])
        if m7_runs and not rerun:
            print(f"refusing to run: M7 already scored the test set {m7_runs} time(s); use --rerun only for a bug fix")
            return 1
        run_number, test_use = m7_runs + 1, sum(count_lines(p) for p in TEST_LOGS) + 1

    setup = load_retrieval_setup()
    instruction = load_experiments().query_instruction if setup.query_instruction else ""
    print(f"{split}: {len(scenarios)} answerable incidents; rerankers: {', '.join(rerankers)}")

    async with ModelGateway.from_settings(get_settings()) as gw:
        if bad := await smoke(gw, rerankers):
            print("reranker smoke test failed (fix the key/model id or drop it with --rerankers):")
            for b in bad:
                print(f"  - {b}")
            return 1
        retriever = Retriever.open(setup.config, gw)
        try:
            changes = ChangeRetriever(world, change_params)
            vectors = (await gw.embed([instruction + incident_query(s) for s in scenarios], input_type="query")).vectors
            gathered = {s.id: gather(retriever, changes, v, incident_query(s), s.reported_at, change_params)
                        for s, v in zip(scenarios, vectors, strict=True)}
        finally:
            retriever.close()

        prep = Prepared(order={sid: g.order for sid, g in gathered.items()}, scores={},
                        latency_ms={r: [] for r in rerankers})
        for short in rerankers:
            for condense in (True, False):
                prep.scores[(short, condense)] = {}
                for n, s in enumerate(scenarios, start=1):
                    print(f"\r  reranking {short} (condensed={condense}) {n}/{len(scenarios)}", end="", flush=True)
                    g = gathered[s.id]
                    head = g.order[:MAX_DEPTH]
                    inc = s.incident
                    query = (condense_incident(s.title, inc.reporter_note, inc.alerts, inc.logs) if condense
                             else incident_query(s))
                    resp = await gw.rerank(query, [g.passages[d] for d in head], pin=RERANKERS[short], degrade=False)
                    prep.scores[(short, condense)][s.id] = {head[r.index]: r.score for r in resp.results}
                    prep.latency_ms[short].append(resp.latency_ms)
            print()

    b = load_experiments().bootstrap
    today = date.today().isoformat()
    dev_table = None
    if split == "dev":
        setups, dev_table = tune(scenarios, prep, rerankers)
        setups = {k: v.model_copy(update={"chosen_by": f"m7 dev grid {today}"}) for k, v in setups.items()}
        selected = select(dev_table)
        print("dev nDCG@10 (slot-preserving): " + ", ".join(f"{k}={v:.3f}" for k, v in dev_table.items())
              + f" -> selected {selected}")
        if apply:
            BAKEOFF_PATH.write_text("# Written by run_m7_benchmark.py --split dev --apply (DEV only).\n" +
                                    yaml.safe_dump({"selected": selected,
                                                    "setups": {k: v.model_dump() for k, v in setups.items()}},
                                                   sort_keys=False), encoding="utf-8")
            save_rerank_setup(setups[selected])
            print("saved config/rerank.yaml and config/rerank_bakeoff.yaml")

    run = evaluate(scenarios, prep, setups)
    report = render_report(run, prep, setups, selected, split, today, b.resamples, b.seed, dev_table=dev_table,
                           test_sha=test_sha, run_number=run_number, test_use=test_use)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"m7_{split}_{today}" + (f"_run{run_number}" if run_number and run_number > 1 else "")
    (RESULTS_DIR / f"{stem}.md").write_text(report, encoding="utf-8")
    (RESULTS_DIR / f"{stem}.json").write_text(json.dumps({
        "split": split, "date": today, "selected": selected, "setups": {k: v.model_dump() for k, v in setups.items()},
        "test_sha256": test_sha, "per_query": run.per_query, "rankings": run.rankings,
        "latency_ms": prep.latency_ms}, indent=1), encoding="utf-8")
    if split == "test":
        with TEST_LOGS[1].open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"run": run_number, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                 "sha256": test_sha, "selected": selected}) + "\n")
    print("\n" + report.split("## Per incident")[0])
    print(f"full report: {(RESULTS_DIR / f'{stem}.md').relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--split", choices=["dev", "test"], required=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--rerun", action="store_true")
    p.add_argument("--rerankers", nargs="+", choices=list(RERANKERS), default=list(RERANKERS))
    a = p.parse_args()
    raise SystemExit(asyncio.run(main(a.split, a.apply, a.rerun, a.rerankers)))
