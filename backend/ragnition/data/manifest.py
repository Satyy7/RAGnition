"""The manifest: one JSON line per corpus document, recording where it came from.

Every citation RAGnition shows later traces back to a row here: source URL,
license, whether it may be redistributed, whether it is synthetic, and a SHA-256
of the exact bytes stored, so a changed document is detectable.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from ragnition.core.config import PROJECT_ROOT


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class ManifestRecord(BaseModel):
    doc_id: str  # stable, e.g. "k8s-docs/debug-pods" or "kestrel/postmortem/PM-2025-014"
    source_id: str
    title: str
    url: str | None = None
    license: str
    redistributable: bool
    attribution: str | None = None
    synthetic: bool
    doc_type: str  # documentation | book_chapter | postmortem | runbook | service | change
    tags: list[str] = []  # e.g. services or failure category, used for filtering later
    path: str | None = None  # relative to the project root, forward slashes
    content_type: str | None = None
    sha256: str | None = None
    bytes: int | None = None
    status: Literal["ok", "failed", "skipped"]
    error: str | None = None
    retrieved_at: str
    generator_model: str | None = None  # synthetic only: which LLM wrote the prose


class Manifest:
    """In-memory view of data/manifest.jsonl, keyed by doc_id (last write wins)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.records: dict[str, ManifestRecord] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = ManifestRecord.model_validate_json(line)
                    self.records[rec.doc_id] = rec

    def upsert(self, record: ManifestRecord) -> None:
        self.records[record.doc_id] = record

    def is_current(self, doc_id: str) -> bool:
        """True if the document was stored OK and its file is still there unchanged."""
        rec = self.records.get(doc_id)
        if rec is None or rec.status != "ok" or rec.path is None:
            return False
        file = PROJECT_ROOT / rec.path
        return file.exists() and sha256_bytes(file.read_bytes()) == rec.sha256

    def ok_records(self) -> list[ManifestRecord]:
        return [r for r in self.records.values() if r.status == "ok"]

    def save(self) -> None:
        """Write atomically (temp file + rename) so a crash never leaves half a manifest."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".jsonl.tmp")
        lines = [json.dumps(r.model_dump(exclude_none=True), ensure_ascii=False) for r in
                 sorted(self.records.values(), key=lambda r: r.doc_id)]
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)


def store_document(content: bytes, dest: Path) -> tuple[str, int, str]:
    """Write bytes to `dest`; return (relative path, size, sha256)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    rel = dest.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    return rel, len(content), sha256_bytes(content)
