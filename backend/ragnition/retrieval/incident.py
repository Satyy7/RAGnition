"""IncidentRetriever: incident in, ranked evidence with explanations out.

This is what the answer generator (M8) calls. It composes M4-M7:

    BM25 + dense (chunks -> documents)          text evidence
    change-aware candidates (M6)                structural evidence, each with a `why`
    plain RRF of the three lists                (M6 showed adaptive weights hurt)
    reranker, slot-preserving (M7)              reorders explanatory docs only

The benchmark uses `gather` directly so it can score many settings from one
retrieval pass; production uses `IncidentRetriever.retrieve`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from ragnition.core.config import PROJECT_ROOT
from ragnition.core.providers import ModelGateway
from ragnition.retrieval.changes import ChangeParams, ChangeRetriever
from ragnition.retrieval.evidence import blend, condense_incident, naive, passage, slot_preserving
from ragnition.retrieval.fusion import rrf
from ragnition.retrieval.retriever import Hit, Retriever, rank_docs

RERANK_CONFIG_PATH = PROJECT_ROOT / "config" / "rerank.yaml"
CHUNKS_PER_QUERY = 50
MAX_DEPTH = 30


class RerankSetup(BaseModel):
    reranker: str = "local:BAAI/bge-reranker-base"  # gateway slot id
    method: str = Field(default="slots", pattern="^(none|naive|blend|slots)$")
    depth: int = Field(default=20, ge=1, le=MAX_DEPTH)
    condense: bool = True
    blend_weight: float = 1.0
    chosen_by: str = "default"


def load_rerank_setup(path: Path = RERANK_CONFIG_PATH) -> RerankSetup:
    if not path.exists():
        return RerankSetup()
    with path.open(encoding="utf-8") as fh:
        return RerankSetup.model_validate(yaml.safe_load(fh))


def save_rerank_setup(setup: RerankSetup, path: Path = RERANK_CONFIG_PATH) -> None:
    header = "# Written by scripts/run_m7_benchmark.py --split dev --apply (tuned on DEV incidents only).\n"
    path.write_text(header + yaml.safe_dump(setup.model_dump(), sort_keys=False), encoding="utf-8")


@dataclass
class Gathered:
    order: list[str]                 # fused ranking of documents (before reranking)
    passages: dict[str, str]         # doc -> best passage, for the top MAX_DEPTH docs
    why: dict[str, str] = field(default_factory=dict)  # change doc -> explanation


def best_passages(order: list[str], hit_lists: list[list[Hit]], retriever: Retriever, depth: int) -> dict[str, str]:
    """Each document is represented by its best-ranked chunk; documents only found by the
    change retriever (no text hit) use their first chunk (change records are one chunk)."""
    best: dict[str, str] = {}
    for hits in hit_lists:
        for h in hits:
            best.setdefault(h.chunk.doc_id, h.chunk.text)
    first: dict[str, str] = {}
    for c in sorted(retriever.searchable, key=lambda c: c.position):
        first.setdefault(c.doc_id, c.text)
    return {d: passage(best.get(d) or first.get(d, d)) for d in order[:depth]}


def gather(retriever: Retriever, changes: ChangeRetriever, vector: list[float], text: str, reported_at: str,
           params: ChangeParams) -> Gathered:
    dense_hits = retriever.dense_by_vector(vector, CHUNKS_PER_QUERY)
    bm25_hits = retriever.sparse(text, CHUNKS_PER_QUERY)
    cands = changes.candidates(text, reported_at)
    change_list = [c.doc_id for c in cands]
    order = rrf([rank_docs(bm25_hits), rank_docs(dense_hits), change_list], [1.0, 1.0, params.fusion_weight])
    return Gathered(order, best_passages(order, [dense_hits, bm25_hits], retriever, MAX_DEPTH),
                    {c.doc_id: c.why for c in cands})


def apply_rerank(order: list[str], scores: dict[str, float], setup: RerankSetup) -> list[str]:
    scores = {d: s for d, s in scores.items() if d in set(order[: setup.depth])}
    if setup.method == "naive":
        return naive(order, scores)
    if setup.method == "blend":
        return blend(order, scores, setup.blend_weight)
    if setup.method == "slots":
        return slot_preserving(order, scores)
    return order


@dataclass
class Evidence:
    doc_id: str
    rank: int
    kind: str
    passage: str
    why: str


class IncidentRetriever:
    def __init__(self, retriever: Retriever, changes: ChangeRetriever, gw: ModelGateway, *,
                 query_instruction: str = "", params: ChangeParams | None = None,
                 rerank: RerankSetup | None = None) -> None:
        self.retriever, self.changes, self.gw = retriever, changes, gw
        self.instruction = query_instruction
        self.params = params or changes.params
        self.rerank = rerank or RerankSetup()

    async def retrieve(self, *, title: str, note: str, alerts: list[str], logs: list[str], reported_at: str,
                       k: int = 10) -> tuple[list[Evidence], bool]:
        """Returns (evidence, degraded). degraded=True means no reranker answered."""
        text = "\n".join([title, note, *alerts, *logs])
        vector = (await self.gw.embed([self.instruction + text], input_type="query")).vectors[0]
        g = gather(self.retriever, self.changes, vector, text, reported_at, self.params)
        order, degraded = g.order, False
        if self.rerank.method != "none":
            head = order[: self.rerank.depth]
            query = condense_incident(title, note, alerts, logs) if self.rerank.condense else text
            resp = await self.gw.rerank(query, [g.passages[d] for d in head])
            degraded = resp.degraded
            if not degraded:
                order = apply_rerank(order, {head[r.index]: r.score for r in resp.results}, self.rerank)
        kinds = defaultdict(lambda: "document")
        kinds.update({d: d.split("/")[1] for d in order if d.startswith("kestrel/")})
        return [Evidence(d, i + 1, kinds[d], g.passages.get(d, ""), g.why.get(d, "matched the incident text"))
                for i, d in enumerate(order[:k])], degraded
