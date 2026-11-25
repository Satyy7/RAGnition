"""Quick retrieval check on the 10 answerable eval incidents for one config.

    uv run python scripts/probe_retrieval.py                       # config from config/retrieval.yaml
    uv run python scripts/probe_retrieval.py --config fixed-256

Shows the rank of each supporting document ('-' = not in the top 10). For the
full comparison with confidence intervals use scripts/run_retrieval_benchmark.py.
"""

from __future__ import annotations

import argparse
import asyncio

from ragnition.core.config import get_settings
from ragnition.core.logging import configure_logging
from ragnition.core.providers import ModelGateway
from ragnition.data.paths import EVAL_SCENARIOS_PATH
from ragnition.data.world import load_eval_set
from ragnition.eval.benchmark import incident_query
from ragnition.eval.experiments import load_retrieval_setup
from ragnition.retrieval.retriever import Retriever, rank_docs


async def main(config: str) -> int:
    configure_logging("WARNING", json_output=False)
    answerable = [s for s in load_eval_set(EVAL_SCENARIOS_PATH).scenarios if s.answerable]
    async with ModelGateway.from_settings(get_settings()) as gw:
        r = Retriever.open(config, gw)
        try:
            print(f"config {config}: rank of each supporting doc in the top 10, '-' = missed")
            for s in answerable:
                q = incident_query(s)
                dense_docs = rank_docs(await r.dense(q, 50))[:10]
                bm25_docs = rank_docs(r.sparse(q, 50))[:10]
                print(f"\n{s.id}  {s.title}")
                for doc in sorted(s.label.supporting_doc_ids):
                    dr = dense_docs.index(doc) + 1 if doc in dense_docs else "-"
                    br = bm25_docs.index(doc) + 1 if doc in bm25_docs else "-"
                    print(f"   dense {dr!s:>2}   bm25 {br!s:>2}   {doc}")
        finally:
            r.close()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=None)
    args = p.parse_args()
    raise SystemExit(asyncio.run(main(args.config or load_retrieval_setup().config)))
