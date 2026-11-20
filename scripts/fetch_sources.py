"""Fetch public sources (data/sources.yaml) into data/raw and record them in the manifest.

Usage:
    uv run python scripts/fetch_sources.py                       # all sources
    uv run python scripts/fetch_sources.py --source k8s-docs     # one source
    uv run python scripts/fetch_sources.py --refresh             # re-download everything

Already-downloaded, unchanged documents are skipped, so re-running is cheap.
Expect some public postmortem links to fail (dead pages, robots.txt): they are
recorded as failed and the run continues.
"""

from __future__ import annotations

import argparse
import asyncio

import httpx

from ragnition.core.config import get_settings, tls_verify
from ragnition.core.logging import configure_logging
from ragnition.data.fetch import PoliteFetcher, fetch_source
from ragnition.data.manifest import Manifest
from ragnition.data.paths import MANIFEST_PATH, SOURCES_PATH
from ragnition.data.sources import load_sources


async def main(only: list[str], refresh: bool) -> int:
    settings = get_settings()
    configure_logging("WARNING", json_output=False)
    sources = [s for s in load_sources(SOURCES_PATH).sources if not only or s.id in only]
    if not sources:
        print(f"no source matches {only}")
        return 1
    manifest = Manifest(MANIFEST_PATH)
    async with httpx.AsyncClient(verify=tls_verify(settings)) as http:
        fetcher = PoliteFetcher(http)
        for source in sources:
            print(f"\n{source.id}  [{source.license}, redistributable={source.redistributable}]")
            summary = await fetch_source(source, fetcher, manifest, refresh=refresh)
            manifest.save()
            print(f"  ok={summary.ok}  skipped(unchanged)={summary.skipped}  failed={summary.failed}")
            for err in summary.errors[:8]:
                print(f"    - {err[:160]}")
            if len(summary.errors) > 8:
                print(f"    ... {len(summary.errors) - 8} more (see data/manifest.jsonl, status=failed)")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", action="append", default=[], help="source id (repeatable)")
    parser.add_argument("--refresh", action="store_true", help="download again even if unchanged")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.source, args.refresh)))
