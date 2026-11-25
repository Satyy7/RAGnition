"""M7 benchmark: reranker bake-off x three ways of applying a reranker.

Pre-registered (written before any M7 run; see README "M7"):
- PRIMARY: on TEST, slot-preserving reranking with the selected reranker improves
  graded nDCG@10 over `base` (M6 change-aware retrieval with plain RRF); paired
  95% bootstrap CI above 0.
- PREDICTION: naive reranking LOWERS root-cause-change Recall@5 versus `base`
  (reported whatever happens).
- Selection (on DEV): the local reranker is used unless an API reranker beats it by
  at least 0.02 nDCG@10 with slot-preserving reranking (privacy and cost win ties).
- Per reranker, depth (10/20/30), query condensation (on/off) and the blend weight
  (0.5/1/2) are tuned on DEV only.

Scores are computed once per (reranker, condensation) for the top 30 and reused for
smaller depths. That is exact for pointwise cross-encoders (bge, Cohere) and an
approximation for listwise ones (Jina v3 sees all 30 documents together).
"""

from __future__ import annotations

import itertools
import statistics
from dataclasses import dataclass, field

from ragnition.data.world import EvalScenario, change_doc_id
from ragnition.eval.m6 import score_query
from ragnition.eval.stats import bootstrap_ci, paired_diff_ci
from ragnition.retrieval.incident import RerankSetup, apply_rerank

RERANKERS = {  # short name -> gateway slot id (config/providers.yaml rerank chain)
    "bge-local": "local:BAAI/bge-reranker-base",
    "jina-v3": "jina:jina-reranker-v3",
    "cohere-3.5": "cohere:rerank-v3.5",
}
METHODS = ("naive", "blend", "slots")
DEPTHS = (10, 20, 30)
BLEND_WEIGHTS = (0.5, 1.0, 2.0)
API_MARGIN = 0.02


@dataclass
class Prepared:
    """Everything a setting needs, computed once per incident."""

    order: dict[str, list[str]]                                   # incident -> base order
    scores: dict[tuple[str, bool], dict[str, dict[str, float]]]   # (reranker, condense) -> incident -> doc -> score
    latency_ms: dict[str, list[float]] = field(default_factory=dict)


def system_name(reranker: str, method: str) -> str:
    return f"{reranker}|{method}"


def rank_with(setup: RerankSetup, short: str, prep: Prepared, sid: str) -> list[str]:
    return apply_rerank(prep.order[sid], prep.scores[(short, setup.condense)][sid], setup)


def evaluate_setting(scenarios: list[EvalScenario], prep: Prepared, short: str, setup: RerankSetup,
                     metric: str = "ndcg10") -> float:
    return statistics.mean(score_query(rank_with(setup, short, prep, s.id), s)[metric] for s in scenarios)


def tune(dev: list[EvalScenario], prep: Prepared, rerankers: list[str]) -> tuple[dict[str, RerankSetup], dict]:
    """Per reranker: depth + condensation by slot-preserving nDCG, then the blend weight.
    Ties go to the smaller depth and to condensation on (cheaper, shorter inputs)."""
    chosen, table = {}, {}
    for short in rerankers:
        best = max(itertools.product(DEPTHS, (True, False)),
                   key=lambda dc: (round(evaluate_setting(dev, prep, short, RerankSetup(
                       reranker=RERANKERS[short], method="slots", depth=dc[0], condense=dc[1])), 6),
                       -dc[0], dc[1]))
        weight = max(BLEND_WEIGHTS, key=lambda w: (round(evaluate_setting(dev, prep, short, RerankSetup(
            reranker=RERANKERS[short], method="blend", depth=best[0], condense=best[1], blend_weight=w)), 6),
            -abs(w - 1.0)))
        chosen[short] = RerankSetup(reranker=RERANKERS[short], method="slots", depth=best[0], condense=best[1],
                                    blend_weight=weight)
        table[short] = evaluate_setting(dev, prep, short, chosen[short])
    return chosen, table


def select(table: dict[str, float]) -> str:
    """Local unless an API reranker beats it by API_MARGIN on dev."""
    local = table.get("bge-local", float("-inf"))
    best_api = max((k for k in table if k != "bge-local"), key=table.get, default=None)
    if best_api is not None and table[best_api] >= local + API_MARGIN:
        return best_api
    return "bge-local" if "bge-local" in table else max(table, key=table.get)


@dataclass
class M7Run:
    scenarios: list[EvalScenario]
    per_query: dict[str, dict[str, dict]] = field(default_factory=dict)
    rankings: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    def values(self, system: str, metric: str) -> list[float]:
        return [v for s in self.scenarios if (v := self.per_query[system][s.id][metric]) is not None]

    def mean(self, system: str, metric: str) -> float:
        vals = self.values(system, metric)
        return statistics.mean(vals) if vals else float("nan")


def evaluate(scenarios: list[EvalScenario], prep: Prepared, setups: dict[str, RerankSetup]) -> M7Run:
    run = M7Run(scenarios)
    systems = {"base": None} | {system_name(r, m): (r, m) for r in setups for m in METHODS}
    for name, spec in systems.items():
        run.per_query[name], run.rankings[name] = {}, {}
        for s in scenarios:
            if spec is None:
                ranked = prep.order[s.id]
            else:
                r, m = spec
                ranked = rank_with(setups[r].model_copy(update={"method": m}), r, prep, s.id)
            run.rankings[name][s.id] = ranked[:20]
            run.per_query[name][s.id] = score_query(ranked, s)
    return run


