"""HTTP clients against mocked endpoints (respx intercepts httpx; nothing leaves the machine)."""

import httpx
import pytest
import respx

from ragnition.core.config import ModelSpec, ProviderSpec
from ragnition.core.providers.chat import chat_completion
from ragnition.core.providers.embeddings import embed_texts
from ragnition.core.providers.fallback import CircuitBreaker, ModelSlot
from ragnition.core.providers.rerank import rerank_documents
from ragnition.core.providers.types import ChatMessage, ChatRequest, ProviderError, RateLimitedError

BASE = "https://api.test/v1"


def slot(kind="openai_compat", dimensions=None) -> ModelSlot:
    return ModelSlot(
        spec=ModelSpec(provider="test", model="m", dimensions=dimensions),
        provider=ProviderSpec(kind=kind, base_url=BASE, api_key_setting="groq_api_key"),
        api_key="secret",
        rpm_bucket=None,
        tpm_bucket=None,
        breaker=CircuitBreaker(3, 60),
    )


REQUEST = ChatRequest(messages=[ChatMessage(role="user", content="hi")])


async def test_chat_parses_response_and_sends_bearer_token():
    with respx.mock:
        route = respx.post(f"{BASE}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 1},
                },
            )
        )
        async with httpx.AsyncClient() as http:
            resp = await chat_completion(http, slot(), REQUEST)
    assert resp.text == "OK"
    assert resp.usage.prompt_tokens == 5
    assert route.calls.last.request.headers["Authorization"] == "Bearer secret"


async def test_429_becomes_rate_limited_with_retry_after():
    with respx.mock:
        respx.post(f"{BASE}/chat/completions").mock(
            return_value=httpx.Response(429, headers={"retry-after": "7"}, json={"error": "rate"})
        )
        async with httpx.AsyncClient() as http:
            with pytest.raises(RateLimitedError) as info:
                await chat_completion(http, slot(), REQUEST)
    assert info.value.retry_after == 7.0


@pytest.mark.parametrize(("status", "kind", "retryable"), [(401, "auth", False), (400, "bad_request", False), (503, "server", True)])
async def test_status_codes_are_classified(status, kind, retryable):
    with respx.mock:
        respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(status, text="nope"))
        async with httpx.AsyncClient() as http:
            with pytest.raises(ProviderError) as info:
                await chat_completion(http, slot(), REQUEST)
    assert (info.value.kind, info.value.retryable) == (kind, retryable)


async def test_timeout_is_retryable():
    with respx.mock:
        respx.post(f"{BASE}/chat/completions").mock(side_effect=httpx.ReadTimeout("slow"))
        async with httpx.AsyncClient() as http:
            with pytest.raises(ProviderError) as info:
                await chat_completion(http, slot(), REQUEST)
    assert info.value.kind == "timeout" and info.value.retryable


async def test_embeddings_reorder_by_index_and_check_dimensions():
    with respx.mock:
        respx.post(f"{BASE}/embeddings").mock(
            return_value=httpx.Response(
                200, json={"data": [{"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [1.0, 0.0]}]}
            )
        )
        async with httpx.AsyncClient() as http:
            resp = await embed_texts(http, slot(dimensions=2), ["first", "second"], "document")
    assert resp.vectors == [[1.0, 0.0], [0.0, 1.0]]
    assert resp.dimensions == 2


async def test_embeddings_without_index_keep_response_order():
    with respx.mock:
        respx.post(f"{BASE}/embeddings").mock(
            return_value=httpx.Response(200, json={"data": [{"embedding": [1.0, 0.0]}, {"embedding": [0.0, 1.0]}]})
        )
        async with httpx.AsyncClient() as http:
            resp = await embed_texts(http, slot(), ["first", "second"], "document")
    assert resp.vectors == [[1.0, 0.0], [0.0, 1.0]]


async def test_embedding_count_mismatch_is_rejected():
    with respx.mock:
        respx.post(f"{BASE}/embeddings").mock(
            return_value=httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})
        )
        async with httpx.AsyncClient() as http:
            with pytest.raises(ProviderError, match="got 1 vectors"):
                await embed_texts(http, slot(), ["a", "b"], "document")


async def test_jina_embedding_sends_query_task():
    with respx.mock:
        route = respx.post(f"{BASE}/embeddings").mock(
            return_value=httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})
        )
        async with httpx.AsyncClient() as http:
            await embed_texts(http, slot(kind="jina"), ["q"], "query")
    assert b'"retrieval.query"' in route.calls.last.request.content


async def test_rerank_sorts_best_first():
    with respx.mock:
        respx.post(f"{BASE}/rerank").mock(
            return_value=httpx.Response(
                200, json={"results": [{"index": 0, "relevance_score": 0.1}, {"index": 1, "relevance_score": 0.9}]}
            )
        )
        async with httpx.AsyncClient() as http:
            resp = await rerank_documents(http, slot(kind="jina"), "q", ["a", "b"], None)
    assert [r.index for r in resp.results] == [1, 0]


async def test_rerank_rejects_out_of_range_index():
    with respx.mock:
        respx.post(f"{BASE}/rerank").mock(
            return_value=httpx.Response(200, json={"results": [{"index": 5, "relevance_score": 0.5}]})
        )
        async with httpx.AsyncClient() as http:
            with pytest.raises(ProviderError, match="out-of-range"):
                await rerank_documents(http, slot(kind="cohere"), "q", ["a"], None)
