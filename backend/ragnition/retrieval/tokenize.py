"""Identifier-aware tokens for keyword (BM25) search.

Incident text is full of identifiers: payments-api, default_pool_size, CHG-2318,
search-querylog-*, x509. A plain tokenizer splits them into common words and the
exact identifier is lost. We emit BOTH the whole identifier and its parts:

    "PgBouncer default_pool_size" -> pgbouncer, default_pool_size, default, pool, size

so an exact identifier query matches exactly, and "pool size" still matches.
"""

from __future__ import annotations

import re

from ragnition.ingest.parse import normalize_chars

_COMPOUND = re.compile(r"[a-z0-9]+(?:[._\-/:][a-z0-9]+)*")
_PARTS = re.compile(r"[._\-/:]")

# Small list on purpose: words like "not", "no", "down" carry meaning in incidents.
STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the this to was were will with "
    "we our you your they their there then than so such into about after before".split()
)


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for tok in _COMPOUND.findall(normalize_chars(text).lower()):
        if tok in STOPWORDS:
            continue
        out.append(tok)
        if _PARTS.search(tok):
            out.extend(p for p in _PARTS.split(tok) if p and p not in STOPWORDS)
    return out
