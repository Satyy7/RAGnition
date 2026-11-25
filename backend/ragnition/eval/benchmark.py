"""M5 retrieval benchmark: every config x {dense, dense+instruction, bm25} on the
same queries, with bootstrap confidence intervals and a pre-registered decision rule.

Decision rule (written before seeing results):
1. Query instruction: use it for dense search if it raises mean dense R@10 across
   all configs (it costs nothing, so no significance bar).
2. Chunk config: per query, score = (dense R@10 + BM25 R@10) / 2, because the next
   milestone fuses both. Pick the config with the highest mean. Every config whose
   paired difference to it has a 95% CI containing 0 counts as tied; among tied
   configs pick the one with the FEWEST vectors (cheapest, simplest).
"""

from __future__ import annotations

import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field

from qdrant_client import QdrantClient

from ragnition.core.providers import ModelGateway
from ragnition.data.world import EvalScenario
from ragnition.eval.experiments import Experiments
from ragnition.eval.query_gen import RetrievalQuery
from ragnition.eval.retrieval_metrics import ndcg_at_k, recall_at_k, reciprocal_rank
from ragnition.eval.stats import Estimate, bootstrap_ci, paired_diff_ci
from ragnition.ingest.pipeline import chunks_path
from ragnition.retrieval.retriever import Retriever, load_chunks, rank_docs

CHUNKS_PER_QUERY = 50
METHODS = ("dense", "dense+instr", "bm25")
METRICS = ("r5", "r10", "r20", "mrr", "ndcg10")


def incident_query(s: EvalScenario) -> str:
    inc = s.incident
    return "\n".join([s.title, inc.reporter_note, *inc.alerts, *inc.logs])


def incident_queries(scenarios: list[EvalScenario]) -> list[RetrievalQuery]:
    return [
        RetrievalQuery(id=s.id, query=incident_query(s), style="incident", relevant_doc_ids=s.label.supporting_doc_ids,
                       source_doc=s.id, source_type="incident", synthetic_source=True, origin="incident", overlap=0.0)
        for s in scenarios if s.answerable
    ]


def score(ranked: list[str], relevant: set[str]) -> dict[str, float]:
    return {"r5": recall_at_k(ranked, relevant, 5), "r10": recall_at_k(ranked, relevant, 10),
            "r20": recall_at_k(ranked, relevant, 20), "mrr": reciprocal_rank(ranked, relevant),
            "ndcg10": ndcg_at_k(ranked, relevant, 10)}


@dataclass
class BenchmarkRun:
    queries: list[RetrievalQuery]
    per_query: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)  # system -> qid -> metric
    latency_ms: dict[str, float] = field(default_factory=dict)  # system -> median
    vectors: dict[str, int] = field(default_factory=dict)  # config -> searchable chunks

    def values(self, system: str, metric: str, qids: list[str] | None = None) -> list[float]:
        rows = self.per_query[system]
        return [rows[q][metric] for q in (qids or [q.id for q in self.queries])]


async def run_benchmark(gw: ModelGateway, client: QdrantClient, experiments: Experiments,
                        queries: list[RetrievalQuery], log=print) -> BenchmarkRun:
    run = BenchmarkRun(queries)
    texts = [q.query for q in queries]
    plain = (await gw.embed(texts, input_type="query")).vectors
    instr = (await gw.embed([experiments.query_instruction + t for t in texts], input_type="query")).vectors
    log(f"embedded {len(texts)} queries x 2 variants")

    for cfg in experiments.configs:
        retriever = Retriever(cfg.id, load_chunks(chunks_path(cfg.id)), client, gw)
        run.vectors[cfg.id] = len(retriever.searchable)
        timings: dict[str, list[float]] = defaultdict(list)
        for method in METHODS:
            run.per_query[f"{cfg.id}|{method}"] = {}
        for q, v_plain, v_instr in zip(queries, plain, instr, strict=True):
            relevant = set(q.relevant_doc_ids)
            for method in METHODS:
                t0 = time.perf_counter()
                if method == "bm25":
                    hits = retriever.sparse(q.query, CHUNKS_PER_QUERY)
                else:
                    hits = retriever.dense_by_vector(v_instr if method == "dense+instr" else v_plain, CHUNKS_PER_QUERY)
                timings[method].append((time.perf_counter() - t0) * 1000)
                run.per_query[f"{cfg.id}|{method}"][q.id] = score(rank_docs(hits), relevant)
        for method in METHODS:
            run.latency_ms[f"{cfg.id}|{method}"] = statistics.median(timings[method])
        log(f"  {cfg.id}: done")
    return run


@dataclass
class Decision:
    use_instruction: bool
    instruction_effect: Estimate
    config: str
    best_by_mean: str
    tied: list[str]
    combined: dict[str, Estimate]


