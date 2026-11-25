"""Generate and check synthetic retrieval queries -> data/eval/retrieval_queries.jsonl

    uv run python scripts/generate_retrieval_queries.py              # 3 per Kestrel doc + 1 per 50 public docs
    uv run python scripts/generate_retrieval_queries.py --public 80  # more public docs

Two LLM calls per document (generate, then judge with a different model family).
Calls are cached, so if a provider runs out, simply re-run: finished documents
come back from the cache instantly. Dropped queries are kept in the file with
their reason, so the filtering can be audited.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter

from ragnition.core.config import PROJECT_ROOT, get_settings
from ragnition.core.logging import configure_logging
from ragnition.core.providers import AllProvidersExhausted, ModelGateway
from ragnition.data.manifest import Manifest
from ragnition.data.paths import EVAL_DIR, MANIFEST_PATH
from ragnition.eval.query_gen import SourceDoc, generate_for_doc, plan_styles, sample_public
from ragnition.ingest.chunking import render_sections
from ragnition.ingest.parse import parse_markdown
from ragnition.ingest.pipeline import parse_corpus

OUT_PATH = EVAL_DIR / "retrieval_queries.jsonl"
MIN_WAIT_S = 30   # when every judge/generator model is rate-limited, pause at least this long
MAX_WAITS = 20    # per document, i.e. up to ~10+ minutes of patience before giving up


def family(model: str | None) -> str:
    m = (model or "").lower()
    for key, fam in (("gpt-oss", "openai"), ("qwen", "qwen"), ("gemini", "google"), ("gemma", "google"),
                     ("llama", "meta")):
        if key in m:
            return fam
    return m


def load_sources(public_n: int) -> list[SourceDoc]:
    manifest = Manifest(MANIFEST_PATH)
    kestrel = []
    for r in sorted(manifest.ok_records(), key=lambda r: r.doc_id):
        if r.synthetic:
            doc = parse_markdown((PROJECT_ROOT / r.path).read_text(encoding="utf-8"), r.title)
            kestrel.append(SourceDoc(doc_id=r.doc_id, title=doc.title, text=render_sections(doc.sections),
                                     doc_type=r.doc_type, synthetic=True))
    public = [SourceDoc(doc_id=m.doc_id, title=m.title, text=render_sections(d.sections), doc_type=m.doc_type,
                        synthetic=False)
              for m, d in parse_corpus(manifest).docs if not m.synthetic]
    return kestrel + sample_public(public, public_n)


async def main(public_n: int) -> int:
    # ERROR only: individual fallbacks are expected here and would flood the screen;
    # the summary below reports which models actually answered.
    configure_logging("ERROR", json_output=False)
    docs = load_sources(public_n)
    plans = plan_styles(docs)
    print(f"{len(docs)} source documents ({sum(d.synthetic for d in docs)} Kestrel), "
          f"{sum(len(p) for p in plans)} queries requested")
    counter = iter(range(1, 100_000))
    results, failed_docs = [], []
    async with ModelGateway.from_settings(get_settings()) as gw:
        for n, (doc, styles) in enumerate(zip(docs, plans, strict=True), start=1):
            print(f"\r  {n}/{len(docs)} {doc.doc_id[:60]:<60}", end="", flush=True)
            for attempt in range(1, MAX_WAITS + 1):
                # Ids are assigned after success, so a retried document keeps a clean numbering.
                try:
                    batch = await generate_for_doc(gw, doc, styles, lambda: "pending")
                except (ValueError, json.JSONDecodeError) as exc:
                    failed_docs.append(f"{doc.doc_id}: {exc}")
                    break
                except AllProvidersExhausted as exc:
                    # Batch job: nobody is waiting on a response, so wait for limits to reset.
                    wait = max(exc.retry_in_s or 0, MIN_WAIT_S)
                    print(f"\n  all {exc.role} models busy; waiting {wait:.0f}s (attempt {attempt}/{MAX_WAITS})")
                    await asyncio.sleep(wait)
                    continue
                for q in batch:
                    q.id = f"RQ-{next(counter):04d}"
                results += batch
                break
            else:
                print(f"\n\nstopped after {MAX_WAITS} waits on {doc.doc_id}. Re-run later; finished "
                      "documents come back from the cache instantly.")
                return 1
    print()

    OUT_PATH.write_text("\n".join(q.model_dump_json() for q in results) + "\n", encoding="utf-8")
    kept = [q for q in results if q.kept]
    same_family = sum(family(q.generator_model) == family(q.judge_model) for q in results)
    print(f"\nwrote {len(results)} queries to {OUT_PATH.relative_to(PROJECT_ROOT)}: {len(kept)} kept, "
          f"{len(results) - len(kept)} dropped")
    print("kept by style:  " + ", ".join(f"{k}={v}" for k, v in sorted(Counter(q.style for q in kept).items())))
    print("kept by source: " + ", ".join(f"{k}={v}" for k, v in sorted(Counter(q.source_type for q in kept).items())))
    print("drop reasons:   " + ", ".join(f"{k}={v}" for k, v in
                                         Counter((q.drop_reason or "").split(":")[0] for q in results if not q.kept).items()))
    print(f"mean word overlap with source doc: {sum(q.overlap for q in kept) / max(len(kept), 1):.2f}")
    print("models used:    " + ", ".join(f"{k}={v}" for k, v in sorted(
        Counter(f"gen {q.generator_model} / judge {q.judge_model}" for q in results).items())))
    if same_family:
        print(f"WARNING: {same_family} queries were generated and judged by the same model family (fallbacks)")
    for f in failed_docs:
        print(f"  could not parse model reply for {f}")
    print("\nSpot-check: open the file and read ~10 kept queries (and a few dropped ones).")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--public", type=int, default=50, help="number of public documents to sample")
    raise SystemExit(asyncio.run(main(p.parse_args().public)))
