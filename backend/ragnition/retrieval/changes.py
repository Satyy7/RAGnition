"""Change-aware retrieval: "what changed near this service, shortly before it broke?"

An on-call engineer does not search the change log by symptom words (a pool-size
change never says "503"). They ask which services are involved, walk the
dependency map, and look at recent changes there. This module does the same:

1. services in the incident text  (EntityLinker, generated from the registry);
2. graph distance from those services to every other service (depends_on edges,
   both directions; shared infrastructure is one hop from everything);
3. candidate changes: dated on or before the incident, inside the window, not
   rolled back. Scheduled vendor notices are open risks and kept regardless of age;
4. score = proximity(hops) * recency(days) * risk(kind)   -- transparent, no training.

Every score carries its explanation (`ChangeCandidate.why`), so an answer can say
WHY a change was suggested. Parameters are chosen on the DEV set only
(scripts/run_m6_benchmark.py --split dev) and stored in config/change_retrieval.yaml.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from ragnition.core.config import PROJECT_ROOT
from ragnition.data.world import Change, World, change_doc_id
from ragnition.retrieval.entities import EntityLinker

CHANGE_PARAMS_PATH = PROJECT_ROOT / "config" / "change_retrieval.yaml"


class ChangeParams(BaseModel):
    window_days: float = Field(default=14, gt=0)
    tau_days: float = Field(default=5, gt=0)  # recency = exp(-days / tau)
    max_hops: int = Field(default=2, ge=0, le=3)
    proximity: list[float] = [1.0, 0.6, 0.35]  # by hop count 0, 1, 2 (, 3)
    # Prior risk by change type (fixed from SRE experience, NOT tuned): config and
    # code changes cause most incidents; scheduled vendor notices are open risks.
    risk: dict[str, float] = {"config": 1.0, "deploy": 0.9, "migration": 0.9, "infra": 0.8, "vendor-notice": 0.7}
    open_risk_recency: float = 0.5  # recency factor for scheduled vendor notices
    fusion_weight: float = Field(default=1.0, ge=0)  # weight of the change list in RRF
    top_n: int = 20
    chosen_by: str = "default"


def load_change_params(path: Path = CHANGE_PARAMS_PATH) -> ChangeParams:
    if not path.exists():
        return ChangeParams()
    with path.open(encoding="utf-8") as fh:
        return ChangeParams.model_validate(yaml.safe_load(fh))


def save_change_params(params: ChangeParams, path: Path = CHANGE_PARAMS_PATH) -> None:
    header = "# Written by scripts/run_m6_benchmark.py --split dev --apply (tuned on DEV incidents only).\n"
    path.write_text(header + yaml.safe_dump(params.model_dump(), sort_keys=False), encoding="utf-8")


class ServiceGraph:
    def __init__(self, world: World) -> None:
        self.services = [s.id for s in world.services]
        self.adj: dict[str, set[str]] = {s: set() for s in self.services}
        for s in world.services:
            for dep in s.depends_on:
                self.adj[s.id].add(dep)
                self.adj[dep].add(s.id)
        for shared in (s.id for s in world.services if s.shared):
            for other in self.services:
                if other != shared:
                    self.adj[shared].add(other)
                    self.adj[other].add(shared)

    def distances(self, seeds: list[str], max_hops: int) -> dict[str, int]:
        """Multi-source BFS: hops from the nearest seed, up to max_hops."""
        dist = {s: 0 for s in seeds if s in self.adj}
        queue = deque(dist)
        while queue:
            node = queue.popleft()
            if dist[node] == max_hops:
                continue
            for nxt in self.adj[node]:
                if nxt not in dist:
                    dist[nxt] = dist[node] + 1
                    queue.append(nxt)
        return dist


@dataclass(frozen=True)
class ChangeCandidate:
    change: Change
    hops: int
    days: float
    score: float

    @property
    def doc_id(self) -> str:
        return change_doc_id(self.change.id)

    @property
    def why(self) -> str:
        where = "on an affected service" if self.hops == 0 else f"{self.hops} hop(s) from an affected service"
        return f"{self.change.kind} change {where}, {self.days:.1f} days before the incident"


def _days_before(reported_at: str, change_date: str) -> float:
    t = datetime.fromisoformat(reported_at.replace("Z", "+00:00"))
    d = datetime.fromisoformat(change_date).replace(tzinfo=timezone.utc)
    return (t - d).total_seconds() / 86400


class ChangeRetriever:
    def __init__(self, world: World, params: ChangeParams | None = None) -> None:
        self.world = world
        self.params = params or ChangeParams()
        self.graph = ServiceGraph(world)
        self.linker = EntityLinker(world)

    def candidates(self, text: str, reported_at: str, seeds: list[str] | None = None) -> list[ChangeCandidate]:
        p = self.params
        seeds = self.linker.services(text) if seeds is None else seeds
        if not seeds:
            return []  # nothing to anchor on: add nothing rather than guess
        dist = self.graph.distances(seeds, p.max_hops)
        out = []
        for ch in self.world.changes:
            if ch.status == "rolled-back" or ch.service not in dist:
                continue
            days = _days_before(reported_at, ch.date)
            if days < 0:
                continue  # dated after the incident: cannot be its cause
            if ch.status == "scheduled" and ch.kind == "vendor-notice":
                recency = p.open_risk_recency
            elif days <= p.window_days:
                recency = math.exp(-days / p.tau_days)
            else:
                continue
            hops = dist[ch.service]
            score = p.proximity[min(hops, len(p.proximity) - 1)] * recency * p.risk.get(ch.kind, 0.8)
            out.append(ChangeCandidate(ch, hops, days, score))
        out.sort(key=lambda c: (-c.score, c.days, c.change.id))
        return out[: p.top_n]

    def ranked_docs(self, text: str, reported_at: str) -> list[str]:
        return [c.doc_id for c in self.candidates(text, reported_at)]


def latest_change_baseline(world: World, linker: EntityLinker, text: str, reported_at: str,
                           top_n: int = 20) -> list[str]:
    """Naive baseline: changes to the services NAMED in the incident, newest first."""
    seeds = set(linker.services(text))
    rows = [(c, _days_before(reported_at, c.date)) for c in world.changes if c.service in seeds]
    rows = [(c, d) for c, d in rows if d >= 0]
    rows.sort(key=lambda r: (r[1], r[0].id))
    return [change_doc_id(c.id) for c, _ in rows[:top_n]]