def decide(run: BenchmarkRun, experiments: Experiments) -> Decision:
    b = experiments.bootstrap
    ids = [c.id for c in experiments.configs]
    qids = [q.id for q in run.queries]

    # 1. instruction: average dense R@10 over configs, per query, then paired diff
    def dense_avg(method: str) -> list[float]:
        return [statistics.mean(run.per_query[f"{c}|{method}"][q]["r10"] for c in ids) for q in qids]

    effect = paired_diff_ci(dense_avg("dense+instr"), dense_avg("dense"), b.resamples, b.seed)
    dense_method = "dense+instr" if effect.mean > 0 else "dense"

    # 2. config: combined per-query score of the two signals the next milestone fuses
    combined_vals = {c: [(run.per_query[f"{c}|{dense_method}"][q]["r10"] + run.per_query[f"{c}|bm25"][q]["r10"]) / 2
                         for q in qids] for c in ids}
    combined = {c: bootstrap_ci(v, b.resamples, b.seed) for c, v in combined_vals.items()}
    best = max(ids, key=lambda c: combined[c].mean)
    tied = [c for c in ids if c == best or not paired_diff_ci(combined_vals[best], combined_vals[c],
                                                              b.resamples, b.seed).excludes_zero()]
    chosen = min(tied, key=lambda c: (run.vectors[c], c))
    return Decision(dense_method == "dense+instr", effect, chosen, best, tied, combined)


def render_report(run: BenchmarkRun, experiments: Experiments, decision: Decision, date: str) -> str:
    b = experiments.bootstrap
    systems = [f"{c.id}|{m}" for c in experiments.configs for m in METHODS]
    n_inc = sum(q.origin == "incident" for q in run.queries)
    lines = [
        f"# M5 retrieval benchmark ({date})", "",
        f"{len(run.queries)} queries ({len(run.queries) - n_inc} generated and judge-checked, {n_inc} hand-labelled "
        f"incidents). Document-level metrics; 95% bootstrap CIs ({b.resamples} resamples).", "",
        "## Decision", "",
        f"- **Query instruction for dense search: {'yes' if decision.use_instruction else 'no'}** "
        f"(effect on mean dense R@10: {decision.instruction_effect.fmt()})",
        f"- **Chunk config: `{decision.config}`** (highest mean combined score: `{decision.best_by_mean}`; "
        f"statistically tied: {', '.join(f'`{t}`' for t in decision.tied)}; rule picks the tied config with the fewest vectors)",
        "", "Combined score = (dense R@10 + BM25 R@10) / 2 per query:", "",
        "| config | vectors | combined [95% CI] |", "|---|---:|---|",
        *[f"| {c.id} | {run.vectors[c.id]} | {decision.combined[c.id].fmt()} |" for c in experiments.configs],
        "", "## All systems", "",
        "| system | R@5 | R@10 [95% CI] | R@20 | MRR | nDCG@10 [95% CI] | median ms |",
        "|---|---:|---|---:|---:|---|---:|",
    ]
    for s in systems:
        mean = {m: statistics.mean(run.values(s, m)) for m in METRICS}
        r10 = bootstrap_ci(run.values(s, "r10"), b.resamples, b.seed)
        nd = bootstrap_ci(run.values(s, "ndcg10"), b.resamples, b.seed)
        lines.append(f"| {s} | {mean['r5']:.3f} | {r10.fmt()} | {mean['r20']:.3f} | {mean['mrr']:.3f} | "
                     f"{nd.fmt()} | {run.latency_ms[s]:.1f} |")

    def breakdown(title: str, key) -> None:
        groups: dict[str, list[str]] = defaultdict(list)
        for q in run.queries:
            groups[key(q)].append(q.id)
        names = sorted(groups)
        lines.extend(["", f"## R@10 by {title}", "", "| system | " + " | ".join(f"{g} (n={len(groups[g])})" for g in names) + " |",
                      "|---|" + "---:|" * len(names)])
        for s in systems:
            lines.append(f"| {s} | " + " | ".join(f"{statistics.mean(run.values(s, 'r10', groups[g])):.3f}" for g in names) + " |")

    breakdown("query style", lambda q: q.style)
    breakdown("source", lambda q: "incident" if q.origin == "incident" else ("kestrel" if q.synthetic_source else "public"))
    breakdown("word overlap with source", lambda q: "incident" if q.origin == "incident" else
              ("low (<0.5)" if q.overlap < 0.5 else "high (>=0.5)"))
    lines += ["", "Notes: generated queries have exactly one labelled document, so another document that also "
              "answers a query counts as a miss (scores are a lower bound). Incident queries use the hand-written labels."]
    return "\n".join(lines) + "\n"
