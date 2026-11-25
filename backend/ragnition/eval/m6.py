"""M6 benchmark: hybrid fusion and change-aware retrieval on incident queries.

Pre-registered (written before the test run; see README "M6"):
- PRIMARY claim: on the held-out TEST split, `full` (adaptive RRF + change-aware)
  beats `rrf-adaptive` on root-cause-change Recall@5, and the paired 95% bootstrap
  CI of the difference excludes 0.
- Secondary: `full` vs the naive `rrf+latest` baseline (same fusion, but the change
  list is "newest changes on the named services"); graded nDCG@10 must not drop.
- Hyperparameters (window, tau, proximity, fusion weight) are picked by grid search
  on the DEV split only. The test split is scored with those frozen values.

Systems (each produces one ranked list of documents per incident):
    bm25, dense            single retrievers (chunks -> documents)
    rrf                    equal-weight RRF of bm25 + dense
    rrf-adaptive           RRF with identifier-ratio weights
    rrf+latest             rrf-adaptive + naive latest-change list   (baseline for the idea)
    full                   rrf-adaptive + change-aware list          (the M6 system)
    changes-only           change-aware list alone                   (diagnostic)
"""

from __future__ import annotations

import itertools
import math
import statistics
from dataclasses import dataclass, field

from ragnition.data.world import EvalScenario, World, change_doc_id
from ragnition.eval.benchmark import incident_query
from ragnition.eval.stats import Estimate, bootstrap_ci, paired_diff_ci
from ragnition.retrieval.changes import ChangeParams, ChangeRetriever, latest_change_baseline
from ragnition.retrieval.fusion import adaptive_weights, rrf

SYSTEMS = ("bm25", "dense", "rrf", "rrf-adaptive", "rrf+latest", "full", "changes-only")
METRICS = ("rc_r5", "rc_r10", "rc_mrr", "r10", "ndcg10")


@dataclass
class TextLists:
    """Per-incident text retrieval results, computed once and reused across the grid."""

    bm25: list[str]
    dense: list[str]


def graded_ndcg(ranked: list[str], grades: dict[str, int], k: int = 10) -> float:
    dcg = sum((2 ** grades.get(d, 0) - 1) / math.log2(i + 2) for i, d in enumerate(ranked[:k]))
    ideal = sorted(grades.values(), reverse=True)[:k]
    idcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def score_query(ranked: list[str], s: EvalScenario) -> dict[str, float | None]:
    relevant = set(s.label.supporting_doc_ids)
    out: dict[str, float | None] = {
        "r10": len(relevant & set(ranked[:10])) / len(relevant),
        "ndcg10": graded_ndcg(ranked, s.label.relevance()),
        "rc_r5": None, "rc_r10": None, "rc_mrr": None,
    }
    if s.label.root_cause_change:
        target = change_doc_id(s.label.root_cause_change)
        rank = ranked.index(target) + 1 if target in ranked else None
        out["rc_r5"] = float(rank is not None and rank <= 5)
        out["rc_r10"] = float(rank is not None and rank <= 10)
        out["rc_mrr"] = 1 / rank if rank else 0.0
    return out


def rank_systems(s: EvalScenario, text: TextLists, changes: ChangeRetriever, world: World,
                 params: ChangeParams) -> dict[str, list[str]]:
    q = incident_query(s)
    w_bm25, w_dense = adaptive_weights(q)
    change_list = changes.ranked_docs(q, s.reported_at)
    latest = latest_change_baseline(world, changes.linker, q, s.reported_at, params.top_n)
    lists = [text.bm25, text.dense]
    return {
        "bm25": text.bm25,
        "dense": text.dense,
        "rrf": rrf(lists),
        "rrf-adaptive": rrf(lists, [w_bm25, w_dense]),
        "rrf+latest": rrf([*lists, latest], [w_bm25, w_dense, params.fusion_weight]),
        "full": rrf([*lists, change_list], [w_bm25, w_dense, params.fusion_weight]),
        "changes-only": change_list,
    }


