"""Embed chunks into Qdrant (local mode) for every chunking strategy.

    uv run python scripts/build_index.py            # build what is missing or out of date
    uv run python scripts/build_index.py --force    # rebuild everything

Run scripts/chunk_corpus.py first. A collection is rebuilt only if its chunks
file or the embedding model changed (fingerprint check). BM25 needs no build
step: it is rebuilt in memory (well under a second) whenever a Retriever opens.
Close any other process using data/index/qdrant first (local-mode lock).
"""

from __future__ import annotations

import argparse
import asyncio
import time

from qdrant_client import QdrantClient

from ragnition.core.config import get_settings
from ragnition.core.logging import configure_logging
from ragnition.core.providers import ModelGateway
from ragnition.ingest.chunking import STRATEGIES
from ragnition.ingest.pipeline import chunks_path
from ragnition.retrieval.dense import FingerprintStore, build_collection, collection_name, fingerprint
from ragnition.retrieval.retriever import FINGERPRINTS_PATH, QDRANT_DIR, load_chunks


async def main(force: bool) -> int:
    configure_logging("WARNING", json_output=False)
    QDRANT_DIR.mkdir(parents=True, exist_ok=True)
    client = QdrantClient(path=str(QDRANT_DIR))
    store = FingerprintStore(FINGERPRINTS_PATH)
    try:
        async with ModelGateway.from_settings(get_settings()) as gw:
            spec = gw.executor("embedding").slots[0].spec
            print(f"embedding model: {spec.provider}:{spec.model} ({spec.dimensions} dims)\n")
            for strategy in STRATEGIES:
                path = chunks_path(strategy)
                if not path.exists():
                    print(f"{strategy}: no chunks file, run scripts/chunk_corpus.py first")
                    return 1
                name, fp = collection_name(strategy), fingerprint(path, spec.model, spec.dimensions)
                if not force and store.get(name) == fp and client.collection_exists(name):
                    print(f"{strategy}: up to date ({client.count(name).count} vectors), skipped")
                    continue
                start = time.perf_counter()

                def progress(done: int, total: int, s: str = strategy) -> None:
                    print(f"\r{s}: embedded {done}/{total}", end="", flush=True)

                n = await build_collection(client, gw, name, load_chunks(path), spec.dimensions, progress)
                store.set(name, fp)
                secs = time.perf_counter() - start
                print(f"\r{strategy}: {n} vectors in {secs:.0f}s ({n / secs:.1f} chunks/s)")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true")
    raise SystemExit(asyncio.run(main(parser.parse_args().force)))
