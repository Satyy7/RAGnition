"""M6: held-out test set integrity (labels valid, graded relevance, frozen hash)."""

import pytest

from ragnition.data.paths import EVAL_SCENARIOS_PATH, EVAL_TEST_SCENARIOS_PATH, WORLD_PATH
from ragnition.data.world import EvalScenario, load_all_eval, load_world
from ragnition.eval.freeze import FrozenSetError, freeze, verify


@pytest.fixture(scope="module")
def world():
    return load_world(WORLD_PATH)


@pytest.fixture(scope="module")
def evalset(world):
    es = load_all_eval(EVAL_SCENARIOS_PATH, EVAL_TEST_SCENARIOS_PATH)
    es.validate_against(world)  # supporting docs exist, culprit changes dated before the incident
    return es


def test_splits_are_disjoint_and_sized(evalset):
    dev, test = evalset.split("dev"), evalset.split("test")
    assert len(dev) == 12 and len(test) == 30
    assert not {s.id for s in dev} & {s.id for s in test}
    assert sum(s.answerable for s in test) == 26


def test_every_changed_caused_incident_names_its_change(evalset):
    for s in evalset.scenarios:
        if s.answerable and "no-change" not in s.tags:
            assert s.label.root_cause_change, s.id


def test_test_set_covers_the_hard_cases(evalset):
    tags = [t for s in evalset.split("test") for t in s.tags]
    for tag, minimum in (("hop-1", 8), ("recent-distractor", 12), ("lexical-distractor", 4),
                         ("old-culprit", 2), ("shared-infra", 3), ("culprit-unnamed", 2)):
        assert tags.count(tag) >= minimum, tag


def test_graded_relevance(evalset):
    s = next(s for s in evalset.scenarios if s.id == "EVAL-101")
    grades = s.label.relevance()
    assert grades["kestrel/change/CHG-2351"] == 3
    assert grades["kestrel/postmortem/PM-2026-009"] == 2
    assert grades["kestrel/service/auth-service"] == 1


def test_root_cause_change_must_be_a_supporting_doc(evalset):
    data = next(s for s in evalset.scenarios if s.id == "EVAL-101").model_dump()
    data["label"]["root_cause_change"] = "CHG-2352"
    with pytest.raises(ValueError, match="root_cause_change"):
        EvalScenario.model_validate(data)


def test_culprit_change_after_incident_is_rejected(world, evalset):
    broken = evalset.model_copy(deep=True)
    s = next(s for s in broken.scenarios if s.id == "EVAL-101")
    s.reported_at = "2026-11-01T00:00:00Z"
    with pytest.raises(ValueError, match="after the incident"):
        broken.validate_against(world)


def test_freeze_detects_any_edit(tmp_path):
    f, lock = tmp_path / "t.yaml", tmp_path / "t.lock"
    f.write_bytes(b"scenarios: []\r\n")
    digest = freeze("now", f, lock)
    f.write_bytes(b"scenarios: []\n")  # line endings alone must not matter
    assert verify(f, lock) == digest
    with pytest.raises(FrozenSetError):
        freeze("again", f, lock)
    f.write_text("scenarios: [x]\n")
    with pytest.raises(FrozenSetError, match="changed"):
        verify(f, lock)
