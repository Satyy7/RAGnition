"""Local model runtime with FAKE models: no download, no fastembed calls."""

import pytest

from ragnition.core.config import ModelSpec, ProviderSpec, ResilienceSpec
from ragnition.core.providers.cache import CallCache
from ragnition.core.providers.embeddings import embed_texts
from ragnition.core.providers.fallback import CircuitBreaker, FallbackExecutor, ModelSlot
from ragnition.core.providers.local import LocalModels
from ragnition.core.providers.rerank import rerank_documents
from ragnition.core.providers.types import EmbeddingResponse, ProviderError


class FakeEmbedder:
    def __init__(self):
        self.calls: list[str] = []

    def query_embed(self, texts):
        self.calls.append("query")
        return iter([[1.0, 0.0] for _ in texts])

    def passage_embed(self, texts, batch_size=256):
        self.calls.append("passage")
        return iter([[0.0, 1.0] for _ in texts])


class FakeCrossEncoder:
    def rerank(self, query, documents):
        return [float(len(d)) for d in documents]  # longer document = "more relevant"


def make_local(tmp_path, embedder=None, reranker=None, loads=None):
    loads = loads if loads is not None else []

    def load_embedding(name, cache_dir):
        loads.append(name)
        return embedder

    def load_rerank(name, cache_dir):
        loads.append(name)
        return reranker

    return LocalModels(tmp_path, loaders={"embedding": load_embedding, "rerank": load_rerank})


def local_slot(model="m", dimensions=None) -> ModelSlot:
    return ModelSlot(
        spec=ModelSpec(provider="local", model=model, dimensions=dimensions, cache=False),
        provider=ProviderSpec(kind="local"),
        api_key=None,
        rpm_bucket=None,
        tpm_bucket=None,
        breaker=CircuitBreaker(3, 60),
    )


async def test_queries_and_passages_use_different_entry_points(tmp_path):
    embedder = FakeEmbedder()
    local = make_local(tmp_path, embedder=embedder)
    q = await embed_texts(None, local_slot(dimensions=2), ["why timeout?"], "query", local)
    d = await embed_texts(None, local_slot(dimensions=2), ["doc a", "doc b"], "document", local)
    assert embedder.calls == ["query", "passage"]
    assert q.vectors == [[1.0, 0.0]]
    assert d.vectors == [[0.0, 1.0], [0.0, 1.0]]


async def test_model_is_loaded_once(tmp_path):
    loads: list[str] = []
    local = make_local(tmp_path, embedder=FakeEmbedder(), loads=loads)
    for _ in range(3):
        await embed_texts(None, local_slot(), ["x"], "document", local)
    assert loads == ["m"]


async def test_dimension_mismatch_is_rejected(tmp_path):
    local = make_local(tmp_path, embedder=FakeEmbedder())
    with pytest.raises(ProviderError, match="asked for 768 dims, got 2"):
        await embed_texts(None, local_slot(dimensions=768), ["x"], "document", local)


async def test_local_rerank_sorts_and_applies_top_n(tmp_path):
    local = make_local(tmp_path, reranker=FakeCrossEncoder())
    resp = await rerank_documents(None, local_slot(), "q", ["a", "ccc", "bb"], top_n=2, local=local)
    assert [r.index for r in resp.results] == [1, 2]


async def test_library_errors_become_provider_errors(tmp_path):
    def broken_loader(name, cache_dir):
        raise OSError("download failed")

    local = LocalModels(tmp_path, loaders={"embedding": broken_loader, "rerank": broken_loader})
    with pytest.raises(ProviderError, match="OSError: download failed") as info:
        await embed_texts(None, local_slot(), ["x"], "document", local)
    assert info.value.kind == "bad_response"


async def test_keyless_local_slot_is_not_skipped_and_not_cached(tmp_path):
    embedder = FakeEmbedder()
    local = make_local(tmp_path, embedder=embedder)
    slot = local_slot()
    executor = FallbackExecutor("embedding", [slot], ResilienceSpec(), CallCache(":memory:"))

    async def call(s):
        return await embed_texts(None, s, ["x"], "document", local)

    for _ in range(2):
        resp = await executor.run(payload={"t": ["x"]}, call=call, response_type=EmbeddingResponse)
        assert not resp.cached
    assert embedder.calls == ["passage", "passage"]  # cache: false => computed both times


async def test_gateway_batching_keeps_latency_and_attempt_trail(tmp_path):
    from ragnition.core.config import ProvidersConfig, Settings
    from ragnition.core.providers.gateway import ModelGateway

    config = ProvidersConfig.model_validate(
        {
            "providers": {"local": {"kind": "local"}},
            "chains": {"embedding": [{"provider": "local", "model": "m", "batch_size": 2, "cache": False}]},
        }
    )
    gw = ModelGateway(Settings(_env_file=None, model_cache_dir=tmp_path), config, CallCache(":memory:"))
    gw.local = make_local(tmp_path, embedder=FakeEmbedder())
    async with gw:
        resp = await gw.embed(["a", "b", "c"], input_type="document")
    assert len(resp.vectors) == 3
    assert len(resp.attempts) == 2  # 3 texts / batch_size 2 = 2 batches, one attempt each
    assert all(a.outcome == "ok" for a in resp.attempts)
    assert resp.cached is False
