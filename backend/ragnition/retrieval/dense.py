"""Dense (vector) index in Qdrant local mode: no server, data in a folder.

One collection per chunking strategy. Point ids are derived from chunk ids, so
re-indexing overwrites instead of duplicating. A fingerprint (chunks file hash +
embedding model) decides whether an existing collection is still valid.
Only ONE process may open the folder at a time (local-mode file lock).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams

from ragnition.core.providers import ModelGateway
from ragnition.ingest.chunking import Chunk

EMBED_BATCH = 256  # per gateway call (the gateway batches further by batch_size)


def collection_name(strategy: str) -> str:
    return f"chunks_{strategy}"


def point_id(chunk_id: str) -> str:
    """Qdrant ids must be ints or UUIDs: derive a stable UUID from the chunk id."""
    return str(uuid.UUID(hex=hashlib.sha256(chunk_id.encode()).hexdigest()[:32]))


def fingerprint(chunks_file: Path, model: str, dimensions: int) -> str:
    h = hashlib.sha256(chunks_file.read_bytes())
    h.update(f"|{model}|{dimensions}".encode())
    return h.hexdigest()[:16]


class FingerprintStore:
    """Sidecar JSON {collection: fingerprint} next to the Qdrant folder."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, str] = json.loads(path.read_text()) if path.exists() else {}

    def get(self, name: str) -> str | None:
        return self.data.get(name)

    def set(self, name: str, value: str) -> None:
        self.data[name] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2))


def build_filter(doc_type: str | None = None, tag: str | None = None) -> Filter | None:
    must = []
    if doc_type:
        must.append(FieldCondition(key="doc_type", match=MatchValue(value=doc_type)))
    if tag:  # tags is a list payload: matches if any element equals the value
        must.append(FieldCondition(key="tags", match=MatchValue(value=tag)))
    return Filter(must=must) if must else None


async def build_collection(
    client: QdrantClient, gw: ModelGateway, name: str, chunks: list[Chunk], dimensions: int,
    progress: Callable[[int, int], None] = lambda done, total: None,
) -> int:
    """(Re)create the collection and embed every searchable chunk (parents excluded)."""
    if client.collection_exists(name):
        client.delete_collection(name)
    client.create_collection(name, vectors_config=VectorParams(size=dimensions, distance=Distance.COSINE))
    embedded = [c for c in chunks if c.role != "parent"]
    for start in range(0, len(embedded), EMBED_BATCH):
        batch = embedded[start : start + EMBED_BATCH]
        resp = await gw.embed([c.text for c in batch], input_type="document", use_cache=False)
        client.upsert(
            collection_name=name,
            points=[
                PointStruct(
                    id=point_id(c.chunk_id), vector=v,
                    payload={"chunk_id": c.chunk_id, "doc_id": c.doc_id, "doc_type": c.doc_type, "tags": c.tags,
                             "source_id": c.source_id, "synthetic": c.synthetic, "role": c.role},
                )
                for c, v in zip(batch, resp.vectors, strict=True)
            ],
        )
        progress(min(start + EMBED_BATCH, len(embedded)), len(embedded))
    return len(embedded)


def dense_search(client: QdrantClient, name: str, vector: list[float], k: int,
                 query_filter: Filter | None = None) -> list[tuple[str, float]]:
    res = client.query_points(collection_name=name, query=vector, limit=k, query_filter=query_filter,
                              with_payload=["chunk_id"])
    return [(p.payload["chunk_id"], p.score) for p in res.points]
