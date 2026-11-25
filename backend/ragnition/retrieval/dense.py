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
from types import SimpleNamespace

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointIdsList,
    PointStruct,
    VectorParams,
)

from ragnition.core.providers import ModelGateway
from ragnition.ingest.chunking import Chunk
from ragnition.retrieval.embed_cache import EmbeddingCache, text_key

EMBED_BATCH = 256  # per gateway call (the gateway batches further by batch_size)


def collection_name(config_id: str) -> str:
    """'structure-384' -> 'chunks_structure_384'."""
    return "chunks_" + config_id.replace("-", "_")


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


async def embed_passages(gw: ModelGateway, texts: list[str], model: str, cache: EmbeddingCache | None) -> list[list[float]]:
    """Embed passages, reusing cached vectors for texts already embedded by `model`."""
    keys = [text_key(model, t) for t in texts]
    # `is not None`, not truthiness: an EMPTY cache has len 0 and would count as False.
    known = cache.get_many(keys) if cache is not None else {}
    missing = [i for i, k in enumerate(keys) if k not in known]
    if missing:
        resp = await gw.embed([texts[i] for i in missing], input_type="document", use_cache=False)
        fresh = {keys[i]: v for i, v in zip(missing, resp.vectors, strict=True)}
        if cache is not None:
            cache.put_many(fresh)
        known |= fresh
    return [known[k] for k in keys]


def seed_cache_from_collection(client: QdrantClient, name: str, texts_by_chunk: dict[str, str], model: str,
                               cache: EmbeddingCache) -> int:
    """Copy vectors out of an existing collection into the cache (no re-embedding)."""
    if not client.collection_exists(name):
        return 0
    seeded, offset = 0, None
    while True:
        points, offset = client.scroll(name, limit=512, offset=offset, with_payload=["chunk_id"], with_vectors=True)
        batch = {text_key(model, texts_by_chunk[p.payload["chunk_id"]]): list(p.vector)
                 for p in points if p.payload["chunk_id"] in texts_by_chunk}
        cache.put_many(batch)
        seeded += len(batch)
        if offset is None:
            return seeded


async def build_collection(
    client: QdrantClient, gw: ModelGateway, name: str, chunks: list[Chunk], dimensions: int,
    progress: Callable[[int, int], None] = lambda done, total: None,
    *, model: str = "", cache: EmbeddingCache | None = None,
) -> int:
    """(Re)create the collection and embed every searchable chunk (parents excluded)."""
    if client.collection_exists(name):
        client.delete_collection(name)
    client.create_collection(name, vectors_config=VectorParams(size=dimensions, distance=Distance.COSINE))
    embedded = [c for c in chunks if c.role != "parent"]
    for start in range(0, len(embedded), EMBED_BATCH):
        batch = embedded[start : start + EMBED_BATCH]
        resp = SimpleNamespace(vectors=await embed_passages(gw, [c.text for c in batch], model, cache))
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
    # Never trust delete_collection alone: in local mode on Windows it removes files with
    # rmtree(ignore_errors=True) while SQLite still holds them open, and create_collection
    # then reopens the OLD points. So remove every point that is not a current chunk.
    keep = {point_id(c.chunk_id) for c in embedded}
    stale = prune_stale_points(client, name, keep)
    count = client.count(name, exact=True).count
    if count != len(keep):
        raise RuntimeError(f"{name}: {count} points after build, expected {len(keep)} (pruned {stale})")
    return len(embedded)


def prune_stale_points(client: QdrantClient, name: str, keep: set[str]) -> int:
    stale, offset = [], None
    while True:
        points, offset = client.scroll(name, limit=1024, offset=offset, with_payload=False, with_vectors=False)
        stale += [p.id for p in points if str(p.id) not in keep]
        if offset is None:
            break
    if stale:
        client.delete(name, points_selector=PointIdsList(points=stale))
    return len(stale)


def dense_search(client: QdrantClient, name: str, vector: list[float], k: int,
                 query_filter: Filter | None = None) -> list[tuple[str, float]]:
    res = client.query_points(collection_name=name, query=vector, limit=k, query_filter=query_filter,
                              with_payload=["chunk_id"])
    return [(p.payload["chunk_id"], p.score) for p in res.points]
