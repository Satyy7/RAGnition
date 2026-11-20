"""Embedding calls: local fastembed models, OpenAI-compatible APIs, or Jina."""

from __future__ import annotations

import time
from typing import Literal

import httpx

from ragnition.core.providers.fallback import ModelSlot
from ragnition.core.providers.local import LocalModels
from ragnition.core.providers.transport import post_json
from ragnition.core.providers.types import EmbeddingResponse, ProviderError

InputType = Literal["query", "document"]


async def embed_texts(
    http: httpx.AsyncClient,
    slot: ModelSlot,
    texts: list[str],
    input_type: InputType,
    local: LocalModels | None = None,
) -> EmbeddingResponse:
    if slot.provider.kind == "local":
        if local is None:
            raise ProviderError("no local model runtime configured", kind="bad_request", retryable=False)
        start = time.perf_counter()
        vectors = await local.embed(slot.spec.model, texts, input_type, slot.spec.batch_size)
        return _validated(slot, texts, vectors, (time.perf_counter() - start) * 1000)

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
        items = data["data"]
        # OpenAI's spec includes "index"; some compatible APIs omit it and rely on
        # response order (which matches input order). Sort only when present.
        if all("index" in item for item in items):
            items = sorted(items, key=lambda item: item["index"])
        vectors = [item["embedding"] for item in items]
    except (KeyError, TypeError) as exc:
        preview = str(data)[:200]
        raise ProviderError(
            f"unexpected embedding response shape: {preview}", kind="bad_response", retryable=False
        ) from exc
    return _validated(slot, texts, vectors, latency_ms)


def _validated(slot: ModelSlot, texts: list[str], vectors: list[list[float]], latency_ms: float) -> EmbeddingResponse:
    """Same checks for every backend: one vector per text, consistent and expected size."""
    if len(vectors) != len(texts):
        raise ProviderError(
            f"sent {len(texts)} texts, got {len(vectors)} vectors", kind="bad_response", retryable=False
        )
    dims = len(vectors[0]) if vectors else 0
    if any(len(v) != dims for v in vectors):
        raise ProviderError("inconsistent vector dimensions", kind="bad_response", retryable=False)
    if vectors and slot.spec.dimensions and dims != slot.spec.dimensions:
        raise ProviderError(
            f"asked for {slot.spec.dimensions} dims, got {dims}", kind="bad_response", retryable=False
        )
    return EmbeddingResponse(
        vectors=vectors, dimensions=dims, provider=slot.spec.provider, model=slot.spec.model, latency_ms=latency_ms
    )
