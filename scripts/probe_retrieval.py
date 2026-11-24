"""First retrieval check: do the eval incidents find their supporting documents?

    uv run python scripts/probe_retrieval.py
    uv run python scripts/probe_retrieval.py --detail parent_child

Query = reporter note + alerts + logs, as pasted by an engineer (no rewriting yet).
Measured at document level against data/eval labels (reading labels here is
fine: this is evaluation, not ingestion). A quick probe, not the M5 benchmark.
"""

from __future__ import annotations

import argparse
import asyncio

from ragnition.core.config import get_settings
from ragnition.core.logging import configure_logging
from ragnition.core.providers import ModelGateway
from ragnition.data.paths import EVAL_SCENARIOS_PATH
from ragnition.data.world import EvalScenario, load_eval_set
from ragnition.eval.retrieval_metrics import mean, recall_at_k, reciprocal_rank
from ragnition.ingest.chunking import STRATEGIES
from ragnition.retrieval.retriever import Retriever, rank_docs

CHUNKS_PER_QUERY = 50  # retrieve plenty of chunks, then collapse to documents


def incident_query(s: EvalScenario) -> str:
    inc = s.incident
    return "\n".join([s.title, inc.reporter_note, *inc.alerts, *inc.logs])


async def main(detail: str) -> int:
    configure_logging("WARNING", json_output=False)
    scenarios = load_eval_set(EVAL_SCENARIOS_PATH).scenarios
    answerable = [s for s in scenarios if s.answerable]
    print(f"{len(answerable)} answerable incidents; metrics at document level\n")
    print(f"{'strategy':<14}{'method':<7}{'R@5':>6}{'R@10':>7}{'MRR':>7}")
    async with ModelGateway.from_settings(get_settings()) as gw:
        for strategy in STRATEGIES:
            r = Retriever.open(strategy, gw)
            try:
                rows: dict[str, list[tuple[str, list[str], set[str]]]] = {"dense": [], "bm25": []}
                for s in answerable:
                    q, relevant = incident_query(s), set(s.label.supporting_doc_ids)
                    rows["dense"].append((s.id, rank_docs(await r.dense(q, CHUNKS_PER_QUERY)), relevant))
                    rows["bm25"].append((s.id, rank_docs(r.sparse(q, CHUNKS_PER_QUERY)), relevant))
                for method, data in rows.items():
                    r5 = mean([recall_at_k(d, rel, 5) for _, d, rel in data])
                    r10 = mean([recall_at_k(d, rel, 10) for _, d, rel in data])
                    mrr = mean([reciprocal_rank(d, rel) for _, d, rel in data])
                    print(f"{strategy:<14}{method:<7}{r5:>6.2f}{r10:>7.2f}{mrr:>7.2f}")
                if strategy == detail:
                    detail_rows = rows
            finally:
                r.close()

    print(f"\nPer incident ({detail}): rank of each supporting doc in the top 10, '-' = missed")
    for (sid, dense_docs, rel), (_, bm25_docs, _) in zip(detail_rows["dense"], detail_rows["bm25"], strict=True):
        print(f"\n{sid}")
        for doc in sorted(rel):
            dr = dense_docs[:10].index(doc) + 1 if doc in dense_docs[:10] else "-"
            br = bm25_docs[:10].index(doc) + 1 if doc in bm25_docs[:10] else "-"
            print(f"   dense {dr!s:>2}   bm25 {br!s:>2}   {doc}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--detail", choices=STRATEGIES, default="structure")
    raise SystemExit(asyncio.run(main(p.parse_args().detail)))