@dataclass
class M6Run:
    scenarios: list[EvalScenario]
    params: ChangeParams
    per_query: dict[str, dict[str, dict[str, float | None]]] = field(default_factory=dict)  # system -> id -> metric
    rankings: dict[str, dict[str, list[str]]] = field(default_factory=dict)  # system -> id -> docs

    def values(self, system: str, metric: str, ids: list[str] | None = None) -> list[float]:
        rows = self.per_query[system]
        ids = ids or [s.id for s in self.scenarios]
        return [v for i in ids if (v := rows[i][metric]) is not None]

    def mean(self, system: str, metric: str, ids: list[str] | None = None) -> float:
        vals = self.values(system, metric, ids)
        return statistics.mean(vals) if vals else float("nan")


def evaluate(scenarios: list[EvalScenario], text: dict[str, TextLists], world: World, params: ChangeParams) -> M6Run:
    answerable = [s for s in scenarios if s.answerable]
    run = M6Run(answerable, params, {k: {} for k in SYSTEMS}, {k: {} for k in SYSTEMS})
    changes = ChangeRetriever(world, params)
    for s in answerable:
        for system, ranked in rank_systems(s, text[s.id], changes, world, params).items():
            run.rankings[system][s.id] = ranked[:20]
            run.per_query[system][s.id] = score_query(ranked, s)
    return run


# ------------------------------------------------------------------ dev tuning

GRID = {
    "window_days": [7, 14, 21, 30],
    "tau_days": [3, 5, 10],
    "hop1": [0.4, 0.6, 0.8],  # proximity for 1 hop; 2 hops = hop1 ** 2
    "fusion_weight": [0.5, 1.0, 2.0],
}


def tune_on_dev(dev: list[EvalScenario], text: dict[str, TextLists], world: World) -> tuple[ChangeParams, list[dict]]:
    """Grid search on DEV only. Objective: mean root-cause R@5 of `full`; ties broken by
    nDCG@10, then MRR, then the parameters closest to the defaults (simplest story)."""
    default = ChangeParams()
    rows = []
    for window, tau, hop1, fw in itertools.product(*GRID.values()):
        params = default.model_copy(update={"window_days": window, "tau_days": tau,
                                            "proximity": [1.0, hop1, round(hop1 ** 2, 3)], "fusion_weight": fw})
        run = evaluate(dev, text, world, params)
        distance = (abs(window - default.window_days) / 14 + abs(tau - default.tau_days) / 5
                    + abs(hop1 - default.proximity[1]) / 0.6 + abs(fw - default.fusion_weight))
        rows.append({"params": params, "rc_r5": run.mean("full", "rc_r5"), "ndcg10": run.mean("full", "ndcg10"),
                     "rc_mrr": run.mean("full", "rc_mrr"), "distance": distance})
    rows.sort(key=lambda r: (-r["rc_r5"], -r["ndcg10"], -r["rc_mrr"], r["distance"]))
    return rows[0]["params"], rows


# ------------------------------------------------------------------ comparisons


@dataclass
class Comparison:
    a: str
    b: str
    metric: str
    n: int
    diff: Estimate


def compare(run: M6Run, a: str, b: str, metric: str, resamples: int, seed: int) -> Comparison:
    ids = [s.id for s in run.scenarios if run.per_query[a][s.id][metric] is not None]
    va = [run.per_query[a][i][metric] for i in ids]
    vb = [run.per_query[b][i][metric] for i in ids]
    return Comparison(a, b, metric, len(ids), paired_diff_ci(va, vb, resamples, seed))


