"""Audit the corpus: what we have, under which license, and whether it is safe to use.

    uv run python scripts/data_report.py

Checks:
- every 'ok' manifest row's file exists and its SHA-256 still matches;
- every corpus file is under a corpus root (never under data/eval);
- every eval label's supporting documents are present in the corpus;
- no corpus document (public or synthetic) contains an eval answer.
"""

from __future__ import annotations

from collections import Counter

from ragnition.core.config import PROJECT_ROOT
from ragnition.data.manifest import Manifest, sha256_bytes
from ragnition.data.paths import (
    EVAL_SCENARIOS_PATH,
    EVAL_TEST_SCENARIOS_PATH,
    MANIFEST_PATH,
    LeakageError,
    ensure_corpus_path,
)
from ragnition.data.synthetic import find_leaks
from ragnition.data.world import load_all_eval


def main() -> int:
    manifest = Manifest(MANIFEST_PATH)
    records = list(manifest.records.values())
    ok = [r for r in records if r.status == "ok"]
    problems: list[str] = []

    print(f"manifest: {len(records)} rows, {len(ok)} ok, {len(records) - len(ok)} failed/skipped\n")
    print(f"{'source':<22}{'ok':>5}{'failed':>8}  license / redistributable")
    for source in sorted({r.source_id for r in records}):
        rows = [r for r in records if r.source_id == source]
        n_ok = sum(r.status == "ok" for r in rows)
        print(f"{source:<22}{n_ok:>5}{len(rows) - n_ok:>8}  {rows[0].license} / {rows[0].redistributable}")
    print("\nby document type: " + ", ".join(f"{k}={v}" for k, v in sorted(Counter(r.doc_type for r in ok).items())))
    print(f"synthetic: {sum(r.synthetic for r in ok)}   public: {sum(not r.synthetic for r in ok)}")

    texts: dict[str, str] = {}
    for r in ok:
        path = PROJECT_ROOT / r.path
        try:
            ensure_corpus_path(path)
        except LeakageError as exc:
            problems.append(str(exc))
            continue
        if not path.exists():
            problems.append(f"{r.doc_id}: file missing ({r.path})")
            continue
        data = path.read_bytes()
        if sha256_bytes(data) != r.sha256:
            problems.append(f"{r.doc_id}: file changed since it was recorded")
        texts[r.doc_id] = data.decode("utf-8", errors="replace")

    evalset = load_all_eval(EVAL_SCENARIOS_PATH, EVAL_TEST_SCENARIOS_PATH)
    needed = {d for s in evalset.scenarios for d in s.label.supporting_doc_ids}
    missing = sorted(needed - set(texts))
    if missing:
        problems.append(f"supporting docs not in corpus yet: {', '.join(missing)}")
    problems += find_leaks(texts, evalset)

    reasoning = Counter(s.reasoning for s in evalset.scenarios)
    print(f"\neval: {len(evalset.scenarios)} scenarios: " + ", ".join(f"{k}={v}" for k, v in sorted(reasoning.items())))
    print(f"\n{'All checks passed.' if not problems else f'{len(problems)} problem(s):'}")
    for p in problems:
        print(f"  - {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
