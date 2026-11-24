"""Tokenizer, BM25, metrics, and Qdrant in-memory mode with a fake embedder."""

from types import SimpleNamespace

import pytest
from qdrant_client import QdrantClient

from ragnition.eval.retrieval_metrics import recall_at_k, reciprocal_rank
from ragnition.ingest.chunking import Chunk
from ragnition.retrieval.bm25 import BM25Index
from ragnition.retrieval.dense import build_collection, point_id
from ragnition.retrieval.retriever import Retriever, rank_docs
from ragnition.retrieval.tokenize import tokenize


def test_tokenizer_keeps_identifiers_and_their_parts():
    toks = tokenize("PgBouncer default_pool_size on payments‑api (CHG-2318)")
    for t in ["pgbouncer", "default_pool_size", "default", "pool", "size", "payments-api", "payments", "api", "chg-2318"]:
        assert t in toks
    assert "on" not in toks


def test_bm25_prefers_rare_exact_terms():
    ids = ["a", "b", "c"]
    texts = ["service restarted service service", "cl_waiting climbed at pgbouncer", "service is healthy"]
    idx = BM25Index(ids, texts)
    assert idx.search("cl_waiting service", k=3)[0][0] == "b"  # rare term beats a frequent one
    assert idx.search("nothing matches", k=3) == []
    assert idx.search("service", k=3, allowed={2})[0][0] == "c"


def test_metrics():
    ranked = ["x", "a", "y", "b"]
    assert recall_at_k(ranked, {"a", "b"}, 2) == 0.5
    assert recall_at_k(ranked, {"a", "b"}, 4) == 1.0
    assert reciprocal_rank(ranked, {"b"}) == 0.25
    assert reciprocal_rank(ranked, {"z"}) == 0.0
    with pytest.raises(ValueError):
        recall_at_k(ranked, set(), 5)


def _chunk(cid, doc, text, doc_type="change", tags=("svc",), role="chunk", parent=None):
    return Chunk(chunk_id=cid, doc_id=doc, strategy="structure", role=role, parent_id=parent, position=0,
                 header="H", body=text, text=text, n_tokens=len(text.split()), section_path=[], title="T",
                 source_id="s", doc_type=doc_type, tags=list(tags), url=None, license="x",
                 redistributable=True, synthetic=True)


class FakeGateway:
    """Embeds by keyword presence, so similarity is predictable."""

    VOCAB = ["pool", "dns", "disk"]

    async def embed(self, texts, input_type="document", use_cache=True):
        vecs = [[1.0 if w in t.lower() else 0.01 for w in self.VOCAB] for t in texts]
        return SimpleNamespace(vectors=vecs)


async def test_dense_index_search_filter_and_parents():
    chunks = [
        _chunk("p1", "doc/pool", "parent text about pool", role="parent"),
        _chunk("c1", "doc/pool", "pool exhausted", parent="p1"),
        _chunk("c2", "doc/dns", "dns timeouts", doc_type="postmortem", tags=("cluster-dns",)),
        _chunk("c3", "doc/disk", "disk full"),
    ]
    client = QdrantClient(":memory:")
    gw = FakeGateway()
    n = await build_collection(client, gw, "chunks_structure", chunks, dimensions=3)
    assert n == 3  # the parent is not embedded

    r = Retriever("structure", chunks, client, gw)
    hits = await r.dense("why is the pool empty", k=2)
    assert hits[0].chunk.chunk_id == "c1" and hits[0].rank == 1
    assert r.parent(hits[0].chunk).chunk_id == "p1"
    only_pm = await r.dense("pool", k=3, doc_type="postmortem")
    assert [h.chunk.doc_id for h in only_pm] == ["doc/dns"]
    tagged = r.sparse("dns timeouts", k=3, tag="cluster-dns")
    assert [h.chunk.chunk_id for h in tagged] == ["c2"]

    # re-building replaces the collection instead of duplicating points
    await build_collection(client, gw, "chunks_structure", chunks, dimensions=3)
    assert client.count("chunks_structure").count == 3
    client.close()


def test_rank_docs_collapses_chunks_and_point_ids_are_stable():
    hits = [SimpleNamespace(chunk=SimpleNamespace(doc_id=d)) for d in ["a", "a", "b", "a", "c"]]
    assert rank_docs(hits) == ["a", "b", "c"]
    assert point_id("abc") == point_id("abc") != point_id("abd")
