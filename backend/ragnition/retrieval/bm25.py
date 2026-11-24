"""Okapi BM25, written out so the formula is visible.

score(q, d) = sum over query terms t of
    idf(t) * tf(t,d) * (k1 + 1) / (tf(t,d) + k1 * (1 - b + b * |d| / avgdl))

idf(t) = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))

- tf: how often t occurs in the chunk (more = better, with diminishing returns via k1);
- idf: rare terms (cl_waiting) count far more than common ones (service);
- b: long chunks are penalised a little, since they match more by chance.
An inverted index (term -> {chunk: tf}) means we only touch chunks that share a term.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from ragnition.retrieval.tokenize import tokenize


class BM25Index:
    def __init__(self, ids: list[str], texts: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.ids = ids
        self.k1, self.b = k1, b
        self.postings: dict[str, dict[int, int]] = defaultdict(dict)
        self.lengths: list[int] = []
        for i, text in enumerate(texts):
            counts = Counter(tokenize(text))
            self.lengths.append(sum(counts.values()))
            for term, tf in counts.items():
                self.postings[term][i] = tf
        n = len(texts)
        self.avgdl = (sum(self.lengths) / n) if n else 0.0
        self.idf = {t: math.log(1 + (n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.postings.items()}

    def search(self, query: str, k: int = 10, allowed: set[int] | None = None) -> list[tuple[str, float]]:
        scores: dict[int, float] = defaultdict(float)
        for term in set(tokenize(query)):
            postings = self.postings.get(term)
            if not postings:
                continue
            idf = self.idf[term]
            for i, tf in postings.items():
                if allowed is not None and i not in allowed:
                    continue
                norm = tf + self.k1 * (1 - self.b + self.b * self.lengths[i] / self.avgdl)
                scores[i] += idf * tf * (self.k1 + 1) / norm
        best = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
        return [(self.ids[i], s) for i, s in best]
