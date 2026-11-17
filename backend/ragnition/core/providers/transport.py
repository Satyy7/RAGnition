"""Shared HTTP plumbing: one POST helper that turns every failure into a ProviderError.

All our providers (Groq, Gemini-compat, OpenRouter, Jina, Cohere v2) accept a
Bearer token and JSON, so one helper covers them all.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ragnition.core.providers.fallback import ModelSlot
from ragnition.core.providers.ratelimit import parse_retry_after
from ragnition.core.providers.types import ProviderError, RateLimitedError


def raise_for_provider_status(resp: httpx.Response) -> None:
    if resp.is_success:
        return
    status = resp.status_code
    detail = f"HTTP {status}: {resp.text[:300]}"
    if status == 429:
        raise RateLimitedError(detail, retry_after=parse_retry_after(resp.headers))
    if status in (401, 403):
        raise ProviderError(detail, kind="auth", retryable=False, status=status)
    if status == 408 or status >= 500:
        raise ProviderError(detail, kind="server", retryable=True, status=status)
    raise ProviderError(detail, kind="bad_request", retryable=False, status=status)


async def post_json(
    http: httpx.AsyncClient, slot: ModelSlot, path: str, body: dict[str, Any]
) -> tuple[dict[str, Any], float]:
    """POST `body` to the slot's provider. Returns (parsed JSON, latency in ms)."""
    url = f"{slot.provider.base_url.rstrip('/')}/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {slot.api_key}"}
    start = time.perf_counter()
    try:
        resp = await http.post(url, headers=headers, json=body, timeout=slot.spec.timeout_s)
    except httpx.TimeoutException as exc:
        raise ProviderError(f"timeout after {slot.spec.timeout_s}s", kind="timeout", retryable=True) from exc
    except httpx.TransportError as exc:
        raise ProviderError(f"network error: {exc}", kind="network", retryable=True) from exc
    latency_ms = (time.perf_counter() - start) * 1000

    raise_for_provider_status(resp)
    try:
        data = resp.json()
    except ValueError as exc:
        raise ProviderError("response body is not JSON", kind="bad_response", retryable=False) from exc
    if not isinstance(data, dict):
        raise ProviderError("response JSON is not an object", kind="bad_response", retryable=False)
    return data, latency_ms
