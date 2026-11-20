"""Parse danluu/post-mortems README into (company, url, category) entries.

Entries are paragraphs shaped like:  [Company](https://...). One-line summary.
Section headings (## Config Errors, ## Database, ...) give each entry a category.
Pure function: no network, easy to test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ENTRY = re.compile(r"^\[(?P<company>[^\]]+)\]\((?P<url>https?://[^)\s]+)\)\.?\s*(?P<summary>.*)$")
_HEADING = re.compile(r"^##\s+(?P<name>.+?)\s*$")
# Sections after the postmortem list are links to other lists, analysis, credits.
_STOP_SECTIONS = {"other lists of postmortems", "analysis", "contributors"}


@dataclass(frozen=True)
class PostmortemLink:
    company: str
    url: str
    category: str
    summary: str


def parse_postmortem_index(markdown: str) -> list[PostmortemLink]:
    entries: list[PostmortemLink] = []
    seen: set[str] = set()
    category = "Uncategorized"
    for raw in markdown.splitlines():
        line = raw.strip()
        if heading := _HEADING.match(line):
            category = heading["name"]
            if category.lower() in _STOP_SECTIONS:
                break
            continue
        if (m := _ENTRY.match(line)) and m["url"] not in seen:
            seen.add(m["url"])
            entries.append(PostmortemLink(m["company"].strip(), m["url"], category, m["summary"].strip()))
    return entries


def balanced_sample(entries: list[PostmortemLink], limit: int) -> list[PostmortemLink]:
    """Round-robin across categories so the sample is not 80 config errors."""
    by_cat: dict[str, list[PostmortemLink]] = {}
    for e in entries:
        by_cat.setdefault(e.category, []).append(e)
    queues = list(by_cat.values())
    picked: list[PostmortemLink] = []
    while len(picked) < limit and any(queues):
        for q in queues:
            if q and len(picked) < limit:
                picked.append(q.pop(0))
    return picked
