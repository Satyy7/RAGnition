"""Fallback behaviour with fake models: no network, fully deterministic."""

import pytest

from ragnition.core.config import ModelSpec, ProviderSpec, ResilienceSpec
from ragnition.core.providers.cache import CallCache
from ragnition.core.providers.fallback import CircuitBreaker, FallbackExecutor, ModelSlot
from ragnition.core.providers.types import AllProvidersExhausted, ChatResponse, ProviderError, RateLimitedError


def make_slot(model: str, clock, api_key: str | None = "key") -> ModelSlot:
    return ModelSlot(
        spec=ModelSpec(provider="fake", model=model),
        provider=ProviderSpec(kind="openai_compat", base_url="http://fake", api_key_setting="groq_api_key"),
        api_key=api_key,
        rpm_bucket=None,
        tpm_bucket=None,
        breaker=CircuitBreaker(failure_threshold=3, cooldown_s=60, clock=clock),
    )


class ScriptedModels:
    """Each model pops its next scripted outcome; 'ok' (or an empty script) succeeds."""

    def __init__(self, script: dict[str, list]):
        self.script = script
        self.calls: list[str] = []

    async def __call__(self, slot: ModelSlot) -> ChatResponse:
        model = slot.spec.model
        self.calls.append(model)
        queue = self.script.get(model, [])
        outcome = queue.pop(0) if queue else "ok"
        if isinstance(outcome, Exception):
            raise outcome
        return ChatResponse(text=f"from {model}", provider="fake", model=model)


@pytest.fixture
def sleeps():
    return []


def make_executor(slots, sleeps, cache=None):
    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    resilience = ResilienceSpec(max_retries_per_model=1, backoff_base_s=0.5, max_inline_wait_s=5)
    return FallbackExecutor("generator", slots, resilience, cache, sleep=fake_sleep, rng=lambda: 1.0)


async def run(executor, models, **kwargs):
    return await executor.run(payload={"q": "x"}, call=models, response_type=ChatResponse, **kwargs)


async def test_long_rate_limit_falls_back_and_opens_breaker(clock, sleeps):
    models = ScriptedModels({"a": [RateLimitedError("slow down", retry_after=60)]})
    ex = make_executor([make_slot("a", clock), make_slot("b", clock)], sleeps)

    first = await run(ex, models)
    assert first.model == "b"
    assert [a.outcome for a in first.attempts] == ["rate_limited", "ok"]

    second = await run(ex, models)  # 'a' is cooling down: skipped without a request
    assert second.attempts[0].outcome == "circuit_open"
    assert models.calls == ["a", "b", "b"]

    clock.now += 61  # cooldown over: 'a' gets a half-open trial and succeeds
    third = await run(ex, models)
    assert third.model == "a"


async def test_short_rate_limit_waits_and_retries_same_model(clock, sleeps):
    models = ScriptedModels({"a": [RateLimitedError("tpm", retry_after=2), "ok"]})
    ex = make_executor([make_slot("a", clock), make_slot("b", clock)], sleeps)
    result = await run(ex, models)
    assert result.model == "a"
    assert sleeps == [2]


async def test_transient_error_backs_off_then_succeeds(clock, sleeps):
    models = ScriptedModels({"a": [ProviderError("502", kind="server", retryable=True), "ok"]})
    ex = make_executor([make_slot("a", clock)], sleeps)
    result = await run(ex, models)
    assert result.model == "a"
    assert sleeps == [0.5]  # base * 2^0 * jitter(1.0)


async def test_auth_error_moves_on_without_retry(clock, sleeps):
    models = ScriptedModels({"a": [ProviderError("401", kind="auth", retryable=False)]})
    ex = make_executor([make_slot("a", clock), make_slot("b", clock)], sleeps)
    result = await run(ex, models)
    assert result.model == "b"
    assert models.calls == ["a", "b"]
    assert sleeps == []


async def test_missing_key_is_skipped_without_a_request(clock, sleeps):
    models = ScriptedModels({})
    ex = make_executor([make_slot("a", clock, api_key=None), make_slot("b", clock)], sleeps)
    result = await run(ex, models)
    assert models.calls == ["b"]
    assert result.attempts[0].outcome == "no_api_key"


async def test_all_failing_raises_with_full_trail(clock, sleeps):
    models = ScriptedModels(
        {
            "a": [RateLimitedError("x", retry_after=30)],
            "b": [ProviderError("400", kind="bad_request", retryable=False)],
        }
    )
    ex = make_executor([make_slot("a", clock), make_slot("b", clock)], sleeps)
    with pytest.raises(AllProvidersExhausted) as info:
        await run(ex, models)
    assert [a.outcome for a in info.value.attempts] == ["rate_limited", "bad_request"]
    assert info.value.retry_in_s == pytest.approx(30)


async def test_cache_hit_skips_the_network(clock, sleeps):
    cache = CallCache(":memory:")
    models = ScriptedModels({})
    ex = make_executor([make_slot("a", clock)], sleeps, cache=cache)
    first = await run(ex, models)
    second = await run(ex, models)
    assert (first.cached, second.cached) == (False, True)
    assert second.text == first.text
    assert models.calls == ["a"]


async def test_cache_hit_from_fallback_model_is_reused(clock, sleeps):
    cache = CallCache(":memory:")
    models = ScriptedModels({"a": [RateLimitedError("x", retry_after=60)]})
    ex = make_executor([make_slot("a", clock), make_slot("b", clock)], sleeps, cache=cache)
    await run(ex, models)  # answered by b and cached under b
    clock.now += 61
    again = await run(ex, models)  # same request: served from b's cache, no new call
    assert again.cached and again.model == "b"
    assert models.calls == ["a", "b"]


async def test_pin_disables_fallback(clock, sleeps):
    models = ScriptedModels({"b": [ProviderError("400", kind="bad_request", retryable=False)]})
    ex = make_executor([make_slot("a", clock), make_slot("b", clock)], sleeps)
    with pytest.raises(AllProvidersExhausted):
        await run(ex, models, pin="fake:b")
    assert models.calls == ["b"]


def test_breaker_opens_after_threshold(clock):
    breaker = CircuitBreaker(failure_threshold=2, cooldown_s=10, clock=clock)
    breaker.record_failure()
    assert breaker.allow()
    breaker.record_failure()
    assert not breaker.allow()
    clock.now += 10
    assert breaker.allow() and breaker.state == "half_open"
    breaker.record_failure()  # failed trial: straight back to open
    assert breaker.state == "open"
