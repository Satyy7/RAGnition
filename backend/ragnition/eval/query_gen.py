"""Synthetic retrieval queries: an LLM writes questions a document answers, a
second model family checks them, and the source document becomes the label.

Guards against the two classic traps:
- wording bias: the generator must write as an on-call engineer who has NOT read
  the document (symptoms, not the document's phrasing); we also measure each
  query's word overlap with its document and report results by overlap;
- bad questions: the judge (different model family) drops queries that the
  document does not answer or that are too vague to point at it.
Queries that name a document id (CHG-..., PM-...) are dropped: an engineer
would not know which document to look for.
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel

from ragnition.core.providers import ChatMessage, ChatRequest, ModelGateway
from ragnition.retrieval.tokenize import tokenize

Style = Literal["keyword", "question", "symptom", "incident"]
STYLES: tuple[Style, ...] = ("keyword", "question", "symptom")
MAX_DOC_CHARS = 6000  # ~1,500 tokens: enough to see what a document is about

STYLE_RULES = {
    "keyword": "3-8 words typed into a search box, e.g. 'payments 503 pgbouncer waiting'",
    "question": "one natural question in plain English",
    "symptom": "1-3 sentences describing what the engineer observes (alerts, errors, behaviour), not the cause",
}

_DOC_ID = re.compile(r"\b(chg-\d{4}|pm-\d{4}-\d{3}|eval-\d{3})\b", re.I)


class RetrievalQuery(BaseModel):
    id: str
    query: str
    style: Style
    relevant_doc_ids: list[str]
    source_doc: str
    source_type: str
    synthetic_source: bool
    origin: Literal["generated", "incident"]
    overlap: float  # share of query terms that also occur in the source document
    kept: bool = True
    drop_reason: str | None = None
    generator_model: str | None = None
    judge_model: str | None = None


def extract_json(text: str) -> dict:
    """Parse the first JSON object in a model reply (tolerates code fences / chatter)."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in reply")
    return json.loads(text[start : end + 1])


def lexical_overlap(query: str, doc_text: str) -> float:
    q = set(tokenize(query))
    return len(q & set(tokenize(doc_text))) / len(q) if q else 0.0


def generation_prompt(title: str, text: str, styles: list[Style]) -> ChatRequest:
    wanted = "\n".join(f'- style "{s}": {STYLE_RULES[s]}' for s in styles)
    system = (
        "You create search queries for evaluating an incident-investigation search engine. "
        "Write as an on-call engineer in the middle of an incident who has NOT read the document below "
        "and is searching for help. Describe situations, symptoms and questions in your own words. "
        "Do not copy sentences or distinctive phrases from the document; identifiers an engineer would "
        "really see (service names, error messages, metric names) are fine. Never mention document ids "
        "such as CHG-1234 or PM-2025-001. Each query must be answerable from this document specifically. "
        'Reply with JSON only: {"queries": [{"style": "...", "query": "..."}]}'
    )
    user = f"Document title: {title}\n\nDocument:\n{text[:MAX_DOC_CHARS]}\n\nWrite exactly one query for each style:\n{wanted}"
    return ChatRequest(messages=[ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)],
                       max_tokens=1500, json_mode=True)


def judge_prompt(title: str, text: str, queries: list[str]) -> ChatRequest:
    numbered = "\n".join(f"{i}. {q}" for i, q in enumerate(queries))
    system = (
        "You check search queries used to evaluate a retrieval system. For each query decide: "
        "answerable = the document contains information that answers or directly helps with the query; "
        "specific = the query is specific enough that this document is among the best matches, rather "
        "than being generic enough to match many unrelated documents. Be strict. "
        'Reply with JSON only: {"verdicts": [{"i": 0, "answerable": true, "specific": true, "reason": "..."}]}'
    )
    user = f"Document title: {title}\n\nDocument:\n{text[:MAX_DOC_CHARS]}\n\nQueries:\n{numbered}"
    return ChatRequest(messages=[ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)],
                       max_tokens=1500, json_mode=True)


class SourceDoc(BaseModel):
    doc_id: str
    title: str
    text: str
    doc_type: str
    synthetic: bool


def plan_styles(docs: list[SourceDoc], public_per_doc: int = 1) -> list[list[Style]]:
    """All three styles for Kestrel docs; rotate one style per public doc."""
    plans, rot = [], 0
    for d in docs:
        if d.synthetic:
            plans.append(list(STYLES))
        else:
            plans.append([STYLES[(rot + j) % 3] for j in range(public_per_doc)])
            rot += 1
    return plans


def sample_public(docs: list[SourceDoc], n: int, seed: int = 7) -> list[SourceDoc]:
    """Stratified by source so one big source does not dominate."""
    by_source: dict[str, list[SourceDoc]] = {}
    for d in sorted(docs, key=lambda d: d.doc_id):
        by_source.setdefault(d.doc_id.split("/")[0], []).append(d)
    rng = random.Random(seed)
    for group in by_source.values():
        rng.shuffle(group)
    picked: list[SourceDoc] = []
    while len(picked) < n and any(by_source.values()):
        for group in by_source.values():
            if group and len(picked) < n:
                picked.append(group.pop())
    return picked


async def generate_for_doc(gw: ModelGateway, doc: SourceDoc, styles: list[Style], next_id: Callable[[], str],
                           ) -> list[RetrievalQuery]:
    gen = await gw.chat(generation_prompt(doc.title, doc.text, styles), role="generator")
    raw = extract_json(gen.text).get("queries", [])
    queries = [q for q in raw if isinstance(q, dict) and q.get("query") and q.get("style") in styles]
    if not queries:
        return []
    judge = await gw.chat(judge_prompt(doc.title, doc.text, [q["query"] for q in queries]), role="judge")
    verdicts = {v.get("i"): v for v in extract_json(judge.text).get("verdicts", []) if isinstance(v, dict)}

    out = []
    for i, q in enumerate(queries):
        text = " ".join(str(q["query"]).split())
        v = verdicts.get(i, {})
        reason = None
        if _DOC_ID.search(text):
            reason = "names a document id"
        elif not v:
            reason = "judge gave no verdict"
        elif not v.get("answerable"):
            reason = f"not answerable: {v.get('reason', '')}"
        elif not v.get("specific"):
            reason = f"too generic: {v.get('reason', '')}"
        out.append(RetrievalQuery(
            id=next_id(), query=text, style=q["style"], relevant_doc_ids=[doc.doc_id], source_doc=doc.doc_id,
            source_type=doc.doc_type, synthetic_source=doc.synthetic, origin="generated",
            overlap=round(lexical_overlap(text, doc.text), 3), kept=reason is None, drop_reason=reason,
            generator_model=f"{gen.provider}:{gen.model}", judge_model=f"{judge.provider}:{judge.model}",
        ))
    return out
