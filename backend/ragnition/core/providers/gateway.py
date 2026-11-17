"""ModelGateway: the single entry point the rest of RAGnition uses for model calls.

    async with ModelGateway.from_settings() as gw:
        answer = await gw.chat(ChatRequest(messages=[...]))
        vecs   = await gw.embed(["text"], input_type="document")
        ranked = await gw.rerank("query", ["doc a", "doc b"])
"""

from __future__ import annotations

import logging
from typing import Self

import httpx

from ragnition.core.config import ModelSpec, ProvidersConfig, Role, Settings, get_settings, load_providers_config
from ragnition.core.providers.cache import CallCache
from ragnition.core.providers.chat import chat_completion
from ragnition.core.providers.embeddings import InputType, embed_texts
from ragnition.core.providers.fallback import CircuitBreaker, FallbackExecutor, ModelSlot
from ragnition.core.providers.ratelimit import TokenBucket
from ragnition.core.providers.rerank import rerank_documents
from ragnition.core.providers.types import (
    AllProvidersExhausted,
    ChatRequest,
    ChatResponse,
    EmbeddingResponse,
    RerankResponse,
    RerankResult,
)

logger = logging.getLogger(__name__)


def estimate_tokens(request: ChatRequest) -> int:
    """Rough token estimate (~4 chars/token) for client-side TPM limiting.

    Includes max_tokens because providers count the completion against TPM too.
    """
    return sum(len(m.content) for m in request.messages) // 4 + request.max_tokens


class ModelGateway:
    def __init__(
        self,
        settings: Settings,
        config: ProvidersConfig,
        cache: CallCache | None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.config = config
        self.cache = cache
        self.http = http or httpx.AsyncClient()
        # Limits are per model (per org), so if two roles use the same model they
        # must share ONE limiter and breaker: key everything by slot id.
        self._shared: dict[str, tuple[TokenBucket | None, TokenBucket | None, CircuitBreaker]] = {}
        self.executors: dict[str, FallbackExecutor] = {
            role: FallbackExecutor(role, [self._make_slot(spec) for spec in chain], config.resilience, cache)
            for role, chain in config.chains.items()
        }

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> Self:
        settings = settings or get_settings()
        return cls(settings, load_providers_config(settings.providers_config_path), CallCache(settings.cache_path))

    def _make_slot(self, spec: ModelSpec) -> ModelSlot:
        provider = self.config.providers[spec.provider]
        if spec.slot_id not in self._shared:
            res = self.config.resilience
            self._shared[spec.slot_id] = (
                TokenBucket(spec.rpm) if spec.rpm else None,
                TokenBucket(spec.tpm) if spec.tpm else None,
                CircuitBreaker(res.breaker_failure_threshold, res.breaker_cooldown_s),
            )
        rpm, tpm, breaker = self._shared[spec.slot_id]
        return ModelSlot(
            spec=spec,
            provider=provider,
            api_key=self.settings.secret(provider.api_key_setting),
            rpm_bucket=rpm,
            tpm_bucket=tpm,
            breaker=breaker,
        )

    def executor(self, role: Role) -> FallbackExecutor:
        if role not in self.executors:
            raise ValueError(f"no chain configured for role '{role}'")
        return self.executors[role]

    async def chat(
        self, request: ChatRequest, *, role: Role = "generator", use_cache: bool = True, pin: str | None = None
    ) -> ChatResponse:
        async def call(slot: ModelSlot) -> ChatResponse:
            return await chat_completion(self.http, slot, request)

        return await self.executor(role).run(
            payload=request.model_dump(),
            call=call,
            response_type=ChatResponse,
            est_tokens=estimate_tokens(request),
            use_cache=use_cache,
            pin=pin,
        )

    async def embed(
        self, texts: list[str], *, input_type: InputType = "document", use_cache: bool = True
    ) -> EmbeddingResponse:
        executor = self.executor("embedding")
        spec = executor.slots[0].spec
        vectors: list[list[float]] = []
        last: EmbeddingResponse | None = None
        for start in range(0, len(texts), spec.batch_size):
            batch = texts[start : start + spec.batch_size]

            async def call(slot: ModelSlot, batch: list[str] = batch) -> EmbeddingResponse:
                return await embed_texts(self.http, slot, batch, input_type)

            last = await executor.run(
                payload={"texts": batch, "input_type": input_type, "dimensions": spec.dimensions},
                call=call,
                response_type=EmbeddingResponse,
                use_cache=use_cache,
            )
            vectors.extend(last.vectors)
        if last is None:
            return EmbeddingResponse(vectors=[], dimensions=spec.dimensions or 0, provider=spec.provider, model=spec.model)
        return EmbeddingResponse(
            vectors=vectors, dimensions=last.dimensions, provider=last.provider, model=last.model
        )

    async def rerank(
        self, query: str, documents: list[str], *, top_n: int | None = None, use_cache: bool = True
    ) -> RerankResponse:
        n = len(documents) if top_n is None else min(top_n, len(documents))
        if not documents:
            return RerankResponse(results=[], provider="none", model="none")

        async def call(slot: ModelSlot) -> RerankResponse:
            return await rerank_documents(self.http, slot, query, documents, top_n)

        try:
            return await self.executor("rerank").run(
                payload={"query": query, "documents": documents, "top_n": top_n},
                call=call,
                response_type=RerankResponse,
                use_cache=use_cache,
            )
        except AllProvidersExhausted as exc:
            # Graceful degradation: the caller keeps its fused (pre-rerank) order and
            # the trace says so. Better a slightly worse answer than no answer.
            logger.warning("rerank degraded to passthrough", extra={"reason": str(exc)})
            return RerankResponse(
                results=[RerankResult(index=i, score=0.0) for i in range(n)],
                provider="none",
                model="passthrough",
                degraded=True,
                attempts=exc.attempts,
            )

    async def aclose(self) -> None:
        await self.http.aclose()
        if self.cache is not None:
            self.cache.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
