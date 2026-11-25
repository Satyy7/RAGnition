"""M5: statistics, nDCG, grid config, embedding cache, query generation, decision rule."""

from types import SimpleNamespace

import pytest
from qdrant_client import QdrantClient

from ragnition.eval.benchmark import BenchmarkRun, decide
from ragnition.eval.experiments import Experiments, load_experiments
from ragnition.eval.query_gen import (
    RetrievalQuery,
    SourceDoc,
    extract_json,
    generate_for_doc,
    lexical_overlap,
    plan_styles,
    sample_public,
)
from ragnition.eval.retrieval_metrics import ndcg_at_k
from ragnition.eval.stats import bootstrap_ci, paired_diff_ci
from ragnition.ingest.chunking import Chunk
from ragnition.retrieval.dense import build_collection, collection_name
from ragnition.retrieval.embed_cache import EmbeddingCache, text_key


def test_bootstrap_ci_brackets_the_mean_and_is_deterministic():
    vals = [0.0, 1.0] * 50
    est = bootstrap_ci(vals, resamples=1000, seed=1)
    assert est.mean == 0.5 and est.lo < 0.5 < est.hi
    assert est == bootstrap_ci(vals, resamples=1000, seed=1)
    assert bootstrap_ci([0.7] * 20).lo == pytest.approx(0.7)


def test_paired_difference_detects_consistent_gain_only():
    a = [1.0] * 30
    assert paired_diff_ci(a, [0.0] * 30).excludes_zero()
    noisy = [1.0, 0.0] * 15
    assert not paired_diff_ci(noisy, noisy[::-1]).excludes_zero()


def test_ndcg():
    assert ndcg_at_k(["a", "b"], {"a"}, 10) == 1.0
    assert ndcg_at_k(["x", "a"], {"a"}, 10) == pytest.approx(1 / 1.58496, rel=1e-4)
    assert ndcg_at_k(["x"], {"a"}, 10) == 0.0


def test_shipped_grid_is_valid():
    exp = load_experiments()
    assert {c.strategy for c in exp.configs} == {"fixed", "structure", "parent_child"}
    assert collection_name("structure-384") == "chunks_structure_384"
    with pytest.raises(ValueError):
        Experiments.model_validate({"configs": [{"id": "fixed-10", "strategy": "fixed", "size": 64, "overlap": 64}],
                                    "query_instruction": "x"})


def _chunk(cid, text):
    return Chunk(chunk_id=cid, doc_id="d", strategy="structure", role="chunk", position=0, header="H", body=text,
                 text=text, n_tokens=1, section_path=[], title="T", source_id="s", doc_type="change", tags=[],
                 url=None, license="x", redistributable=True, synthetic=True)


class CountingGateway:
    def __init__(self):
        self.embedded = 0

    async def embed(self, texts, input_type="document", use_cache=True):
        self.embedded += len(texts)
        return SimpleNamespace(vectors=[[float(len(t)), 1.0] for t in texts])


async def test_embedding_cache_avoids_re_embedding_across_collections():
    cache, client, gw = EmbeddingCache(":memory:"), QdrantClient(":memory:"), CountingGateway()
    chunks = [_chunk("a", "alpha"), _chunk("b", "beta")]
    await build_collection(client, gw, "c1", chunks, 2, model="m", cache=cache)
    await build_collection(client, gw, "c2", chunks + [_chunk("c", "gamma")], 2, model="m", cache=cache)
    assert gw.embedded == 3  # alpha, beta once; gamma once
    assert cache.get_many([text_key("m", "alpha")])[text_key("m", "alpha")] == [5.0, 1.0]
    assert text_key("other-model", "alpha") not in cache.get_many([text_key("other-model", "alpha")])
    client.close()


def test_json_extraction_and_overlap():
    assert extract_json('Sure!\n```json\n{"queries": []}\n```') == {"queries": []}
    with pytest.raises(ValueError):
        extract_json("no json here")
    assert lexical_overlap("pgbouncer pool waiting", "PgBouncer pool size") == pytest.approx(2 / 3)


def test_styles_and_stratified_sampling():
    docs = [SourceDoc(doc_id=f"{src}/{i}", title="t", text="x", doc_type="d", synthetic=False)
            for src in ("a", "b") for i in range(10)]
    picked = sample_public(docs, 6)
    assert sum(d.doc_id.startswith("a/") for d in picked) == 3
    plans = plan_styles([SourceDoc(doc_id="k", title="t", text="x", doc_type="d", synthetic=True)] + picked[:3])
    assert plans[0] == ["keyword", "question", "symptom"]
    assert [p[0] for p in plans[1:]] == ["keyword", "question", "symptom"]


class ScriptedGateway:
    def __init__(self, gen, judge):
        self.replies = {"generator": gen, "judge": judge}

    async def chat(self, request, role="generator", use_cache=True, pin=None):
        return SimpleNamespace(text=self.replies[role], provider="p", model=role)


async def test_generated_queries_are_filtered_with_reasons():
    gen = ('{"queries": [{"style": "keyword", "query": "payments 503 pool"}, '
           '{"style": "question", "query": "What did CHG-2318 change?"}, '
           '{"style": "symptom", "query": "Everything is slow"}]}')
    judge = ('{"verdicts": [{"i": 0, "answerable": true, "specific": true}, '
             '{"i": 1, "answerable": true, "specific": true}, {"i": 2, "answerable": true, "specific": false, "reason": "vague"}]}')
    doc = SourceDoc(doc_id="kestrel/x", title="t", text="payments pool", doc_type="change", synthetic=True)
    ids = iter(range(1, 10))
    out = await generate_for_doc(ScriptedGateway(gen, judge), doc, ["keyword", "question", "symptom"],
                                 lambda: f"RQ-{next(ids):04d}")
    assert [q.kept for q in out] == [True, False, False]
    assert out[1].drop_reason == "names a document id"
    assert out[2].drop_reason.startswith("too generic")
    assert out[0].relevant_doc_ids == ["kestrel/x"]


def _run(scores: dict[str, list[float]], vectors: dict[str, int]) -> BenchmarkRun:
    n = len(next(iter(scores.values())))
    queries = [RetrievalQuery(id=f"q{i}", query="q", style="keyword", relevant_doc_ids=["d"], source_doc="d",
                              source_type="t", synthetic_source=True, origin="generated", overlap=0) for i in range(n)]
    run = BenchmarkRun(queries, vectors=vectors)
    for system, vals in scores.items():
        run.per_query[system] = {f"q{i}": {"r10": v} for i, v in enumerate(vals)}
    return run


def test_decision_picks_cheapest_among_statistically_tied_configs():
    exp = Experiments.model_validate({"configs": [
        {"id": "structure-384", "strategy": "structure"}, {"id": "fixed-256", "strategy": "fixed", "size": 256},
        {"id": "fixed-510", "strategy": "fixed", "size": 510}], "query_instruction": "x"})
    good, same, bad = [1.0, 0.0] * 20, [0.0, 1.0] * 20, [0.0] * 40
    scores = {}
    for cfg, vals in (("structure-384", good), ("fixed-256", same), ("fixed-510", bad)):
        for m in ("dense", "dense+instr", "bm25"):
            scores[f"{cfg}|{m}"] = vals
    d = decide(_run(scores, {"structure-384": 500, "fixed-256": 300, "fixed-510": 100}), exp)
    assert set(d.tied) == {"structure-384", "fixed-256"}  # fixed-510 is clearly worse
    assert d.config == "fixed-256"  # tied and fewer vectors
    assert d.use_instruction is False  # no gain from the prefix
