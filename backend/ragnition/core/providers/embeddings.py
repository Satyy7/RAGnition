"""Embedding calls (OpenAI-compatible /embeddings, plus Jina's task-aware variant)."""

from __future__ import annotations

from typing import Literal

import httpx

from ragnition.core.providers.fallback import ModelSlot
from ragnition.core.providers.transport import post_json
from ragnition.core.providers.types import EmbeddingResponse, ProviderError

InputType = Literal["query", "document"]


async def embed_texts(
    http: httpx.AsyncClient, slot: ModelSlot, texts: list[str], input_type: InputType
) -> EmbeddingResponse:
    body: dict[str, object] = {"model": slot.spec.model, "input": texts}
    if slot.spec.dimensions:
        body["dimensions"] = slot.spec.dimensions
    if slot.provider.kind == "jina":
        # Asymmetric retrieval: short questions and long passages are embedded
        # slightly differently so they meet in the same space.
        body["task"] = "retrieval.query" if input_type == "query" else "retrieval.passage"
    elif slot.provider.kind != "openai_compat":
        raise ProviderError(f"embeddings not supported for {slot.id}", kind="bad_request", retryable=False)

    data, latency_ms = await post_json(http, slot, "embeddings", body)
    try:
        items = sorted(data["data"], key=lambda item: item["index"])
        vectors = [item["embedding"] for item in items]
    except (KeyError, TypeError) as exc:
        raise ProviderError("unexpected embedding response shape", kind="bad_response", retryable=False) from exc

    if len(vectors) != len(texts):
        raise ProviderError(
            f"sent {len(texts)} texts, got {len(vectors)} vectors", kind="bad_response", retryable=False
        )
    dims = len(vectors[0]) if vectors else 0
    if any(len(v) != dims for v in vectors):
        raise ProviderError("inconsistent vector dimensions", kind="bad_response", retryable=False)
    if slot.spec.dimensions and dims != slot.spec.dimensions:
        raise ProviderError(
            f"asked for {slot.spec.dimensions} dims, got {dims}", kind="bad_response", retryable=False
        )

    return EmbeddingResponse(
        vectors=vectors, dimensions=dims, provider=slot.spec.provider, model=slot.spec.model, latency_ms=latency_ms
    )
