import httpx
import pytest

from ragnition.core.providers.ratelimit import TokenBucket, parse_duration, parse_retry_after


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2", 2.0), ("1.5", 1.5), ("7.66s", 7.66), ("2m59.56s", 179.56), ("250ms", 0.25), ("1h", 3600.0)],
)
def test_parse_duration(raw, expected):
    assert parse_duration(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["", "soon", "5x"])
def test_parse_duration_rejects_garbage(raw):
    assert parse_duration(raw) is None


def test_retry_after_prefers_standard_header():
    headers = httpx.Headers({"retry-after": "3", "x-ratelimit-reset-tokens": "10s"})
    assert parse_retry_after(headers) == 3.0


def test_retry_after_uses_smallest_reset_header():
    headers = httpx.Headers({"x-ratelimit-reset-requests": "2m", "x-ratelimit-reset-tokens": "7.5s"})
    assert parse_retry_after(headers) == pytest.approx(7.5)


def test_retry_after_missing():
    assert parse_retry_after(httpx.Headers({})) is None


async def test_bucket_allows_burst_then_waits(clock):
    bucket = TokenBucket(60, clock=clock, sleep=clock.sleep)  # 1 token/s, capacity 60
    for _ in range(60):
        await bucket.acquire()
    assert clock.now == 0.0  # the whole burst fit
    await bucket.acquire()
    assert clock.now == pytest.approx(1.0)  # 61st request waited one refill


async def test_bucket_refills_over_time(clock):
    bucket = TokenBucket(60, clock=clock, sleep=clock.sleep)
    for _ in range(60):
        await bucket.acquire()
    clock.now += 10  # ten seconds pass
    for _ in range(10):
        await bucket.acquire()
    assert clock.now == pytest.approx(10.0)  # 10 tokens refilled, no wait


async def test_bucket_clamps_oversized_request(clock):
    bucket = TokenBucket(100, clock=clock, sleep=clock.sleep)
    await bucket.acquire(500)  # bigger than capacity: takes everything, no deadlock
    assert clock.now == 0.0
    await bucket.acquire(1)
    assert clock.now == pytest.approx(0.6)  # 100/min => one token per 0.6s
