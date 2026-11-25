"""Bootstrap confidence intervals, plain Python (no numpy needed).

bootstrap_ci: resample the per-query scores with replacement many times; the
    middle 95% of the resampled means is the confidence interval.
paired_diff_ci: same, on per-query DIFFERENCES between two systems evaluated on
    the same queries. If the interval contains 0, we cannot claim either is better.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Estimate:
    mean: float
    lo: float
    hi: float

    def excludes_zero(self) -> bool:
        return self.lo > 0 or self.hi < 0

    def fmt(self, digits: int = 3) -> str:
        return f"{self.mean:.{digits}f} [{self.lo:.{digits}f}, {self.hi:.{digits}f}]"


def _percentile(sorted_vals: list[float], q: float) -> float:
    """Linear interpolation between closest ranks."""
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def bootstrap_ci(values: list[float], resamples: int = 2000, seed: int = 7, alpha: float = 0.05) -> Estimate:
    if not values:
        raise ValueError("no values")
    n = len(values)
    rng = random.Random(seed)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples))
    return Estimate(sum(values) / n, _percentile(means, alpha / 2), _percentile(means, 1 - alpha / 2))


def paired_diff_ci(a: list[float], b: list[float], resamples: int = 2000, seed: int = 7) -> Estimate:
    """CI of mean(a - b) over the same queries."""
    if len(a) != len(b):
        raise ValueError("paired comparison needs the same queries")
    return bootstrap_ci([x - y for x, y in zip(a, b, strict=True)], resamples, seed)
