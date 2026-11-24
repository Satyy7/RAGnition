"""Three chunking strategies, all measured in real model tokens.

fixed         -> sliding window of `size` tokens over the whole document with
                 `overlap` tokens repeated between neighbours. Ignores structure.
structure     -> one or more chunks per heading section; paragraphs are packed
                 together and only split (sentence, then word) when too long.
                 Tiny sections are merged into the next one.
parent_child  -> structure chunks of up to `parent_size` tokens are PARENTS (what
                 the LLM reads); each is cut into CHILDREN of `child_size` tokens
                 (what the search matches). Children point at their parent.

Every chunk's embedded text starts with a context header built from the
document title and heading path, e.g. "PM-2025-007: ... > Resolution".
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from ragnition.ingest.parse import ParsedDoc, Section
from ragnition.ingest.tokens import TokenCounter

Strategy = Literal["fixed", "structure", "parent_child"]
STRATEGIES: tuple[Strategy, ...] = ("fixed", "structure", "parent_child")
HEADER_SEP = " > "


@dataclass(frozen=True)
class ChunkConfig:
    size: int = 384           # fixed + structure: max tokens per chunk, header included
    overlap: int = 64         # fixed only
    child_size: int = 256     # parent_child: searched unit
    parent_size: int = 1024   # parent_child: unit given to the LLM (never embedded)
    min_section_tokens: int = 48  # smaller sections are merged into the next one
    max_word_tokens: int = 64     # longer "words" (hashes, minified junk) are cut up


class Chunk(BaseModel):
    chunk_id: str
    doc_id: str
    strategy: Strategy
    role: Literal["chunk", "parent", "child"]
    parent_id: str | None = None
    position: int
    header: str
    body: str
    text: str                  # header + body: what gets embedded
    n_tokens: int              # tokens of `text`
    section_path: list[str]
    # copied from the manifest so every search hit can be cited and filtered
    title: str
    source_id: str
    doc_type: str
    tags: list[str]
    url: str | None
    license: str
    redistributable: bool
    synthetic: bool


@dataclass
class DocMeta:
    doc_id: str
    title: str
    source_id: str
    doc_type: str
    tags: list[str]
    url: str | None
    license: str
    redistributable: bool
    synthetic: bool


# ------------------------------------------------------------------ helpers


def chunk_id(strategy: str, doc_id: str, position: int, body: str) -> str:
    return hashlib.sha256(f"{strategy}|{doc_id}|{position}|{body}".encode()).hexdigest()[:16]


def make_header(title: str, path: tuple[str, ...] | list[str]) -> str:
    return HEADER_SEP.join([title, *path])


def _safe_words(text: str, count: TokenCounter, max_word_tokens: int) -> list[str]:
    """Whitespace words, with pathological 'words' cut into 40-char pieces."""
    out: list[str] = []
    for w in text.split():
        if count(w) > max_word_tokens:
            out.extend(w[i : i + 40] for i in range(0, len(w), 40))
        else:
            out.append(w)
    return out


def _pack_words(words: list[str], budget: int, count: TokenCounter) -> list[str]:
    pieces, cur, tot = [], [], 0
    for w in words:
        c = count(w)
        if cur and tot + c > budget:
            pieces.append(" ".join(cur))
            cur, tot = [], 0
        cur.append(w)
        tot += c
    if cur:
        pieces.append(" ".join(cur))
    return pieces


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`\"'(\[])")


def _paragraphs(text: str) -> list[str]:
    """Blank-line separated blocks; a fenced code block stays one block."""
    blocks, cur, in_code = [], [], False
    for line in text.split("\n"):
        if line.strip().startswith(("```", "~~~")):
            in_code = not in_code
        if not line.strip() and not in_code:
            if cur:
                blocks.append("\n".join(cur))
                cur = []
        else:
            cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    return blocks


def split_units(text: str, budget: int, count: TokenCounter, cfg: ChunkConfig) -> list[str]:
    """Paragraphs, each guaranteed to fit `budget` (split by sentence, then by word)."""
    units: list[str] = []
    for para in _paragraphs(text):
        if count(para) <= budget:
            units.append(para)
            continue
        for sent in _SENTENCE_END.split(para):
            if count(sent) <= budget:
                units.append(sent)
            else:
                units.extend(_pack_words(_safe_words(sent, count, cfg.max_word_tokens), budget, count))
    return units


def pack_units(units: list[str], budget: int, count: TokenCounter) -> list[str]:
    """Greedily join units (with blank lines) while the total fits `budget`."""
    out, cur, tot = [], [], 0
    for u in units:
        c = count(u)
        if cur and tot + c > budget:
            out.append("\n\n".join(cur))
            cur, tot = [], 0
        cur.append(u)
        tot += c
    if cur:
        out.append("\n\n".join(cur))
    return out


def merge_small_sections(sections: list[Section], count: TokenCounter, min_tokens: int) -> list[Section]:
    """Fold tiny sections into the following one.

    The merged section's last path element names every section it contains
    ("Impact / Detection"), so the header never hides where text came from.
    """
    merged: list[Section] = []
    carry_parts: list[str] = []  # earlier tiny sections, each with its heading inline
    carry_names: list[str] = []
    for i, s in enumerate(sections):
        name = s.path[-1] if s.path else ""
        inline = f"{name}:\n{s.text}" if name else s.text
        if count("\n\n".join([*carry_parts, s.text])) < min_tokens and i < len(sections) - 1:
            carry_parts.append(inline)
            carry_names += [name] if name else []
            continue
        if carry_parts:
            text = "\n\n".join([*carry_parts, inline])
            names = [*carry_names, name] if name else carry_names
            path = (*s.path[:-1], " / ".join(names)) if names else s.path
        else:
            text, path = s.text, s.path
        merged.append(Section(path, text))
        carry_parts, carry_names = [], []
    return merged


def render_sections(sections: list[Section]) -> str:
    return "\n\n".join((f"{s.path[-1]}:\n" if s.path else "") + s.text for s in sections)


# --------------------------------------------------------------- strategies


def _chunk(meta: DocMeta, strategy: Strategy, role, position, header, body, count, parent_id=None, path=()) -> Chunk:
    text = f"{header}\n\n{body}"
    return Chunk(
        chunk_id=chunk_id(f"{strategy}:{role}", meta.doc_id, position, body), doc_id=meta.doc_id,
        strategy=strategy, role=role, parent_id=parent_id, position=position, header=header, body=body,
        text=text, n_tokens=count(text), section_path=list(path), title=meta.title, source_id=meta.source_id,
        doc_type=meta.doc_type, tags=meta.tags, url=meta.url, license=meta.license,
        redistributable=meta.redistributable, synthetic=meta.synthetic,
    )


def fixed_chunks(doc: ParsedDoc, meta: DocMeta, count: TokenCounter, cfg: ChunkConfig) -> list[Chunk]:
    header = make_header(meta.title, ())
    budget = cfg.size - count(header)
    full = "\n\n".join(("## " + s.path[-1] + "\n" if s.path else "") + s.text for s in doc.sections)
    words = _safe_words(full, count, cfg.max_word_tokens)
    costs = [count(w) for w in words]
    chunks, i, n = [], 0, len(words)
    while i < n:
        j, tot = i, 0
        while j < n and tot + costs[j] <= budget:
            tot += costs[j]
            j += 1
        j = max(j, i + 1)
        chunks.append(_chunk(meta, "fixed", "chunk", len(chunks), header, " ".join(words[i:j]), count))
        if j >= n:
            break
        k, ov = j, 0  # step back so the next window repeats ~overlap tokens
        while k - 1 > i and ov + costs[k - 1] <= cfg.overlap:
            k -= 1
            ov += costs[k]
        i = k
    return chunks


def structure_chunks(doc: ParsedDoc, meta: DocMeta, count: TokenCounter, cfg: ChunkConfig,
                     size: int | None = None, strategy: Strategy = "structure", role="chunk") -> list[Chunk]:
    size = size or cfg.size
    # A document that fits in one chunk stays whole: a change record split into
    # "date/service" and "details" chunks would lose the link between them.
    whole = render_sections(doc.sections)
    title_header = make_header(meta.title, ())
    if count(title_header) + count(whole) <= size:
        return [_chunk(meta, strategy, role, 0, title_header, whole, count)]
    chunks: list[Chunk] = []
    for section in merge_small_sections(doc.sections, count, cfg.min_section_tokens):
        header = make_header(meta.title, section.path)
        budget = max(size - count(header), 32)
        for body in pack_units(split_units(section.text, budget, count, cfg), budget, count):
            chunks.append(_chunk(meta, strategy, role, len(chunks), header, body, count, path=section.path))
    return chunks


def parent_child_chunks(doc: ParsedDoc, meta: DocMeta, count: TokenCounter, cfg: ChunkConfig) -> list[Chunk]:
    parents = structure_chunks(doc, meta, count, cfg, size=cfg.parent_size, strategy="parent_child", role="parent")
    out: list[Chunk] = []
    position = 0
    for parent in parents:
        out.append(parent)
        budget = cfg.child_size - count(parent.header)
        for body in pack_units(split_units(parent.body, budget, count, cfg), budget, count):
            out.append(_chunk(meta, "parent_child", "child", position, parent.header, body, count,
                              parent_id=parent.chunk_id, path=parent.section_path))
            position += 1
    return out


def chunk_document(doc: ParsedDoc, meta: DocMeta, strategy: Strategy, count: TokenCounter,
                   cfg: ChunkConfig = ChunkConfig()) -> list[Chunk]:
    if strategy == "fixed":
        return fixed_chunks(doc, meta, count, cfg)
    if strategy == "structure":
        return structure_chunks(doc, meta, count, cfg)
    return parent_child_chunks(doc, meta, count, cfg)
