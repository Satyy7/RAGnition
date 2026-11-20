"""Public source registry (data/sources.yaml), validated at load time."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class SourceSpec(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    title: str
    kind: Literal["github_dir", "html_pages", "link_index"]
    doc_type: str
    license: str
    license_evidence: str  # where/when the license was checked
    redistributable: bool
    attribution: str | None = None
    max_docs: int = Field(default=50, ge=1)
    # github_dir
    repo: str | None = None
    ref: str = "main"
    dirs: list[str] = []
    # html_pages
    urls: list[str] = []
    # link_index
    index_url: str | None = None

    @model_validator(mode="after")
    def _fields_for_kind(self) -> SourceSpec:
        need = {"github_dir": self.repo and self.dirs, "html_pages": self.urls, "link_index": self.index_url}
        if not need[self.kind]:
            raise ValueError(f"source '{self.id}' of kind {self.kind} is missing its location fields")
        return self


class SourcesConfig(BaseModel):
    sources: list[SourceSpec]

    @model_validator(mode="after")
    def _unique_ids(self) -> SourcesConfig:
        ids = [s.id for s in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate source ids")
        return self


def load_sources(path: Path) -> SourcesConfig:
    with path.open(encoding="utf-8") as fh:
        return SourcesConfig.model_validate(yaml.safe_load(fh))
