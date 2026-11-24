"""Manifest -> leakage guard -> parse -> chunk -> dedup -> data/processed/chunks.<strategy>.jsonl"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from ragnition.core.config import PROJECT_ROOT
from ragnition.data.manifest import Manifest
from ragnition.data.paths import DATA_DIR, ensure_corpus_path
from ragnition.ingest.chunking import Chunk, ChunkConfig, DocMeta, Strategy, chunk_document
from ragnition.ingest.parse import ParsedDoc, parse_document
from ragnition.ingest.tokens import MODEL_MAX_TOKENS, TokenCounter

PROCESSED_DIR = DATA_DIR / "processed"
MIN_PUBLIC_WORDS = 150  # below this a fetched page is usually an error/login/consent page


def chunks_path(strategy: Strategy, out_dir: Path = PROCESSED_DIR) -> Path:
    return out_dir / f"chunks.{strategy}.jsonl"


@dataclass
class ParseResult:
    docs: list[tuple[DocMeta, ParsedDoc]] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # doc_id -> reason


def parse_corpus(manifest: Manifest) -> ParseResult:
    result = ParseResult()
    for rec in sorted(manifest.ok_records(), key=lambda r: r.doc_id):
        path = ensure_corpus_path(PROJECT_ROOT / rec.path)  # raises on anything under data/eval
        try:
            doc = parse_document(path.read_bytes(), path.suffix.lower(), rec.title)
        except Exception as exc:  # one broken page must not stop the corpus
            result.skipped[rec.doc_id] = f"parse error: {type(exc).__name__}: {exc}"
            continue
        if not doc.sections:
            result.skipped[rec.doc_id] = "no main content extracted"
            continue
        if not rec.synthetic and doc.word_count < MIN_PUBLIC_WORDS:
            result.skipped[rec.doc_id] = f"only {doc.word_count} words (likely not an article)"
            continue
        # Prefer the title found in the document; keep the manifest's id/source/license.
        meta = DocMeta(rec.doc_id, doc.title or rec.title, rec.source_id, rec.doc_type, rec.tags, rec.url,
                       rec.license, rec.redistributable, rec.synthetic)
        result.docs.append((meta, doc))
    return result


def _norm_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text.lower()).strip().encode()).hexdigest()


def dedup(chunks: list[Chunk]) -> tuple[list[Chunk], int]:
    """Drop embedded chunks whose body repeats one seen earlier. Parents are kept."""
    seen: set[str] = set()
    kept, dropped = [], 0
    for c in chunks:
        if c.role == "parent":
            kept.append(c)
            continue
        h = _norm_hash(c.body)
        if h in seen:
            dropped += 1
            continue
        seen.add(h)
        kept.append(c)
    return kept, dropped


@dataclass
class ChunkReport:
    strategy: Strategy
    docs: int
    chunks: int
    parents: int
    duplicates_dropped: int
    token_min: int
    token_median: float
    token_p95: int
    token_max: int
    over_limit: list[str]
    by_doc_type: dict[str, int]


def run_strategy(parsed: ParseResult, strategy: Strategy, count: TokenCounter,
                 cfg: ChunkConfig = ChunkConfig(), out_dir: Path = PROCESSED_DIR) -> ChunkReport:
    all_chunks: list[Chunk] = []
    for meta, doc in parsed.docs:
        all_chunks.extend(chunk_document(doc, meta, strategy, count, cfg))
    chunks, dropped = dedup(all_chunks)

    out = chunks_path(strategy, out_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for c in chunks:
            fh.write(json.dumps(c.model_dump(), ensure_ascii=False) + "\n")
    tmp.replace(out)

    embedded = [c for c in chunks if c.role != "parent"]
    sizes = sorted(c.n_tokens for c in embedded) or [0]
    by_type: dict[str, int] = {}
    for c in embedded:
        by_type[c.doc_type] = by_type.get(c.doc_type, 0) + 1
    return ChunkReport(
        strategy=strategy, docs=len(parsed.docs), chunks=len(embedded),
        parents=sum(c.role == "parent" for c in chunks), duplicates_dropped=dropped,
        token_min=sizes[0], token_median=statistics.median(sizes),
        token_p95=sizes[min(len(sizes) - 1, int(0.95 * len(sizes)))], token_max=sizes[-1],
        over_limit=[c.chunk_id for c in embedded if c.n_tokens > MODEL_MAX_TOKENS],
        by_doc_type=dict(sorted(by_type.items())),
    )
