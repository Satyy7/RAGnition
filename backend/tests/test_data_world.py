"""The shipped Kestrel world and eval set are consistent, and answers cannot leak."""

import pytest

from ragnition.data.manifest import Manifest
from ragnition.data.paths import (
    EVAL_LABELS_PATH,
    EVAL_SCENARIOS_PATH,
    RAW_DIR,
    SYNTH_CORPUS_DIR,
    WORLD_PATH,
    LeakageError,
    ensure_corpus_path,
)
from ragnition.data.synthetic import (
    POSTMORTEM_SECTIONS,
    build_deterministic,
    clean_markdown,
    find_leaks,
    missing_sections,
    render_change,
    render_service,
    write_eval_files,
)
from ragnition.data.world import EvalSet, World, load_eval_set, load_world


@pytest.fixture(scope="module")
def world() -> World:
    return load_world(WORLD_PATH)


@pytest.fixture(scope="module")
def evalset(world) -> EvalSet:
    es = load_eval_set(EVAL_SCENARIOS_PATH)
    es.validate_against(world)  # every supporting doc id exists
    return es


def test_eval_set_mix(evalset):
    kinds = [s.reasoning for s in evalset.scenarios]
    assert len(kinds) >= 12
    assert kinds.count("multi-hop") >= 3
    assert kinds.count("unanswerable") >= 2


def test_eval_label_pointing_at_unknown_doc_is_rejected(world, evalset):
    broken = evalset.model_copy(deep=True)
    broken.scenarios[0].label.supporting_doc_ids.append("kestrel/change/CHG-9999")
    with pytest.raises(ValueError, match="not in corpus"):
        broken.validate_against(world)


def test_unanswerable_must_not_carry_an_answer(evalset):
    raw = evalset.scenarios[-1].model_dump()
    raw["label"]["root_cause"] = "something"
    with pytest.raises(ValueError, match="must not carry an answer"):
        type(evalset.scenarios[-1]).model_validate(raw)


def test_deterministic_world_docs_do_not_leak_answers(world, evalset):
    texts = {f"service/{s.id}": render_service(world, s) for s in world.services}
    texts |= {f"change/{c.id}": render_change(c) for c in world.changes}
    assert find_leaks(texts, evalset) == []


def test_leak_detector_catches_a_copied_answer(evalset):
    answer = evalset.scenarios[0].label.root_cause
    assert find_leaks({"doc": f"Some intro. {answer} More text."}, evalset)
    assert find_leaks({"doc": "see EVAL-001 for details"}, evalset)


def test_eval_files_are_refused_by_the_corpus_guard(tmp_path):
    with pytest.raises(LeakageError):
        ensure_corpus_path(EVAL_LABELS_PATH)
    with pytest.raises(LeakageError):
        ensure_corpus_path(EVAL_SCENARIOS_PATH)
    with pytest.raises(LeakageError):
        ensure_corpus_path(tmp_path / "elsewhere.md")
    assert ensure_corpus_path(RAW_DIR / "k8s-docs" / "x.md")
    assert ensure_corpus_path(SYNTH_CORPUS_DIR / "services" / "x.md")


def test_build_deterministic_records_synthetic_docs(world, tmp_path, monkeypatch):
    import ragnition.data.manifest as manifest_mod

    monkeypatch.setattr(manifest_mod, "PROJECT_ROOT", tmp_path)  # paths relative to tmp
    manifest = Manifest(tmp_path / "manifest.jsonl")
    n = build_deterministic(world, manifest, out_dir=tmp_path / "corpus")
    assert n == len(world.services) + len(world.changes)
    rec = manifest.records["kestrel/service/payments-api"]
    assert rec.synthetic and rec.redistributable and rec.status == "ok"
    assert "SYNTHETIC DOCUMENT" in (tmp_path / rec.path).read_text(encoding="utf-8")
    assert manifest.is_current("kestrel/service/payments-api")


def test_service_page_shows_reverse_dependencies(world):
    page = render_service(world, world.service("inventory-api"))
    assert "checkout-api" in page.split("Called by:")[1]


def test_incident_files_contain_no_labels(evalset, tmp_path):
    write_eval_files(evalset, tmp_path / "incidents", tmp_path / "labels.jsonl")
    for s in evalset.scenarios:
        text = (tmp_path / "incidents" / f"{s.id}.md").read_text(encoding="utf-8")
        # Incident files are eval files, so their own EVAL id is expected there;
        # what must never appear is the answer text.
        if s.label.root_cause:
            leaks = find_leaks({s.id: text}, EvalSet(scenarios=[s]))
            assert [p for p in leaks if "answer" in p] == []


def test_section_check_and_fence_stripping():
    md = clean_markdown("```markdown\n# T\n" + "\n".join(f"## {s}\ntext" for s in POSTMORTEM_SECTIONS) + "\n```")
    assert md.startswith("# T")
    assert missing_sections(md, POSTMORTEM_SECTIONS) == []
    assert missing_sections("# T\n## Summary\n", POSTMORTEM_SECTIONS)[0] == "Impact"
