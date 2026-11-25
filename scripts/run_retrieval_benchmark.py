"""Run the M5 retrieval benchmark over every config in config/experiments.yaml.

    uv run python scripts/run_retrieval_benchmark.py           # report only
    uv run python scripts/run_retrieval_benchmark.py --apply   # also write config/retrieval.yaml

Needs: scripts/generate_retrieval_queries.py, scripts/chunk_corpus.py and
scripts/build_index.py done for all configs. Writes
data/eval/results/m5_<date>.md (report) and .json (every per-query score).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date

from qdrant_client import QdrantClient

from ragnition.core.config import PROJECT_ROOT, get_settings
from ragnition.core.logging import configure_logging
from ragnition.core.providers import ModelGateway
from ragnition.data.paths import EVAL_DIR, EVAL_SCENARIOS_PATH
from ragnition.data.world import load_eval_set
from ragnition.eval.benchmark import decide, incident_queries, render_report, run_benchmark
from ragnition.eval.experiments import RetrievalSetup, load_experiments, save_retrieval_setup
from ragnition.eval.query_gen import RetrievalQuery
from ragnition.retrieval.dense import collection_name
from ragnition.retrieval.retriever import QDRANT_DIR

QUERIES_PATH = EVAL_DIR / "retrieval_queries.jsonl"
RESULTS_DIR = EVAL_DIR / "results"


async def main(apply: bool) -> int:
    configure_logging("WARNING", json_output=False)
    experiments = load_experiments()
    if not QUERIES_PATH.exists():
        print("no generated queries: run scripts/generate_retrieval_queries.py first")
        return 1
    generated = [RetrievalQuery.model_validate_json(line)
                 for line in QUERIES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    queries = [q for q in generated if q.kept] + incident_queries(load_eval_set(EVAL_SCENARIOS_PATH).scenarios)
    print(f"{len(queries)} queries ({sum(q.origin == 'incident' for q in queries)} incidents)")

    client = QdrantClient(path=str(QDRANT_DIR))
    try:
        missing = [c.id for c in experiments.configs if not client.collection_exists(collection_name(c.id))]
        if missing:
            print(f"not indexed yet: {missing} -> run scripts/chunk_corpus.py and scripts/build_index.py")
            return 1
        async with ModelGateway.from_settings(get_settings()) as gw:
            run = await run_benchmark(gw, client, experiments, queries)
    finally:
        client.close()

    decision = decide(run, experiments)
    today = date.today().isoformat()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    report = render_report(run, experiments, decision, today)
    (RESULTS_DIR / f"m5_{today}.md").write_text(report, encoding="utf-8")
    (RESULTS_DIR / f"m5_{today}.json").write_text(json.dumps({
        "date": today, "queries": [q.model_dump() for q in queries], "per_query": run.per_query,
        "latency_ms": run.latency_ms, "vectors": run.vectors,
        "decision": {"config": decision.config, "use_instruction": decision.use_instruction,
                     "tied": decision.tied, "best_by_mean": decision.best_by_mean},
    }, indent=1), encoding="utf-8")

    print("\n" + report.split("## All systems")[0])
    print(f"full report: {(RESULTS_DIR / f'm5_{today}.md').relative_to(PROJECT_ROOT)}")
    if apply:
        save_retrieval_setup(RetrievalSetup(config=decision.config, query_instruction=decision.use_instruction,
                                            chosen_by=f"m5 benchmark {today}"))
        print("config/retrieval.yaml updated")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true")
    raise SystemExit(asyncio.run(main(p.parse_args().apply)))
