"""Where data lives, and the one rule that keeps evaluation honest.

    data/raw/<source>/...         fetched public documents        (gitignored)
    data/synthetic/world.yaml     Kestrel Commerce: services, history, changes (hand-written truth)
    data/synthetic/corpus/...     generated knowledge-base documents (committed)
    data/eval/scenarios.yaml      evaluation incidents + their answers   (NEVER ingested)
    data/eval/incidents/...       what the user would paste in: alerts, logs
    data/eval/labels.jsonl        ground-truth answers                    (NEVER ingested)
    data/manifest.jsonl           one row per corpus document (metadata only)
"""

from __future__ import annotations

from pathlib import Path

from ragnition.core.config import PROJECT_ROOT

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
SOURCES_PATH = DATA_DIR / "sources.yaml"
WORLD_PATH = DATA_DIR / "synthetic" / "world.yaml"
SYNTH_CORPUS_DIR = DATA_DIR / "synthetic" / "corpus"
EVAL_DIR = DATA_DIR / "eval"
EVAL_SCENARIOS_PATH = EVAL_DIR / "scenarios.yaml"            # dev set (seen while designing)
EVAL_TEST_SCENARIOS_PATH = EVAL_DIR / "scenarios_test.yaml"  # held-out test set (M6)
EVAL_TEST_LOCK_PATH = EVAL_DIR / "scenarios_test.lock"       # SHA-256 frozen before M6 scorer code
EVAL_INCIDENTS_DIR = EVAL_DIR / "incidents"
EVAL_LABELS_PATH = EVAL_DIR / "labels.jsonl"
MANIFEST_PATH = DATA_DIR / "manifest.jsonl"

# Only these trees may ever feed the search index.
CORPUS_ROOTS = (RAW_DIR, SYNTH_CORPUS_DIR)


class LeakageError(RuntimeError):
    """Raised when evaluation material is about to enter the corpus."""


def ensure_corpus_path(path: Path) -> Path:
    """Return `path` if it may be ingested; raise LeakageError otherwise.

    Ingestion (M3) calls this for every file. A file under data/eval, or anywhere
    outside the corpus roots, is refused, so answers can never be retrieved.
    """
    resolved = path.resolve()
    if resolved.is_relative_to(EVAL_DIR.resolve()):
        raise LeakageError(f"evaluation file must never be ingested: {path}")
    if not any(resolved.is_relative_to(root.resolve()) for root in CORPUS_ROOTS):
        raise LeakageError(f"not under a corpus root: {path}")
    return resolved
