"""Fallback chains with retries, backoff and per-model circuit breakers.

For one role (e.g. "generator") we hold an ordered list of ModelSlots. A call:
  1. returns a cached answer if ANY slot in the chain already answered this request;
  2. otherwise tries slots in order, skipping ones with no key or an open breaker;
  3. on each slot: rate-limit ourselves, call, and on failure either retry the same
     slot (transient error / short 429) or move to the next slot;
  4. raises AllProvidersExhausted with the full attempt trail if nothing worked.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from ragnition.core.config import ModelSpec, ProviderSpec, ResilienceSpec
from ragnition.core.providers.cache import CallCache, make_cache_key
from ragnition.core.providers.ratelimit import TokenBucket
from ragnition.core.providers.types import (
    AllProvidersExhausted,
    AttemptRecord,
    ModelResponse,
    ProviderError,
    RateLimitedError,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=ModelResponse)

AUTH_FAILURE_COOLDOWN_S = 3600.0  # a rejected key will not fix itself in a minute


class CircuitBreaker:
    """closed -> (failures) -> open -> (cooldown passes) -> half_open -> closed|open.

    While open, the model is skipped instantly instead of wasting a request (and
    our quota) on something we already know is failing.
    """

    def __init__(self, failure_threshold: int, cooldown_s: float, clock: Callable[[], float] = time.monotonic):
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self._clock = clock
        self.state: Literal["closed", "open", "half_open"] = "closed"
        self.failures = 0
        self.open_until = 0.0

    def allow(self) -> bool:
        if self.state == "open":
            if self._clock() >= self.open_until:
                self.state = "half_open"  # let one trial request through
                return True
            return False
        return True

    def record_success(self) -> None:
        self.state = "closed"
        self.failures = 0

    def record_failure(self) -> None:
        self.failures += 1
        if self.state == "half_open" or self.failures >= self.failure_threshold:
            self.trip()

    def trip(self, cooldown_s: float | None = None) -> None:
        self.state = "open"
        self.failures = 0
        self.open_until = self._clock() + (cooldown_s if cooldown_s is not None else self.cooldown_s)

    def retry_in(self) -> float:
        return max(0.0, self.open_until - self._clock()) if self.state == "open" else 0.0


@dataclass
class ModelSlot:
    """One model in a chain, with its own limiter and breaker."""

    spec: ModelSpec
    provider: ProviderSpec
    api_key: str | None
    rpm_bucket: TokenBucket | None
    tpm_bucket: TokenBucket | None
    breaker: CircuitBreaker

    @property
    def id(self) -> str:
        return self.spec.slot_id


class FallbackExecutor:
    def __init__(
        self,
        role: str,
        slots: list[ModelSlot],
        resilience: ResilienceSpec,
        cache: CallCache | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        if not slots:
            raise ValueError(f"role '{role}' has no model slots")
        self.role = role
        self.slots = slots
        self.res = resilience
        self.cache = cache
        self._sleep = sleep
        self._rng = rng

    def _select(self, pin: str | None) -> list[ModelSlot]:
        if pin is None:
            return self.slots
        chosen = [s for s in self.slots if s.id == pin]
        if not chosen:
            raise ValueError(f"pin '{pin}' is not in the '{self.role}' chain: {[s.id for s in self.slots]}")
        return chosen

    def _key(self, slot: ModelSlot, payload: dict[str, Any]) -> str:
        return make_cache_key(self.role, slot.spec.provider, slot.spec.model, payload)

    def _backoff(self, attempt: int) -> float:
        # "Full jitter": random point in [0, base*2^attempt]. Spreads out retries so
        # many clients do not hammer the provider in sync (thundering herd).
        return min(self.res.backoff_max_s, self.res.backoff_base_s * (2**attempt)) * self._rng()

    async def run(
        self,
        *,
        payload: dict[str, Any],
        call: Callable[[ModelSlot], Awaitable[T]],
        response_type: type[T],
        est_tokens: int = 0,
        use_cache: bool = True,
        pin: str | None = None,
    ) -> T:
        slots = self._select(pin)

        if use_cache and self.cache is not None:
            for slot in slots:
                if not slot.spec.cache:
                    continue
                hit = self.cache.get(self._key(slot, payload))
                if hit is not None:
                    response = response_type.model_validate_json(hit)
                    response.cached = True
                    logger.info("cache hit", extra={"role": self.role, "slot": slot.id})
                    return response

        attempts: list[AttemptRecord] = []
        for slot in slots:
            if slot.provider.api_key_setting is not None and slot.api_key is None:
                attempts.append(AttemptRecord(slot=slot.id, outcome="no_api_key"))
                continue
            if not slot.breaker.allow():
                attempts.append(
                    AttemptRecord(slot=slot.id, outcome="circuit_open", detail=f"retry in {slot.breaker.retry_in():.0f}s")
                )
                continue
            result = await self._try_slot(slot, call, est_tokens, attempts)
            if result is not None:
                result.attempts = attempts
                if use_cache and self.cache is not None and slot.spec.cache:
                    self.cache.put(
                        self._key(slot, payload),
                        namespace=self.role,
                        provider=slot.spec.provider,
                        model=slot.spec.model,
                        response_json=result.model_dump_json(exclude={"attempts", "cached"}),
                    )
                if len(attempts) > 1:
                    logger.warning(
                        "answered after fallback",
                        extra={"role": self.role, "slot": slot.id, "trail": [a.model_dump() for a in attempts]},
                    )
                return result

        waits = [s.breaker.retry_in() for s in slots if s.breaker.state == "open"]
        raise AllProvidersExhausted(self.role, attempts, min(waits) if waits else None)

    async def _try_slot(
        self,
        slot: ModelSlot,
        call: Callable[[ModelSlot], Awaitable[T]],
        est_tokens: int,
        attempts: list[AttemptRecord],
    ) -> T | None:
        max_retries = self.res.max_retries_per_model
        for attempt in range(max_retries + 1):
            if slot.rpm_bucket is not None:
                await slot.rpm_bucket.acquire(1)
            if slot.tpm_bucket is not None and est_tokens > 0:
                await slot.tpm_bucket.acquire(est_tokens)

            start = time.perf_counter()
            try:
                result = await call(slot)
            except RateLimitedError as exc:
                attempts.append(self._record(slot, exc, start))
                wait = exc.retry_after
                if wait is not None and wait <= self.res.max_inline_wait_s and attempt < max_retries:
                    await self._sleep(wait)  # short wait (typically TPM): same model is best
                    continue
                slot.breaker.trip(wait)  # long or unknown wait: skip this model for a while
                return None
            except ProviderError as exc:
                attempts.append(self._record(slot, exc, start))
                if exc.kind == "auth":
                    slot.breaker.trip(AUTH_FAILURE_COOLDOWN_S)
                    return None
                if exc.retryable and attempt < max_retries:
                    await self._sleep(self._backoff(attempt))
                    continue
                if exc.retryable:
                    slot.breaker.record_failure()
                return None  # bad_request: wrong for this model only, breaker unchanged
            else:
                slot.breaker.record_success()
                attempts.append(
                    AttemptRecord(slot=slot.id, outcome="ok", latency_ms=(time.perf_counter() - start) * 1000)
                )
                return result
        return None

    @staticmethod
    def _record(slot: ModelSlot, exc: ProviderError, start: float) -> AttemptRecord:
        record = AttemptRecord(
            slot=slot.id,
            outcome=exc.kind,
            detail=str(exc)[:300],
            latency_ms=(time.perf_counter() - start) * 1000,
        )
        logger.warning("model call failed", extra={"slot": slot.id, "outcome": exc.kind, "detail": record.detail})
        return record
