"""Client-side rate limiting and server rate-limit header parsing."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

_EPS = 1e-9
_DURATION_FULL = re.compile(r"(?:\d+(?:\.\d+)?(?:ms|h|m|s))+")
_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")
_UNIT_SECONDS = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}


class TokenBucket:
    """Token bucket: holds up to `capacity` tokens, refilled continuously.

    rate_per_minute=30 means one token every 2s. A request spends tokens; if not
    enough are available we sleep exactly long enough for the refill. Used for
    both requests-per-minute (spend 1) and tokens-per-minute (spend ~prompt size).
    """

    def __init__(
        self,
        rate_per_minute: float,
        capacity: float | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate_per_minute <= 0:
            raise ValueError("rate_per_minute must be positive")
        self.rate_per_s = rate_per_minute / 60.0
        self.capacity = capacity if capacity is not None else rate_per_minute
        self.tokens = self.capacity
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self.tokens = min(self.capacity, self.tokens + (now - self._updated) * self.rate_per_s)
        self._updated = now

    async def acquire(self, n: float = 1.0) -> None:
        # A request bigger than the whole bucket could never be satisfied; cap it.
        n = min(n, self.capacity)
        # Holding the lock while sleeping makes waiters queue up in order (FIFO).
        async with self._lock:
            while True:
                self._refill()
                if self.tokens + _EPS >= n:
                    self.tokens -= n
                    return
                await self._sleep((n - self.tokens) / self.rate_per_s)


def parse_duration(value: str) -> float | None:
    """Parse '7', '1.5', '7.66s', '2m59.56s', '250ms', '1h' into seconds."""
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    if not _DURATION_FULL.fullmatch(value):
        return None
    return sum(float(num) * _UNIT_SECONDS[unit] for num, unit in _DURATION_PART.findall(value))


def parse_retry_after(headers: Mapping[str, str]) -> float | None:
    """How long the server wants us to wait, in seconds, if it said so.

    Prefer the standard Retry-After header (seconds or an HTTP date). Otherwise use
    the SMALLEST x-ratelimit-reset-* value: on Groq, reset-requests can refer to a
    daily window while reset-tokens is per minute, and the max would be misleading.
    """
    lowered = {k.lower(): v for k, v in headers.items()}
    raw = lowered.get("retry-after")
    if raw:
        seconds = parse_duration(raw)
        if seconds is not None:
            return max(0.0, seconds)
        try:
            when = parsedate_to_datetime(raw)
            return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError):
            pass
    resets = [
        parsed
        for key in ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens")
        if (val := lowered.get(key)) and (parsed := parse_duration(val)) is not None
    ]
    return min(resets) if resets else None
