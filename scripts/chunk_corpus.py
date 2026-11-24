"""Parse every corpus document and chunk it with each strategy.

    uv run python scripts/chunk_corpus.py                        # all three strategies
    uv run python scripts/chunk_corpus.py --strategy structure   # one strategy
    uv run python scripts/chunk_corpus.py --show PM-2025-007     # print one document's chunks

Output: data/processed/chunks.<strategy>.jsonl (gitignored: contains licensed text)
        data/processed/parse_report.json (skipped documents and why)
Needs the BGE model downloaded once (scripts/smoke_providers.py did that).
"""

from __future__ import annotations

import argparse
import json

from ragnition.core.config import get_settings
from ragnition.data.manifest import Manifest
from ragnition.data.paths import MANIFEST_PATH
from ragnition.ingest.chunking import STRATEGIES
from ragnition.ingest.pipeline import PROCESSED_DIR, chunks_path, parse_corpus, run_strategy
from ragnition.ingest.tokens import MODEL_MAX_TOKENS, load_bge_counter


def show(strategy: str, needle: str) -> None:
    for line in chunks_path(strategy).read_text(encoding="utf-8").splitlines():
        c = json.loads(line)
        if needle.lower() in c["doc_id"].lower():
            tag = c["role"] + (f" of {c['parent_id']}" if c["parent_id"] else "")
            print(f"\n--- {c['chunk_id']}  {tag}  {c['n_tokens']} tokens ---\n{c['text']}")


def main(strategies: list[str], show_doc: str | None) -> int:
    if show_doc:
        show(strategies[0], show_doc)
        return 0
    count = load_bge_counter(get_settings().model_cache_dir)
    parsed = parse_corpus(Manifest(MANIFEST_PATH))
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    (PROCESSED_DIR / "parse_report.json").write_text(json.dumps(parsed.skipped, indent=2), encoding="utf-8")

    print(f"parsed {len(parsed.docs)} documents, skipped {len(parsed.skipped)}")
    for doc_id, reason in sorted(parsed.skipped.items()):
        print(f"  - {doc_id}: {reason}")

    failed = False
    print(f"\n{'strategy':<14}{'chunks':>7}{'parents':>8}{'dups':>6}{'min':>6}{'median':>8}{'p95':>6}{'max':>6}  over {MODEL_MAX_TOKENS}")
    for strategy in strategies:
        r = run_strategy(parsed, strategy, count)
        failed |= bool(r.over_limit)
        print(f"{r.strategy:<14}{r.chunks:>7}{r.parents:>8}{r.duplicates_dropped:>6}{r.token_min:>6}"
              f"{r.token_median:>8.0f}{r.token_p95:>6}{r.token_max:>6}  {len(r.over_limit)}")
        print(f"{'':<14}by type: " + ", ".join(f"{k}={v}" for k, v in r.by_doc_type.items()))
    print("\nAll embedded chunks fit the model." if not failed else "\nSOME CHUNKS EXCEED THE MODEL LIMIT.")
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--strategy", choices=STRATEGIES, action="append")
    parser.add_argument("--show", metavar="DOC_ID_PART", help="print the chunks of matching documents")
    args = parser.parse_args()
    default = ["structure"] if args.show else list(STRATEGIES)
    raise SystemExit(main(args.strategy or default, args.show))
