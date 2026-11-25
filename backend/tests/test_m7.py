"""M7: query condensation, the three rerank strategies, and the selection rule."""

from ragnition.eval.m7 import select
from ragnition.retrieval.evidence import blend, condense_incident, naive, slot_preserving
from ragnition.retrieval.incident import RerankSetup, apply_rerank

C1, C2 = "kestrel/change/CHG-1", "kestrel/change/CHG-2"
PM, SVC, DOC = "kestrel/postmortem/PM-1", "kestrel/service/s", "k8s-docs/x"
ORDER = [C1, SVC, C2, DOC, PM]
# The reranker dislikes change records (vocabulary gap) and loves the postmortem.
SCORES = {C1: 0.1, SVC: 0.5, C2: 0.05, DOC: 0.3, PM: 0.9}


def test_naive_reranking_can_bury_the_root_cause_change():
    out = naive(ORDER, SCORES)
    assert out[0] == PM and out.index(C1) > 2


def test_slot_preserving_keeps_change_positions_and_reorders_the_rest():
    out = slot_preserving(ORDER, SCORES)
    assert out[0] == C1 and out[2] == C2  # changes untouched
    assert [out[1], out[3], out[4]] == [PM, SVC, DOC]  # others by reranker score
    assert sorted(out) == sorted(ORDER)


def test_blend_is_between_the_two():
    out = blend(ORDER, SCORES, weight=1.0)
    assert sorted(out) == sorted(ORDER)
    assert out.index(C1) <= naive(ORDER, SCORES).index(C1)


def test_depth_limits_what_the_reranker_may_touch():
    setup = RerankSetup(method="naive", depth=2)
    out = apply_rerank(ORDER, SCORES, setup)
    assert out[:2] == [SVC, C1] and out[2:] == ORDER[2:]


def test_condense_keeps_errors_first_and_dedups_volatile_lines():
    logs = ["2026-01-01T00:00:01Z svc INFO ok request 41ms",
            "2026-01-01T00:00:02Z svc ERROR timeout after 5s id=ab12cd34",
            "2026-01-01T00:00:03Z svc ERROR timeout after 5s id=ff99ee11"]
    q = condense_incident("Payments failing", "since 19:00", ["ErrorRateHigh"], logs)
    assert q.count("ERROR timeout") == 1
    assert q.index("ERROR") < q.index("INFO")
    assert len(condense_incident("t", "word " * 500, [], [], max_words=150).split()) == 150


def test_selection_prefers_local_unless_an_api_clearly_wins():
    assert select({"bge-local": 0.80, "jina-v3": 0.81, "cohere-3.5": 0.815}) == "bge-local"
    assert select({"bge-local": 0.80, "jina-v3": 0.83, "cohere-3.5": 0.82}) == "jina-v3"
    assert select({"jina-v3": 0.7, "cohere-3.5": 0.75}) == "cohere-3.5"
