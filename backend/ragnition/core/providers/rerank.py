"""Rerank calls. Jina and Cohere v2 share the same /rerank request/response shape."""

from __future__ import annotations

import httpx

from ragnition.core.providers.fallback import ModelSlot
from ragnition.core.providers.transport import post_json
from ragnition.core.providers.types import ProviderError, RerankResponse, RerankResult


async def rerank_documents(
    http: httpx.AsyncClient, slot: ModelSlot, query: str, documents: list[str], top_n: int | None
) -> RerankResponse:
    if slot.provider.kind not in ("jina", "cohere"):
        raise ProviderError(f"rerank not supported for {slot.id}", kind="bad_request", retryable=False)

    body: dict[str, object] = {"model": slot.spec.model, "query": query, "documents": documents}
    if top_n is not None:
        body["top_n"] = top_n
    if slot.provider.kind == "jina":
        body["return_documents"] = False  # we already have the text; save bandwidth

    data, latency_ms = await post_json(http, slot, "rerank", body)
    try:
        results = [RerankResult(index=int(r["index"]), score=float(r["relevance_score"])) for r in data["results"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderError("unexpected rerank response shape", kind="bad_response", retryable=False) from exc
    if any(not 0 <= r.index < len(documents) for r in results):
        raise ProviderError("rerank returned an out-of-range index", kind="bad_response", retryable=False)

    results.sort(key=lambda r: r.score, reverse=True)
    return RerankResponse(results=results, provider=slot.spec.provider, model=slot.spec.model, latency_ms=latency_ms)
