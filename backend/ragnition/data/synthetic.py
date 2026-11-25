"""Turn the hand-written Kestrel world into corpus documents, and eval files.

Two kinds of corpus document:
- deterministic (service pages, change records): rendered straight from world.yaml;
- LLM-written prose (runbooks, postmortems): the facts come from world.yaml, the
  LLM only writes them up. Prompts forbid inventing facts and the output must
  contain every required section, or generation fails loudly.

`build_corpus` takes a World and nothing else from the eval side, so the eval
answers cannot reach a prompt or a corpus file even by accident.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path

import yaml

from ragnition.core.providers import ChatMessage, ChatRequest, ModelGateway
from ragnition.data.manifest import Manifest, ManifestRecord, now_iso, store_document
from ragnition.data.paths import EVAL_INCIDENTS_DIR, EVAL_LABELS_PATH, SYNTH_CORPUS_DIR
from ragnition.data.world import (
    SYNTHETIC_LICENSE,
    Change,
    EvalSet,
    HistoryIncident,
    Service,
    World,
    change_doc_id,
    postmortem_doc_id,
    runbook_doc_id,
    service_doc_id,
)

BANNER = "> SYNTHETIC DOCUMENT. Kestrel Commerce is a fictional company created for RAGnition.\n\n"

RUNBOOK_SECTIONS = ["Overview", "Key metrics and dashboards", "Alerts and first response", "Diagnosis",
                    "Mitigation", "Escalation"]
POSTMORTEM_SECTIONS = ["Summary", "Impact", "Detection", "Timeline", "Root cause", "Trigger", "Resolution",
                       "Contributing factors", "Action items", "Lessons learned"]


class GenerationError(RuntimeError):
    pass


# ----------------------------------------------------------- deterministic docs


def _bullets(items: Iterable[str]) -> str:
    items = list(items)
    return "\n".join(f"- {i}" for i in items) if items else "- none"


def render_service(world: World, svc: Service) -> str:
    used_by = [s.id for s in world.services if svc.id in s.depends_on]
    if svc.shared:
        used_by = ["every service (shared infrastructure)"]
    return (
        f"{BANNER}# Service: {svc.id}\n\n"
        f"**Team:** {svc.team}  \n**Runtime:** {svc.language}  \n**SLO:** {svc.slo}\n\n"
        f"{svc.description}\n\n"
        f"## Dependencies\n\nCalls:\n{_bullets(svc.depends_on)}\n\nCalled by:\n{_bullets(used_by)}\n\n"
        f"## Datastores\n\n{_bullets(svc.datastores)}\n\n"
        f"## External providers\n\n{_bullets(svc.external)}\n\n"
        f"## Configuration notes\n\n{_bullets(svc.config_notes)}\n\n"
        f"## Known failure modes\n\n{_bullets(svc.known_failure_modes)}\n"
    )


def render_change(change: Change) -> str:
    return (
        f"{BANNER}# {change.id}: {change.summary}\n\n"
        f"- **Date:** {change.date}\n- **Service:** {change.service}\n- **Type:** {change.kind}\n"
        f"- **Team:** {change.team}\n- **Status:** {change.status}\n\n"
        f"## Details\n\n{change.details}\n"
    )


# ------------------------------------------------------------------- LLM prose

_SYSTEM = (
    "You are a senior site reliability engineer at Kestrel Commerce writing internal documentation. "
    "Use ONLY the facts provided. Do not invent incidents, change ids, dates, numbers, people or tools "
    "that are not in the facts. Generic commands (kubectl, psql, redis-cli) are allowed. "
    "Write in plain Markdown, no preamble, no closing remarks."
)


def runbook_prompt(world: World, svc: Service) -> ChatRequest:
    facts = render_service(world, svc).removeprefix(BANNER)
    sections = "\n".join(f"## {s}" for s in RUNBOOK_SECTIONS)
    user = (
        f"Company environment: {world.company.environment}\n\nService facts:\n{facts}\n\n"
        f"Write the on-call runbook for {svc.id}. Start with '# Runbook: {svc.id}' and use exactly these "
        f"second-level sections in order:\n{sections}\n"
        "Under Diagnosis, give one subsection per known failure mode with concrete checks."
    )
    return ChatRequest(messages=[ChatMessage(role="system", content=_SYSTEM), ChatMessage(role="user", content=user)],
                       max_tokens=3000)


def postmortem_prompt(world: World, inc: HistoryIncident) -> ChatRequest:
    facts = yaml.safe_dump(inc.model_dump(), sort_keys=False, allow_unicode=True)
    svc = world.service(inc.service)
    sections = "\n".join(f"## {s}" for s in POSTMORTEM_SECTIONS)
    user = (
        f"Service context: {svc.id} ({svc.description})\n\nIncident facts (YAML):\n{facts}\n"
        f"Write a blameless postmortem. Start with '# {inc.id}: {inc.title}' then a line with date, "
        f"severity and service, then exactly these second-level sections in order:\n{sections}\n"
        "Lessons learned must follow from the facts; do not add new action items."
    )
    return ChatRequest(messages=[ChatMessage(role="system", content=_SYSTEM), ChatMessage(role="user", content=user)],
                       max_tokens=3000)


_FENCE = re.compile(r"^```(?:markdown|md)?\s*\n(.*)\n```\s*$", re.S)


def clean_markdown(text: str) -> str:
    text = text.strip()
    if m := _FENCE.match(text):
        text = m.group(1).strip()
    return text + "\n"


def missing_sections(markdown: str, sections: list[str]) -> list[str]:
    found = {h.strip().lower() for h in re.findall(r"^##\s+(.+?)\s*$", markdown, re.M)}
    return [s for s in sections if s.lower() not in found]


async def _write_prose(gw: ModelGateway, request: ChatRequest, sections: list[str]) -> tuple[str, str]:
    """Generate, check structure, retry once uncached. Returns (markdown, 'provider:model')."""
    for attempt in range(2):
        resp = await gw.chat(request, role="generator", use_cache=attempt == 0)
        text = clean_markdown(resp.text)
        if not missing_sections(text, sections):
            return text, f"{resp.provider}:{resp.model}"
    raise GenerationError(f"model output missing sections {missing_sections(text, sections)}")


# --------------------------------------------------------------- corpus build


def _record(doc_id: str, title: str, doc_type: str, tags: list[str], rel: str, size: int, digest: str,
            model: str | None = None) -> ManifestRecord:
    return ManifestRecord(
        doc_id=doc_id, source_id="kestrel-synthetic", title=title, license=SYNTHETIC_LICENSE,
        redistributable=True, synthetic=True, doc_type=doc_type, tags=tags, path=rel,
        content_type="text/markdown", sha256=digest, bytes=size, status="ok",
        retrieved_at=now_iso(), generator_model=model,
    )


def build_deterministic(world: World, manifest: Manifest, out_dir: Path = SYNTH_CORPUS_DIR) -> int:
    count = 0
    for svc in world.services:
        rel, size, digest = store_document(render_service(world, svc).encode(), out_dir / "services" / f"{svc.id}.md")
        manifest.upsert(_record(service_doc_id(svc.id), f"Service: {svc.id}", "service", [svc.id], rel, size, digest))
        count += 1
    for ch in world.changes:
        rel, size, digest = store_document(render_change(ch).encode(), out_dir / "changes" / f"{ch.id}.md")
        manifest.upsert(_record(change_doc_id(ch.id), f"{ch.id}: {ch.summary}", "change",
                                [ch.service, ch.kind], rel, size, digest))
        count += 1
    return count


async def build_prose(world: World, gw: ModelGateway, manifest: Manifest, *, refresh: bool = False,
                      out_dir: Path = SYNTH_CORPUS_DIR, log=print) -> int:
    jobs: list[tuple[str, str, str, list[str], ChatRequest, list[str], Path]] = []
    for svc in world.services:
        jobs.append((runbook_doc_id(svc.id), f"Runbook: {svc.id}", "runbook", [svc.id],
                     runbook_prompt(world, svc), RUNBOOK_SECTIONS, out_dir / "runbooks" / f"{svc.id}.md"))
    for inc in world.history:
        jobs.append((postmortem_doc_id(inc.id), f"{inc.id}: {inc.title}", "postmortem", [inc.service, inc.severity],
                     postmortem_prompt(world, inc), POSTMORTEM_SECTIONS, out_dir / "postmortems" / f"{inc.id}.md"))
    written = 0
    for doc_id, title, doc_type, tags, request, sections, dest in jobs:
        if not refresh and manifest.is_current(doc_id):
            continue
        log(f"  writing {doc_id} ...")
        text, model = await _write_prose(gw, request, sections)
        rel, size, digest = store_document((BANNER + text).encode(), dest)
        manifest.upsert(_record(doc_id, title, doc_type, tags, rel, size, digest, model))
        manifest.save()  # save after each doc: a rate-limit stop loses nothing
        written += 1
    return written


# ------------------------------------------------------------------ eval files


def render_incident(scenario) -> str:
    inc = scenario.incident
    return (
        f"# {scenario.id}: {scenario.title}\n\n_Synthetic incident report (Kestrel Commerce is fictional)._\n\n"
        f"**Reported at:** {scenario.reported_at}\n\n## Reporter note\n\n{inc.reporter_note.strip()}\n\n"
        f"## Alerts\n\n{_bullets(inc.alerts)}\n\n## Logs\n\n```\n" + "\n".join(inc.logs) + "\n```\n"
    )


def write_eval_files(evalset: EvalSet, incidents_dir: Path = EVAL_INCIDENTS_DIR,
                     labels_path: Path = EVAL_LABELS_PATH) -> int:
    """Incidents (the questions) and labels (the answers), both under data/eval."""
    incidents_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for s in evalset.scenarios:
        (incidents_dir / f"{s.id}.md").write_text(render_incident(s), encoding="utf-8")
        lines.append(json.dumps({"id": s.id, "split": s.split, "answerable": s.answerable, "reasoning": s.reasoning,
                                 "tags": s.tags, **s.label.model_dump()}, ensure_ascii=False))
    labels_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


# --------------------------------------------------------------- leakage check


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_.\-]+", text.lower())


def _ngrams(words: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def find_leaks(corpus_texts: dict[str, str], evalset: EvalSet, ngram: int = 12) -> list[str]:
    """Report any corpus document that contains an eval answer or an eval id.

    An answer 'leaks' if a run of `ngram` consecutive words from a label's
    root_cause also appears in a corpus document. Evidence (a change record) is
    allowed; the written-out conclusion is not.
    """
    problems = []
    for doc_id, text in corpus_texts.items():
        words = _words(text)
        doc_grams = _ngrams(words, ngram)
        if any(re.fullmatch(r"eval-\d{3}", w) for w in words):
            problems.append(f"{doc_id}: mentions an EVAL id")
        for s in evalset.scenarios:
            if s.label.root_cause and _ngrams(_words(s.label.root_cause), ngram) & doc_grams:
                problems.append(f"{doc_id}: contains text from the answer to {s.id}")
    return problems
