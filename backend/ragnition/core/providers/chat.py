"""Chat completions over the OpenAI-compatible protocol (Groq, Gemini, OpenRouter)."""

from __future__ import annotations

import httpx

from ragnition.core.providers.fallback import ModelSlot
from ragnition.core.providers.transport import post_json
from ragnition.core.providers.types import ChatRequest, ChatResponse, ProviderError, Usage


async def chat_completion(http: httpx.AsyncClient, slot: ModelSlot, request: ChatRequest) -> ChatResponse:
    if slot.provider.kind != "openai_compat":
        raise ProviderError(f"{slot.id} does not speak the chat protocol", kind="bad_request", retryable=False)

    body: dict[str, object] = {
        "model": slot.spec.model,
        "messages": [m.model_dump() for m in request.messages],
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
    }
    if request.json_mode:
        body["response_format"] = {"type": "json_object"}

    data, latency_ms = await post_json(http, slot, "chat/completions", body)
    try:
        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
        usage = data.get("usage") or {}
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ProviderError("unexpected chat response shape", kind="bad_response", retryable=False) from exc
    if not text.strip():
        # Seen with reasoning models when max_tokens is spent on hidden reasoning.
        raise ProviderError(
            f"empty completion (finish_reason={choice.get('finish_reason')})", kind="bad_response", retryable=True
        )

    return ChatResponse(
        text=text,
        provider=slot.spec.provider,
        model=slot.spec.model,
        latency_ms=latency_ms,
        finish_reason=choice.get("finish_reason"),
        usage=Usage(
            prompt_tokens=usage.get("prompt_tokens") or 0,
            completion_tokens=usage.get("completion_tokens") or 0,
        ),
    )
