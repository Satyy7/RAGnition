"""M6: entity linking, dependency graph, change scoring, fusion, graded nDCG. Dev incidents only."""

import pytest

from ragnition.data.paths import EVAL_SCENARIOS_PATH, WORLD_PATH
from ragnition.data.world import load_eval_set, load_world
from ragnition.eval.benchmark import incident_query
from ragnition.eval.m6 import graded_ndcg
from ragnition.retrieval.changes import ChangeParams, ChangeRetriever, ServiceGraph, latest_change_baseline
from ragnition.retrieval.entities import EntityLinker, build_aliases
from ragnition.retrieval.fusion import adaptive_weights, identifier_ratio, rrf


@pytest.fixture(scope="module")
def world():
    return load_world(WORLD_PATH)


@pytest.fixture(scope="module")
def dev():
    return {s.id: s for s in load_eval_set(EVAL_SCENARIOS_PATH).scenarios}


def test_aliases_come_from_the_registry_and_are_unambiguous(world):
    aliases = build_aliases(world)
    assert aliases["pgbouncer"] == "payments-api"
    assert aliases["cart-cache"] == "checkout-api"
    assert aliases["coredns"] == "cluster-dns"
    assert aliases["paygate"] == "payments-api"
    assert "redis" not in aliases and "postgresql" not in aliases  # used by several services


def test_linker_finds_services_inside_hostnames_and_pod_names(world):
    linker = EntityLinker(world)
    text = ("lookup payments-pgbouncer.payments.svc.cluster.local: i/o timeout; "
            "kubectl top pods: coredns-6f9b8-2xk4p 1000m; GET http://inventory-api.fulfilment.svc/v2")
    assert {"payments-api", "cluster-dns", "inventory-api"} <= set(linker.services(text))
    assert linker.services("everything is slow today") == []


def test_graph_distances_include_reverse_edges_and_shared_infra(world):
    g = ServiceGraph(world)
    d = g.distances(["checkout-api"], 2)
    assert d["inventory-api"] == 1  # checkout calls inventory
    assert d["web-storefront"] == 1  # storefront calls checkout (reverse edge)
    assert d["catalog-api"] == 2  # via inventory-api
    assert d["cluster-dns"] == 1  # shared: one hop from everything


def test_change_scoring_respects_time_and_status(world):
    cr = ChangeRetriever(world, ChangeParams(window_days=30))
    cands = cr.candidates("", "2027-01-19T09:00:00Z", seeds=["notification-service"])
    ids = [c.change.id for c in cands]
    assert "CHG-2409" not in ids  # rolled back
    assert "CHG-2410" in ids
    assert all(c.days >= 0 for c in cands)  # nothing dated after the incident
    later = cr.candidates("", "2027-01-21T14:00:00Z", seeds=["catalog-api"])
    assert "CHG-2414" not in [c.change.id for c in later]  # 2027-01-22 > incident date
    assert all(c.why for c in cands)


def test_scheduled_vendor_notice_is_an_open_risk(world, dev):
    s = dev["EVAL-006"]  # notice dated 24 days earlier, outside a 14-day window
    cands = ChangeRetriever(world, ChangeParams(window_days=14)).candidates(incident_query(s), s.reported_at)
    assert "CHG-2302" in [c.change.id for c in cands]


def test_no_anchor_adds_nothing(world):
    assert ChangeRetriever(world).candidates("users unhappy", "2026-12-01T00:00:00Z") == []


def test_latest_change_baseline_only_uses_named_services(world, dev):
    s = dev["EVAL-010"]  # names checkout-api and inventory-api
    docs = latest_change_baseline(world, EntityLinker(world), incident_query(s), s.reported_at)
    assert docs and all(d.startswith("kestrel/change/") for d in docs)


def test_rrf_and_weights():
    assert rrf([["a", "b"], ["b", "c"]])[0] == "b"  # agreement wins
    assert rrf([["a"], ["b"]], [2.0, 1.0])[0] == "a"
    logs = "pgbouncer cl_waiting=58 sv_active=25 default_pool_size CHG-2318 503"
    prose = "customers say checkout is slow this evening and payments fail sometimes"
    assert identifier_ratio(logs) > identifier_ratio(prose)
    w_bm25, w_dense = adaptive_weights(logs)
    assert w_bm25 > 1.0 > w_dense and 0.25 <= w_dense
    w_bm25, w_dense = adaptive_weights(prose)
    assert w_dense > w_bm25


def test_stale_points_are_pruned_even_if_delete_left_them():
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams

    from ragnition.retrieval.dense import point_id, prune_stale_points

    client = QdrantClient(":memory:")
    client.create_collection("c", vectors_config=VectorParams(size=2, distance=Distance.COSINE))
    client.upsert("c", points=[PointStruct(id=point_id(cid), vector=[1.0, 0.0], payload={"chunk_id": cid})
                               for cid in ("old", "new")])  # 'old' survived a failed delete
    assert prune_stale_points(client, "c", {point_id("new")}) == 1
    assert client.count("c", exact=True).count == 1
    client.close()


def test_graded_ndcg():
    grades = {"c": 3, "p": 2, "s": 1}
    assert graded_ndcg(["c", "p", "s"], grades) == pytest.approx(1.0)
    assert graded_ndcg(["s", "p", "c"], grades) < graded_ndcg(["c", "s", "p"], grades) < 1.0
    assert graded_ndcg(["x"], grades) == 0.0
