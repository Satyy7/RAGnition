"""Reciprocal rank fusion (Cormack, Clarke & Buettcher, SIGIR 2009) with per-list weights.

    score(doc) = sum_i  w_i / (k + rank_i(doc))

RRF uses ranks, not scores, so BM25 scores and cosine similarities never need
calibrating against each other. k=60 is the value from the paper.

Query-adaptive weights: text full of identifiers (snake_case, CHG-1234, metric
names, hostnames, error codes) is where exact-match BM25 shines; plain prose is
where dense search helps. `identifier_ratio` measures that, and `adaptive_weights`
shifts weight between the two lists. It is a rule, not a trained model.
"""

from __future__ import annotations

import re
from collections import defaultdict

RRF_K = 60

_TOKEN = re.compile(r"\S+")
_IDENTIFIER = re.compile(
    r"[a-z0-9]+_[a-z0-9_]+"          # snake_case: default_pool_size
    r"|[A-Za-z]+-\d+"                # ticket / change ids: CHG-2318, SEC-212
    r"|[a-z0-9]+(?:[.-][a-z0-9]+){2,}"  # dotted / hyphenated names: max.poll.records, coredns-6f9b8-2xk4p
    r"|\b[1-5]\d\d\b"                # HTTP status codes
    r"|[a-z]+[A-Z][A-Za-z]+"         # camelCase / PascalCase: CommitFailedError
    r"|=\S"                          # key=value pairs in logs
)


def identifier_ratio(text: str) -> float:
    tokens = _TOKEN.findall(text)
    if not tokens:
        return 0.0
    return sum(bool(_IDENTIFIER.search(t)) for t in tokens) / len(tokens)


def adaptive_weights(text: str, strength: float = 1.0) -> tuple[float, float]:
    """(w_bm25, w_dense), both 1.0 at an identifier ratio of 0.25 (typical mixed text).

    Weights move linearly with the ratio and stay within [0.25, 1.75].
    """
    shift = max(-0.75, min(0.75, strength * (identifier_ratio(text) - 0.25) * 3))
    return 1.0 + shift, 1.0 - shift


def rrf(rankings: list[list[str]], weights: list[float] | None = None, k: int = RRF_K) -> list[str]:
    weights = weights or [1.0] * len(rankings)
    scores: dict[str, float] = defaultdict(float)
    for ranking, w in zip(rankings, weights, strict=True):
        for rank, doc in enumerate(ranking, start=1):
            scores[doc] += w / (k + rank)
    return sorted(scores, key=lambda d: (-scores[d], d))