def render_report(run: M6Run, split: str, date: str, resamples: int, seed: int, *, test_sha: str | None = None,
                  run_number: int | None = None, grid_top: list[dict] | None = None) -> str:
    n_rc = len(run.values("full", "rc_r5"))
    lines = [f"# M6 change-aware retrieval: {split.upper()} split ({date})", ""]
    if test_sha:
        lines += [f"Held-out test set SHA-256 `{test_sha}` (matches scenarios_test.lock). "
                  f"Test run #{run_number}.", ""]
    lines += [
        f"{len(run.scenarios)} answerable incidents; {n_rc} have a root-cause change. "
        f"Parameters: {run.params.chosen_by}. 95% bootstrap CIs ({resamples} resamples).", "",
        "## Primary result", "",
    ]
    primary = compare(run, "full", "rrf-adaptive", "rc_r5", resamples, seed)
    verdict = "SUPPORTED" if primary.diff.lo > 0 else "NOT supported"
    lines += [f"Root-cause change Recall@5, `full` - `rrf-adaptive`: **{primary.diff.fmt()}** (n={primary.n}) -> "
              f"pre-registered claim **{verdict}**.", ""]
    lines += ["| comparison | metric | n | difference [95% CI] |", "|---|---|---:|---|"]
    for a, b, m in (("full", "rrf-adaptive", "rc_r5"), ("full", "rrf-adaptive", "rc_mrr"),
                    ("full", "rrf+latest", "rc_r5"), ("full", "bm25", "rc_r5"),
                    ("full", "rrf-adaptive", "ndcg10"), ("rrf-adaptive", "rrf", "ndcg10"),
                    ("rrf", "bm25", "ndcg10")):
        c = compare(run, a, b, m, resamples, seed)
        lines.append(f"| `{a}` - `{b}` | {m} | {c.n} | {c.diff.fmt()} |")

    lines += ["", "## All systems", "",
              "| system | root-cause R@5 [95% CI] | root-cause R@10 | root-cause MRR | doc R@10 | nDCG@10 (graded) [95% CI] |",
              "|---|---|---:|---:|---:|---|"]
    for sys_ in SYSTEMS:
        rc = bootstrap_ci(run.values(sys_, "rc_r5"), resamples, seed)
        nd = bootstrap_ci(run.values(sys_, "ndcg10"), resamples, seed)
        lines.append(f"| {sys_} | {rc.fmt()} | {run.mean(sys_, 'rc_r10'):.3f} | {run.mean(sys_, 'rc_mrr'):.3f} | "
                     f"{run.mean(sys_, 'r10'):.3f} | {nd.fmt()} |")

    tags = sorted({t for s in run.scenarios for t in s.tags})
    if tags:
        lines += ["", "## Root-cause R@5 by difficulty tag", "",
                  "| tag | n | bm25 | rrf-adaptive | rrf+latest | full |", "|---|---:|---:|---:|---:|---:|"]
        for t in tags:
            ids = [s.id for s in run.scenarios if t in s.tags and s.label.root_cause_change]
            if ids:
                lines.append(f"| {t} | {len(ids)} | " + " | ".join(
                    f"{run.mean(x, 'rc_r5', ids):.2f}" for x in ("bm25", "rrf-adaptive", "rrf+latest", "full")) + " |")

    lines += ["", "## Per incident (rank of the root-cause change; - = not in top 20)", "",
              "| incident | tags | bm25 | rrf-adaptive | rrf+latest | full |", "|---|---|---:|---:|---:|---:|"]
    for s in run.scenarios:
        if not s.label.root_cause_change:
            continue
        target = change_doc_id(s.label.root_cause_change)

        def rank(system: str) -> str:
            r = run.rankings[system][s.id]
            return str(r.index(target) + 1) if target in r else "-"

        lines.append(f"| {s.id} | {', '.join(s.tags) or '-'} | {rank('bm25')} | {rank('rrf-adaptive')} | "
                     f"{rank('rrf+latest')} | {rank('full')} |")

    if grid_top:
        lines += ["", "## Dev grid search (top 10 of the grid)", "",
                  "| window | tau | hop1 | fusion weight | rc R@5 | nDCG@10 | MRR |", "|---:|---:|---:|---:|---:|---:|---:|"]
        for r in grid_top[:10]:
            p = r["params"]
            lines.append(f"| {p.window_days:g} | {p.tau_days:g} | {p.proximity[1]:g} | {p.fusion_weight:g} | "
                         f"{r['rc_r5']:.3f} | {r['ndcg10']:.3f} | {r['rc_mrr']:.3f} |")
    lines += ["", "Notes: document-level ranking; text retrievers use config/retrieval.yaml. Unanswerable incidents "
              "are excluded here (no relevant documents); they are scored on answer behaviour in a later milestone. "
              "Dev has only 10 answerable incidents, so dev-tuned parameters can overfit: that is exactly why the "
              "claim is judged on the held-out test split."]
    return "\n".join(lines) + "\n"
