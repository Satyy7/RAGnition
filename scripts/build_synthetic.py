"""Build the synthetic Kestrel Commerce corpus and the evaluation files.

Usage:
    uv run python scripts/build_synthetic.py            # everything (about 25 LLM calls via Groq)
    uv run python scripts/build_synthetic.py --no-llm   # only service pages, change records, eval files
    uv run python scripts/build_synthetic.py --refresh  # rewrite LLM documents even if present

Steps:
1. validate world.yaml and the dev + test eval sets (every label points at a real corpus doc);
2. render service pages and change records (no LLM);
3. have the generator chain write runbooks and postmortems from the facts;
4. write eval incidents and labels (data/eval, never ingested);
5. check that no corpus document contains an eval answer.
"""

from __future__ import annotations

import argparse
import asyncio

from ragnition.core.config import PROJECT_ROOT, get_settings
from ragnition.core.logging import configure_logging
from ragnition.core.providers import AllProvidersExhausted, ModelGateway
from ragnition.data.manifest import Manifest
from ragnition.data.paths import EVAL_SCENARIOS_PATH, EVAL_TEST_SCENARIOS_PATH, MANIFEST_PATH, WORLD_PATH
from ragnition.data.synthetic import (
    GenerationError,
    build_deterministic,
    build_prose,
    find_leaks,
    write_eval_files,
)
from ragnition.data.world import load_all_eval, load_world


async def main(use_llm: bool, refresh: bool) -> int:
    configure_logging("WARNING", json_output=False)
    world = load_world(WORLD_PATH)
    evalset = load_all_eval(EVAL_SCENARIOS_PATH, EVAL_TEST_SCENARIOS_PATH)
    evalset.validate_against(world)
    print(f"world: {len(world.services)} services, {len(world.changes)} changes, {len(world.history)} past incidents")
    print(f"eval:  {len(evalset.scenarios)} scenarios "
          f"({sum(s.answerable for s in evalset.scenarios)} answerable; dev={len(evalset.split('dev'))}, test={len(evalset.split('test'))})")

    manifest = Manifest(MANIFEST_PATH)
    print(f"\nrendered {build_deterministic(world, manifest)} service/change documents")
    manifest.save()

    if use_llm:
        print("writing runbooks and postmortems (cached calls are instant):")
        try:
            async with ModelGateway.from_settings(get_settings()) as gw:
                print(f"wrote {await build_prose(world, gw, manifest, refresh=refresh)} new documents")
        except (AllProvidersExhausted, GenerationError) as exc:
            manifest.save()
            print(f"\nstopped: {exc}\nDocuments written so far are saved; re-run to continue.")
            return 1
        manifest.save()

    print(f"wrote {write_eval_files(evalset)} eval incidents + labels")

    synthetic = {r.doc_id: (PROJECT_ROOT / r.path).read_text(encoding="utf-8")
                 for r in manifest.ok_records() if r.synthetic and r.path}
    leaks = find_leaks(synthetic, evalset)
    print(f"\nleakage check over {len(synthetic)} synthetic docs: {'clean' if not leaks else 'LEAKS FOUND'}")
    for leak in leaks:
        print(f"  - {leak}")
    return 1 if leaks else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-llm", action="store_true", help="skip runbooks and postmortems")
    parser.add_argument("--refresh", action="store_true", help="regenerate LLM documents")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(not args.no_llm, args.refresh)))
