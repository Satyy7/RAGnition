"""Document-level retrieval metrics against the eval labels.

recall@k = |relevant docs in the top k| / |relevant docs|
           "of the documents a good answer needs, how many did we surface?"
MRR      = mean over queries of 1 / rank of the first relevant doc (0 if none in the list)
           "how high does the first useful document appear?"
"""

from __future__ import annotations


def recall_at_k(ranked_docs: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        raise ValueError("recall is undefined without relevant documents")
    return len(set(ranked_docs[:k]) & relevant) / len(relevant)


def reciprocal_rank(ranked_docs: list[str], relevant: set[str]) -> float:
    for i, doc in enumerate(ranked_docs, start=1):
        if doc in relevant:
            return 1.0 / i
    return 0.0


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
