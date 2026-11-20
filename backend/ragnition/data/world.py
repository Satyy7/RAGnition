"""Kestrel Commerce: the fictional company whose knowledge base RAGnition searches.

Two files, deliberately separate:
- data/synthetic/world.yaml   -> what the company KNOWS (services, past incidents,
                                 change log). Becomes corpus documents.
- data/eval/scenarios.yaml    -> NEW incidents and their true root causes.
                                 Never becomes corpus. The corpus generator
                                 cannot even receive it: it only takes a World.

Every fact here is written by hand, so the ground truth is ours, not an LLM's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

SYNTHETIC_LICENSE = "synthetic (project-owned)"


class Company(BaseModel):
    name: str
    description: str
    environment: str


class Service(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    team: str
    language: str
    description: str
    depends_on: list[str] = []
    datastores: list[str] = []
    external: list[str] = []
    slo: str
    config_notes: list[str] = []
    known_failure_modes: list[str] = []


class Change(BaseModel):
    id: str = Field(pattern=r"^CHG-\d{4}$")
    date: str
    service: str
    kind: Literal["deploy", "config", "infra", "migration", "vendor-notice"]
    team: str
    summary: str
    details: str
    status: Literal["applied", "scheduled", "rolled-back"] = "applied"


class HistoryIncident(BaseModel):
    id: str = Field(pattern=r"^PM-\d{4}-\d{3}$")
    date: str
    service: str
    severity: Literal["SEV1", "SEV2", "SEV3"]
    title: str
    duration_min: int
    impact: str
    detection: str
    trigger: str
    root_cause: str
    resolution: str
    contributing_factors: list[str] = []
    timeline: list[str]
    action_items: list[str]
    related_changes: list[str] = []


class World(BaseModel):
    company: Company
    services: list[Service]
    changes: list[Change]
    history: list[HistoryIncident]

    @model_validator(mode="after")
    def _references_resolve(self) -> World:
        services = {s.id for s in self.services}
        changes = {c.id for c in self.changes}
        for name, ids in (("service", [s.id for s in self.services]), ("change", [c.id for c in self.changes]),
                          ("incident", [h.id for h in self.history])):
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {name} ids")
        for s in self.services:
            if missing := set(s.depends_on) - services:
                raise ValueError(f"{s.id} depends on unknown services {sorted(missing)}")
        for c in self.changes:
            if c.service not in services:
                raise ValueError(f"{c.id} references unknown service {c.service}")
        for h in self.history:
            if h.service not in services:
                raise ValueError(f"{h.id} references unknown service {h.service}")
            if missing := set(h.related_changes) - changes:
                raise ValueError(f"{h.id} references unknown changes {sorted(missing)}")
        return self

    def service(self, service_id: str) -> Service:
        return next(s for s in self.services if s.id == service_id)


# Stable corpus document ids. Labels point at these, so retrieval can be scored.
def service_doc_id(service_id: str) -> str:
    return f"kestrel/service/{service_id}"


def runbook_doc_id(service_id: str) -> str:
    return f"kestrel/runbook/{service_id}"


def postmortem_doc_id(incident_id: str) -> str:
    return f"kestrel/postmortem/{incident_id}"


def change_doc_id(change_id: str) -> str:
    return f"kestrel/change/{change_id}"


def corpus_doc_ids(world: World) -> set[str]:
    ids = {service_doc_id(s.id) for s in world.services}
    ids |= {runbook_doc_id(s.id) for s in world.services}
    ids |= {postmortem_doc_id(h.id) for h in world.history}
    ids |= {change_doc_id(c.id) for c in world.changes}
    return ids


# ---------------------------------------------------------------- evaluation side


class IncidentReport(BaseModel):
    """What an on-call engineer would paste into RAGnition."""

    reporter_note: str
    alerts: list[str]
    logs: list[str]


class Label(BaseModel):
    """The answer. Lives only in data/eval."""

    culprit_service: str | None
    category: str | None
    root_cause: str | None
    key_facts: list[str] = []
    supporting_doc_ids: list[str] = []
    expected_fix: str | None = None


class EvalScenario(BaseModel):
    id: str = Field(pattern=r"^EVAL-\d{3}$")
    title: str
    reported_at: str
    answerable: bool
    reasoning: Literal["single-hop", "multi-hop", "unanswerable"]
    incident: IncidentReport
    label: Label

    @model_validator(mode="after")
    def _label_matches_answerability(self) -> EvalScenario:
        if self.answerable and not (self.label.root_cause and self.label.supporting_doc_ids):
            raise ValueError(f"{self.id}: answerable scenarios need a root cause and supporting docs")
        if not self.answerable and (self.label.root_cause or self.label.supporting_doc_ids):
            raise ValueError(f"{self.id}: unanswerable scenarios must not carry an answer")
        return self


class EvalSet(BaseModel):
    scenarios: list[EvalScenario]

    def validate_against(self, world: World) -> None:
        """Every label must point at documents that really exist in the corpus."""
        known_docs = corpus_doc_ids(world)
        known_services = {s.id for s in world.services}
        ids = [s.id for s in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate eval scenario ids")
        for s in self.scenarios:
            if missing := set(s.label.supporting_doc_ids) - known_docs:
                raise ValueError(f"{s.id}: supporting docs not in corpus: {sorted(missing)}")
            if s.label.culprit_service and s.label.culprit_service not in known_services:
                raise ValueError(f"{s.id}: unknown culprit service {s.label.culprit_service}")


def load_world(path: Path) -> World:
    with path.open(encoding="utf-8") as fh:
        return World.model_validate(yaml.safe_load(fh))


def load_eval_set(path: Path) -> EvalSet:
    with path.open(encoding="utf-8") as fh:
        return EvalSet.model_validate(yaml.safe_load(fh))
