"""Rerank calls: local cross-encoders, or Jina / Cohere v2 (same /rerank shape)."""

from __future__ import annotations

import time

import httpx

from ragnition.core.providers.fallback import ModelSlot
from ragnition.core.providers.local import LocalModels
from ragnition.core.providers.transport import post_json
from ragnition.core.providers.types import ProviderError, RerankResponse, RerankResult


async def rerank_documents(
    http: httpx.AsyncClient,
    slot: ModelSlot,
    query: str,
    documents: list[str],
    top_n: int | None,
    local: LocalModels | None = None,
) -> RerankResponse:
    if slot.provider.kind == "local":
        if local is None:
            raise ProviderError("no local model runtime configured", kind="bad_request", retryable=False)
        start = time.perf_counter()
        scores = await local.rerank(slot.spec.model, query, documents)
        latency_ms = (time.perf_counter() - start) * 1000
        if len(scores) != len(documents):
            raise ProviderError(
                f"sent {len(documents)} documents, got {len(scores)} scores", kind="bad_response", retryable=False
            )
        results = [RerankResult(index=i, score=s) for i, s in enumerate(scores)]
    elif slot.provider.kind in ("jina", "cohere"):
        body: dict[str, object] = {"model": slot.spec.model, "query": query, "documents": documents}
        if top_n is not None:
            body["top_n"] = top_n
        if slot.provider.kind == "jina":
            body["return_documents"] = False  # we already have the text; save bandwidth
        data, latency_ms = await post_json(http, slot, "rerank", body)
        try:
            results = [
                RerankResult(index=int(r["index"]), score=float(r["relevance_score"])) for r in data["results"]
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError("unexpected rerank response shape", kind="bad_response", retryable=False) from exc
    else:
        raise ProviderError(f"rerank not supported for {slot.id}", kind="bad_request", retryable=False)

    if any(not 0 <= r.index < len(documents) for r in results):
        raise ProviderError("rerank returned an out-of-range index", kind="bad_response", retryable=False)
    results.sort(key=lambda r: r.score, reverse=True)
    if top_n is not None:
        results = results[:top_n]
    return RerankResponse(results=results, provider=slot.spec.provider, model=slot.spec.model, latency_ms=latency_ms)
