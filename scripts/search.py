"""Search the corpus and see what comes back, from dense and BM25 side by side.

    uv run python scripts/search.py "payments timing out waiting for database connections"
    uv run python scripts/search.py "cl_waiting" --method bm25 --k 3
    uv run python scripts/search.py "pool size change" --doc-type change --config parent_child-256

Default config comes from config/retrieval.yaml.
"""

from __future__ import annotations

import argparse
import asyncio

from ragnition.core.config import get_settings
from ragnition.core.logging import configure_logging
from ragnition.core.providers import ModelGateway
from ragnition.eval.experiments import load_experiments, load_retrieval_setup
from ragnition.retrieval.retriever import Hit, Retriever


def show(title: str, hits: list[Hit]) -> None:
    print(f"\n=== {title} ===")
    for h in hits:
        preview = " ".join(h.chunk.body.split())[:180]
        print(f"{h.rank:>2}. {h.score:7.3f}  {h.chunk.doc_id}\n    [{h.chunk.header}]\n    {preview}")


async def main(args) -> int:
    configure_logging("WARNING", json_output=False)
    setup = load_retrieval_setup()
    config = args.config or setup.config
    query = args.query
    if setup.query_instruction:
        query = load_experiments().query_instruction + query  # dense only; BM25 ignores the prefix words
    async with ModelGateway.from_settings(get_settings()) as gw:
        r = Retriever.open(config, gw)
        try:
            if args.method in ("dense", "both"):
                show(f"dense ({config})", await r.dense(query, args.k, args.doc_type, args.tag))
            if args.method in ("bm25", "both"):
                show(f"bm25 ({config})", r.sparse(args.query, args.k, args.doc_type, args.tag))
        finally:
            r.close()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("query")
    p.add_argument("--config", help="experiment config id, e.g. structure-384 (default: config/retrieval.yaml)")
    p.add_argument("--method", choices=["dense", "bm25", "both"], default="both")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--doc-type", help="e.g. change, postmortem, runbook, service, documentation")
    p.add_argument("--tag", help="e.g. a service id such as payments-api")
    raise SystemExit(asyncio.run(main(p.parse_args())))
