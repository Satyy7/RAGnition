"""Parsing and chunking with a one-token-per-word counter (no model needed)."""

import pytest

from ragnition.data.paths import EVAL_LABELS_PATH, LeakageError, ensure_corpus_path
from ragnition.ingest.chunking import ChunkConfig, DocMeta, chunk_document, merge_small_sections
from ragnition.ingest.parse import Section, clean_markdown, parse_markdown, split_sections
from ragnition.ingest.pipeline import dedup
from ragnition.ingest.tokens import whitespace_counter as count

META = DocMeta("t/doc", "Doc", "t", "postmortem", ["svc"], None, "CC-BY-4.0", True, False)

HUGO = """---
title: Debug Pods
weight: 10
---
<!-- internal note -->
{{< note >}}
Check events first.
{{< /note >}}

## Examine pod logs

```shell
# not a heading
kubectl logs mypod
```
"""


def test_markdown_cleanup_and_code_fence_headings():
    md = clean_markdown(HUGO)
    assert "weight" not in md and "internal note" not in md and "{{" not in md
    assert "Note:" in md
    sections = split_sections(md)
    assert [s.path for s in sections] == [(), ("Examine pod logs",)]
    assert "# not a heading" in sections[1].text


def test_single_h1_becomes_title_and_synthetic_banner_is_removed():
    doc = parse_markdown("> SYNTHETIC DOCUMENT. fictional\n\n# PM-1: Outage\n\n## Root cause\n\nPool too small.", "x")
    assert doc.title == "PM-1: Outage"
    assert doc.sections[0].path == ("Root cause",)
    assert "SYNTHETIC" not in doc.sections[0].text


def test_tiny_sections_merge_into_next():
    secs = [Section(("A",), "short"), Section(("B",), "word " * 60)]
    merged = merge_small_sections(secs, count, 48)
    assert len(merged) == 1 and merged[0].text.startswith("A:\nshort")


def _long_doc():
    para = " ".join(f"w{i}" for i in range(90)) + "."
    return parse_markdown("# T\n\n## Root cause\n\n" + "\n\n".join([para] * 12) + "\n\n## Resolution\n\nFixed it " + "x " * 60, "T")


@pytest.mark.parametrize("strategy", ["fixed", "structure", "parent_child"])
def test_chunks_respect_budgets_and_are_deterministic(strategy):
    cfg = ChunkConfig(size=200, overlap=30, child_size=100, parent_size=400)
    a = chunk_document(_long_doc(), META, strategy, count, cfg)
    b = chunk_document(_long_doc(), META, strategy, count, cfg)
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]
    for c in a:
        limit = {"chunk": cfg.size, "child": cfg.child_size, "parent": cfg.parent_size}[c.role]
        assert c.n_tokens <= limit
        assert c.text.startswith(c.header)
        assert c.license == "CC-BY-4.0" and c.doc_type == "postmortem"


def test_fixed_windows_overlap():
    cfg = ChunkConfig(size=101, overlap=20)  # header "T" = 1 token -> 100-word windows
    chunks = chunk_document(_long_doc(), META, "fixed", count, cfg)
    first, second = chunks[0].body.split(), chunks[1].body.split()
    assert first[-20:] == second[:20]


def test_structure_chunks_carry_section_header():
    chunks = chunk_document(_long_doc(), META, "structure", count, ChunkConfig(size=200))
    # Headers use the pipeline's title (META.title), not the H1 of the test text.
    assert chunks[0].header == "Doc > Root cause"
    assert chunks[-1].header == "Doc > Resolution"


def test_merged_sections_name_every_part():
    secs = [Section(("Impact",), "18% failed."), Section(("Detection",), "word " * 60)]
    merged = merge_small_sections(secs, count, 48)
    assert merged[0].path == ("Impact / Detection",)
    assert merged[0].text.startswith("Impact:\n18% failed.\n\nDetection:\n")


def test_small_document_stays_one_chunk():
    doc = parse_markdown("# CHG-1: Pool lowered\n\n- **Service:** payments-api\n\n## Details\n\nFrom 60 to 25.", "x")
    chunks = chunk_document(doc, META, "structure", count, ChunkConfig(size=200))
    assert len(chunks) == 1
    assert "payments-api" in chunks[0].body and "From 60 to 25." in chunks[0].body


def test_lookalike_characters_are_normalised():
    assert clean_markdown("payments‑api failed for 18 % “now”") == 'payments-api failed for 18 % "now"'


def test_children_point_at_existing_parents():
    chunks = chunk_document(_long_doc(), META, "parent_child", count, ChunkConfig(child_size=100, parent_size=400))
    parents = {c.chunk_id for c in chunks if c.role == "parent"}
    children = [c for c in chunks if c.role == "child"]
    assert parents and children and all(c.parent_id in parents for c in children)


def test_pathological_word_is_cut_up():
    doc = parse_markdown("# T\n\n## S\n\n" + "a" * 5000, "T")
    def char_count(t):  # make the long word expensive, like real minified junk
        return sum(max(1, len(w) // 4) for w in t.split())
    chunks = chunk_document(doc, META, "structure", char_count, ChunkConfig(size=128))
    assert all(c.n_tokens <= 128 for c in chunks)


def test_dedup_drops_repeated_bodies_but_keeps_parents():
    cs = chunk_document(_long_doc(), META, "structure", count, ChunkConfig(size=200))
    copy = [c.model_copy(update={"doc_id": "t/copy", "chunk_id": c.chunk_id + "x"}) for c in cs]
    kept, dropped = dedup(cs + copy)
    assert dropped >= len(cs) - 1 and len(kept) <= len(cs) + 1


def test_ingestion_guard_refuses_eval_files():
    with pytest.raises(LeakageError):
        ensure_corpus_path(EVAL_LABELS_PATH)
