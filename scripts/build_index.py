"""Embed chunks into Qdrant (local mode) for every experiment config.

    uv run python scripts/build_index.py                          # all configs in config/experiments.yaml
    uv run python scripts/build_index.py --config structure-384   # one config
    uv run python scripts/build_index.py --force                  # rebuild even if up to date

Run scripts/chunk_corpus.py first. A collection is rebuilt only if its chunks file
or the embedding model changed (fingerprint). Every vector also goes into an
embedding cache (data/index/embeddings.sqlite3), so text that appears in several
configs is embedded once. Vectors from the M4 indexes are copied into the cache
first, so that work is not repeated. Safe to stop and re-run at any time.
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
from ragnition.eval.experiments import load_experiments
from ragnition.ingest.pipeline import chunks_path
from ragnition.retrieval.dense import (
    FingerprintStore,
    build_collection,
    collection_name,
    fingerprint,
    seed_cache_from_collection,
)
from ragnition.retrieval.embed_cache import EmbeddingCache
from ragnition.retrieval.retriever import FINGERPRINTS_PATH, INDEX_DIR, QDRANT_DIR, load_chunks

LEGACY = ("fixed", "structure", "parent_child")  # M4 collections: chunks_<strategy>


def seed_from_m4(client: QdrantClient, model: str, cache: EmbeddingCache) -> None:
    total = 0
    for strategy in LEGACY:
        path = chunks_path(strategy)
        if path.exists() and client.collection_exists(f"chunks_{strategy}"):
            texts = {c.chunk_id: c.text for c in load_chunks(path)}
            total += seed_cache_from_collection(client, f"chunks_{strategy}", texts, model, cache)
    if total:
        print(f"reused {total} vectors from the M4 indexes (cache now holds {len(cache)})\n")


async def main(config_ids: list[str], force: bool) -> int:
    configure_logging("WARNING", json_output=False)
    experiments = load_experiments()
    configs = [experiments.get(i) for i in config_ids] if config_ids else experiments.configs
    QDRANT_DIR.mkdir(parents=True, exist_ok=True)
    client = QdrantClient(path=str(QDRANT_DIR))
    cache = EmbeddingCache(INDEX_DIR / "embeddings.sqlite3")
    store = FingerprintStore(FINGERPRINTS_PATH)
    try:
        async with ModelGateway.from_settings(get_settings()) as gw:
            spec = gw.executor("embedding").slots[0].spec
            print(f"embedding model: {spec.provider}:{spec.model} ({spec.dimensions} dims)\n")
            seed_from_m4(client, spec.model, cache)
            for cfg in configs:
                path = chunks_path(cfg.id)
                if not path.exists():
                    print(f"{cfg.id}: no chunks file, run scripts/chunk_corpus.py first")
                    return 1
                name, fp = collection_name(cfg.id), fingerprint(path, spec.model, spec.dimensions)
                if not force and store.get(name) == fp and client.collection_exists(name):
                    print(f"{cfg.id}: up to date ({client.count(name).count} vectors), skipped")
                    continue
                before, start = len(cache), time.perf_counter()

                def progress(done: int, total: int, s: str = cfg.id) -> None:
                    print(f"\r{s}: indexed {done}/{total}", end="", flush=True)

                n = await build_collection(client, gw, name, load_chunks(path), spec.dimensions, progress,
                                           model=spec.model, cache=cache)
                store.set(name, fp)
                fresh = len(cache) - before
                print(f"\r{cfg.id}: {n} vectors ({fresh} newly embedded, {n - fresh} from cache) "
                      f"in {time.perf_counter() - start:.0f}s")
    finally:
        cache.close()
        client.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", action="append", default=[])
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.config, args.force)))