def _diff(run: M7Run, a: str, b: str, metric: str, resamples: int, seed: int):
    ids = [s.id for s in run.scenarios if run.per_query[a][s.id][metric] is not None]
    return len(ids), paired_diff_ci([run.per_query[a][i][metric] for i in ids],
                                    [run.per_query[b][i][metric] for i in ids], resamples, seed)


def render_report(run: M7Run, prep: Prepared, setups: dict[str, RerankSetup], selected: str, split: str,
                  date: str, resamples: int, seed: int, *, dev_table: dict[str, float] | None = None,
                  test_sha: str | None = None, run_number: int | None = None, test_use: int | None = None) -> str:
    sel = selected
    lines = [f"# M7 reranking: {split.upper()} split ({date})", ""]
    if test_sha:
        lines += [f"Held-out test set SHA-256 `{test_sha}` (matches the lock). M7 test run #{run_number}; "
                  f"this is use #{test_use} of the test set across milestones.", ""]
    lines += [f"{len(run.scenarios)} answerable incidents. `base` = M6 change-aware retrieval with plain RRF. "
              f"Selected reranker: **{sel}** ({setups[sel].chosen_by}). 95% bootstrap CIs ({resamples} resamples).", "",
              "## Pre-registered results", ""]
    n, p1 = _diff(run, system_name(sel, "slots"), "base", "ndcg10", resamples, seed)
    lines.append(f"- PRIMARY: nDCG@10, `{sel}|slots` - `base` = **{p1.fmt()}** (n={n}) -> "
                 f"**{'SUPPORTED' if p1.lo > 0 else 'NOT supported'}**")
    n, p2 = _diff(run, system_name(sel, "naive"), "base", "rc_r5", resamples, seed)
    lines.append(f"- PREDICTION: root-cause R@5, `{sel}|naive` - `base` = **{p2.fmt()}** (n={n}) -> prediction "
                 f"{'held' if p2.mean < 0 else 'did NOT hold'}"
                 f"{' (CI excludes 0)' if p2.hi < 0 else ' (CI includes 0)' if p2.mean < 0 else ''}")
    lines += ["", "| comparison | metric | n | difference [95% CI] |", "|---|---|---:|---|"]
    for a, b, m in ((f"{sel}|slots", "base", "ndcg10"), (f"{sel}|blend", "base", "ndcg10"),
                    (f"{sel}|naive", "base", "ndcg10"), (f"{sel}|naive", "base", "rc_r5"),
                    (f"{sel}|blend", "base", "rc_r5"), (f"{sel}|slots", f"{sel}|naive", "ndcg10")):
        n, d = _diff(run, a, b, m, resamples, seed)
        lines.append(f"| `{a}` - `{b}` | {m} | {n} | {d.fmt()} |")

    lines += ["", "## All systems", "",
              "| system | root-cause R@5 | root-cause MRR | doc R@10 | nDCG@10 (graded) [95% CI] | rerank ms (median) |",
              "|---|---:|---:|---:|---|---:|"]
    for name in run.per_query:
        r = name.split("|")[0]
        lat = statistics.median(prep.latency_ms[r]) if name != "base" and prep.latency_ms.get(r) else 0.0
        nd = bootstrap_ci(run.values(name, "ndcg10"), resamples, seed)
        lines.append(f"| {name} | {run.mean(name, 'rc_r5'):.3f} | {run.mean(name, 'rc_mrr'):.3f} | "
                     f"{run.mean(name, 'r10'):.3f} | {nd.fmt()} | {lat:.0f} |")

    lines += ["", "## Settings per reranker (tuned on dev)", "",
              "| reranker | depth | condensed query | blend weight | dev nDCG@10 (slots) |", "|---|---:|---|---:|---:|"]
    for r, st in setups.items():
        dev = f"{dev_table[r]:.3f}" if dev_table and r in dev_table else "-"
        lines.append(f"| {r} | {st.depth} | {'yes' if st.condense else 'no'} | {st.blend_weight:g} | {dev} |")

    lines += ["", f"## Per incident: rank of the root-cause change ({sel})", "",
              "| incident | base | naive | blend | slots |", "|---|---:|---:|---:|---:|"]
    for s in run.scenarios:
        if not s.label.root_cause_change:
            continue
        t = change_doc_id(s.label.root_cause_change)

        def rank(name: str) -> str:
            r = run.rankings[name][s.id]
            return str(r.index(t) + 1) if t in r else "-"

        lines.append(f"| {s.id} | {rank('base')} | {rank(f'{sel}|naive')} | {rank(f'{sel}|blend')} | "
                     f"{rank(f'{sel}|slots')} |")
    lines += ["", "Notes: slot-preserving keeps change records at their positions, so its root-cause recall equals "
              "`base` by construction; its effect is on the other evidence. API latency is per call as measured when "
              "the call was made (cached calls keep their original latency)."]
    return "\n".join(lines) + "\n"
