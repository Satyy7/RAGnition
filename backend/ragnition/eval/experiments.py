"""Experiment grid (config/experiments.yaml) and the chosen retrieval setup (config/retrieval.yaml)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from ragnition.core.config import PROJECT_ROOT
from ragnition.ingest.chunking import ChunkConfig, Strategy
from ragnition.ingest.tokens import MODEL_MAX_TOKENS

EXPERIMENTS_PATH = PROJECT_ROOT / "config" / "experiments.yaml"
RETRIEVAL_PATH = PROJECT_ROOT / "config" / "retrieval.yaml"


class ExperimentConfig(BaseModel):
    id: str = Field(pattern=r"^[a-z_]+-\d+$")
    strategy: Strategy
    size: int = Field(default=384, ge=32, le=MODEL_MAX_TOKENS)
    overlap: int = Field(default=64, ge=0)
    child_size: int = Field(default=256, ge=32, le=MODEL_MAX_TOKENS)
    parent_size: int = Field(default=1024, ge=64)

    @model_validator(mode="after")
    def _overlap_smaller_than_size(self) -> ExperimentConfig:
        if self.overlap >= self.size:
            raise ValueError(f"{self.id}: overlap must be smaller than size")
        return self

    def chunk_config(self) -> ChunkConfig:
        return ChunkConfig(size=self.size, overlap=self.overlap, child_size=self.child_size,
                           parent_size=self.parent_size)


class Bootstrap(BaseModel):
    resamples: int = Field(default=2000, ge=100)
    seed: int = 7


class Experiments(BaseModel):
    configs: list[ExperimentConfig]
    query_instruction: str
    bootstrap: Bootstrap = Field(default_factory=Bootstrap)

    @model_validator(mode="after")
    def _unique(self) -> Experiments:
        ids = [c.id for c in self.configs]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate experiment ids")
        return self

    def get(self, config_id: str) -> ExperimentConfig:
        for c in self.configs:
            if c.id == config_id:
                return c
        raise KeyError(f"unknown config '{config_id}'; known: {[c.id for c in self.configs]}")


class RetrievalSetup(BaseModel):
    """What later milestones use. Written by the benchmark with --apply."""

    config: str
    query_instruction: bool
    chosen_by: str = "default"


def load_experiments(path: Path = EXPERIMENTS_PATH) -> Experiments:
    with path.open(encoding="utf-8") as fh:
        return Experiments.model_validate(yaml.safe_load(fh))


def load_retrieval_setup(path: Path = RETRIEVAL_PATH) -> RetrievalSetup:
    if not path.exists():
        return RetrievalSetup(config="structure-384", query_instruction=False)
    with path.open(encoding="utf-8") as fh:
        return RetrievalSetup.model_validate(yaml.safe_load(fh))


def save_retrieval_setup(setup: RetrievalSetup, path: Path = RETRIEVAL_PATH) -> None:
    header = "# Written by scripts/run_retrieval_benchmark.py --apply. See data/eval/results/.\n"
    path.write_text(header + yaml.safe_dump(setup.model_dump(), sort_keys=False), encoding="utf-8")
