"""Reranking incident evidence without throwing away the structural signal.

A cross-encoder scores "does this passage's TEXT answer this query?". That is the
right test for explanatory evidence (postmortems, runbooks, service pages, docs)
and the wrong one for change records: "PgBouncer default_pool_size 60 -> 25" does
not read as relevant to "payments 503s", yet it is the cause. Three ways to apply
a reranker to the fused top-N:

- naive:            reorder everything by reranker score (standard RAG);
- blend:            RRF of the fused order and the reranker order (reranker votes);
- slot-preserving:  change records keep their positions from the change-aware
                    ranking; the reranker reorders only the other documents into
                    the remaining positions. Root-cause recall is therefore
                    unchanged BY CONSTRUCTION; the question is whether the rest
                    of the list gets better.

Cross-encoders read about 512 tokens for query + passage together, so the long
incident text is condensed and each document is represented by one passage.
"""

from __future__ import annotations

import re

from ragnition.retrieval.fusion import rrf

CHANGE_PREFIX = "kestrel/change/"
_SEVERE = re.compile(r"\b(error|fatal|exception|failed|refused|timeout|timed out|oom|denied|rejected|5\d\d)\b", re.I)
_VOLATILE = re.compile(r"\d{4}-\d{2}-\d{2}T[\d:.]+Z|\b[0-9a-f]{6,}\b|\b\d+(\.\d+)?(ms|s|m)?\b")


def is_change(doc_id: str) -> bool:
    return doc_id.startswith(CHANGE_PREFIX)


def _clip(text: str, max_words: int) -> str:
    words = text.split()
    return " ".join(words[:max_words])


def condense_incident(title: str, note: str, alerts: list[str], logs: list[str], max_words: int = 150) -> str:
    """Short query for a cross-encoder: title, note, alerts, then error-like log lines first.

    Log lines that differ only in timestamps, ids or numbers are kept once.
    """
    seen, picked = set(), []
    for line in sorted(logs, key=lambda l: not _SEVERE.search(l)):  # severe lines first, stable otherwise
        key = _VOLATILE.sub("#", line)
        if key not in seen:
            seen.add(key)
            picked.append(line)
    parts = [title, note.strip(), *alerts, *picked]
    return _clip("\n".join(p for p in parts if p), max_words)


def passage(text: str, max_words: int = 220) -> str:
    return _clip(text, max_words)


def naive(order: list[str], scores: dict[str, float]) -> list[str]:
    head = [d for d in order if d in scores]
    tail = [d for d in order if d not in scores]
    return sorted(head, key=lambda d: (-scores[d], order.index(d))) + tail


def blend(order: list[str], scores: dict[str, float], weight: float = 1.0) -> list[str]:
    head = [d for d in order if d in scores]
    tail = [d for d in order if d not in scores]
    by_score = sorted(head, key=lambda d: (-scores[d], order.index(d)))
    return rrf([head, by_score], [1.0, weight]) + tail


def slot_preserving(order: list[str], scores: dict[str, float]) -> list[str]:
    """Change records stay where they are; other reranked docs fill the free slots by score."""
    head = [d for d in order if d in scores]
    tail = [d for d in order if d not in scores]
    others = iter(sorted((d for d in head if not is_change(d)), key=lambda d: (-scores[d], order.index(d))))
    return [d if is_change(d) else next(others) for d in head] + tail
