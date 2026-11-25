"""One object that owns the indexes for one chunking strategy.

    retriever = Retriever.open("structure", gateway)   # loads chunks, BM25, Qdrant
    hits = await retriever.dense("why are payments timing out?", k=10)
    hits = retriever.sparse("cl_waiting pgbouncer", k=10)

Hits are chunks; `rank_docs` turns them into a ranked list of documents (each
document counted once, at its best chunk), which is what the eval labels name.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from qdrant_client import QdrantClient

from ragnition.core.providers import ModelGateway
from ragnition.data.paths import DATA_DIR
from ragnition.ingest.chunking import Chunk
from ragnition.ingest.pipeline import chunks_path
from ragnition.retrieval.bm25 import BM25Index
from ragnition.retrieval.dense import build_filter, collection_name, dense_search

INDEX_DIR = DATA_DIR / "index"
QDRANT_DIR = INDEX_DIR / "qdrant"
FINGERPRINTS_PATH = INDEX_DIR / "fingerprints.json"


@dataclass
class Hit:
    chunk: Chunk
    score: float
    rank: int  # 1-based
    method: str  # "dense" | "bm25"


def load_chunks(path: Path) -> list[Chunk]:
    return [Chunk.model_validate(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line]


def rank_docs(hits: list[Hit]) -> list[str]:
    seen, docs = set(), []
    for h in hits:
        if h.chunk.doc_id not in seen:
            seen.add(h.chunk.doc_id)
            docs.append(h.chunk.doc_id)
    return docs


class Retriever:
    def __init__(self, strategy: str, chunks: list[Chunk], client: QdrantClient, gw: ModelGateway) -> None:
        self.strategy = strategy
        self.client = client
        self.gw = gw
        self.by_id = {c.chunk_id: c for c in chunks}
        self.searchable = [c for c in chunks if c.role != "parent"]
        self.bm25 = BM25Index([c.chunk_id for c in self.searchable], [c.text for c in self.searchable])

    @classmethod
    def open(cls, config_id: str, gw: ModelGateway, client: QdrantClient | None = None) -> Retriever:
        """`config_id` is an experiment config such as 'structure-384'."""
        client = client or QdrantClient(path=str(QDRANT_DIR))
        if not client.collection_exists(collection_name(config_id)):
            raise RuntimeError(f"no index for '{config_id}': run scripts/build_index.py first")
        return cls(config_id, load_chunks(chunks_path(config_id)), client, gw)

    def parent(self, chunk: Chunk) -> Chunk | None:
        return self.by_id.get(chunk.parent_id) if chunk.parent_id else None

    def _hits(self, pairs: list[tuple[str, float]], method: str) -> list[Hit]:
        return [Hit(self.by_id[cid], score, i + 1, method) for i, (cid, score) in enumerate(pairs)]

    async def dense(self, query: str, k: int = 10, doc_type: str | None = None, tag: str | None = None) -> list[Hit]:
        vector = (await self.gw.embed([query], input_type="query")).vectors[0]
        return self.dense_by_vector(vector, k, doc_type, tag)

    def dense_by_vector(self, vector: list[float], k: int = 10, doc_type: str | None = None,
                        tag: str | None = None) -> list[Hit]:
        """Search with a precomputed query vector (the benchmark embeds each query once)."""
        pairs = dense_search(self.client, collection_name(self.strategy), vector, k, build_filter(doc_type, tag))
        return self._hits(pairs, "dense")

    def sparse(self, query: str, k: int = 10, doc_type: str | None = None, tag: str | None = None) -> list[Hit]:
        allowed = None
        if doc_type or tag:
            allowed = {i for i, c in enumerate(self.searchable)
                       if (not doc_type or c.doc_type == doc_type) and (not tag or tag in c.tags)}
        return self._hits(self.bm25.search(query, k, allowed), "bm25")

    def close(self) -> None:
        self.client.close()
