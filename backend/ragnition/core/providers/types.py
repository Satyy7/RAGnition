"""Typed contracts for the provider layer: requests, responses and errors."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- errors

ErrorKind = Literal[
    "rate_limited",  # 429: slow down / switch model
    "timeout",  # no answer in time: retry
    "network",  # connection failed: retry
    "server",  # 5xx / 408: provider-side, retry
    "auth",  # 401/403: bad or missing key, do not retry
    "bad_request",  # 4xx: our request is wrong for this model, do not retry
    "bad_response",  # 200 but unusable body
]


class ProviderError(Exception):
    def __init__(
        self,
        message: str,
        *,
        kind: ErrorKind,
        retryable: bool,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.status = status
        self.retry_after = retry_after


class RateLimitedError(ProviderError):
    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message, kind="rate_limited", retryable=True, status=429, retry_after=retry_after)


AttemptOutcome = Literal["ok", "no_api_key", "circuit_open"] | ErrorKind


class AttemptRecord(BaseModel):
    """One try against one model. The list of these is the call's audit trail."""

    slot: str  # "provider:model"
    outcome: AttemptOutcome
    detail: str = ""
    latency_ms: float = 0.0


class AllProvidersExhausted(Exception):
    def __init__(self, role: str, attempts: list[AttemptRecord], retry_in_s: float | None) -> None:
        summary = "; ".join(f"{a.slot}={a.outcome}" for a in attempts) or "no models available"
        hint = f" Earliest retry in ~{retry_in_s:.0f}s." if retry_in_s else ""
        super().__init__(f"All '{role}' models failed: {summary}.{hint}")
        self.role = role
        self.attempts = attempts
        self.retry_in_s = retry_in_s


# ---------------------------------------------------------------- responses


class ModelResponse(BaseModel):
    """Fields every response carries so callers always know WHO answered."""

    provider: str
    model: str
    latency_ms: float = 0.0
    cached: bool = False
    attempts: list[AttemptRecord] = Field(default_factory=list)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_tokens: int = Field(default=1024, ge=1)
    json_mode: bool = False


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0


class ChatResponse(ModelResponse):
    text: str
    finish_reason: str | None = None
    usage: Usage = Field(default_factory=Usage)


class EmbeddingResponse(ModelResponse):
    vectors: list[list[float]]
    dimensions: int


class RerankResult(BaseModel):
    index: int  # position in the documents list that was sent
    score: float


class RerankResponse(ModelResponse):
    results: list[RerankResult]  # sorted, best first
    degraded: bool = False  # True => no reranker answered; order is the input order
