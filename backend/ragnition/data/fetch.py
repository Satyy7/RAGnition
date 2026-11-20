"""Polite fetching of public sources into data/raw, recorded in the manifest.

Politeness rules:
- one request at a time, at least `min_interval_s` between requests to one host;
- robots.txt is respected for web pages (RFC 9309: robots.txt missing (4xx) means
  allowed; unreachable (5xx/network) means disallowed). GitHub API/raw files are
  programmatic endpoints of public repositories, so robots.txt does not apply;
- a generic User-Agent that names the tool, never a person;
- responses larger than `max_bytes` are refused.
A failed document is recorded as status=failed with the reason, and the run continues.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from ragnition.data.manifest import Manifest, ManifestRecord, now_iso, store_document
from ragnition.data.paths import RAW_DIR
from ragnition.data.postmortem_index import balanced_sample, parse_postmortem_index
from ragnition.data.sources import SourceSpec

logger = logging.getLogger(__name__)

USER_AGENT = "RAGnition-corpus-fetcher/0.1 (educational RAG project; polite, low-volume)"
ROBOTS_AGENT = "RAGnition-corpus-fetcher"


class FetchError(Exception):
    pass


class PoliteFetcher:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        min_interval_s: float = 1.0,
        max_bytes: int = 5_000_000,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.http = http
        self.min_interval_s = min_interval_s
        self.max_bytes = max_bytes
        self._clock = clock
        self._sleep = sleep
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}  # None = everything allowed

    async def _wait_turn(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            wait = self.min_interval_s - (self._clock() - last)
            if wait > 0:
                await self._sleep(wait)
        self._last_hit[host] = self._clock()

    async def _raw_get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        await self._wait_turn(urlsplit(url).netloc)
        try:
            resp = await self.http.get(
                url, headers={"User-Agent": USER_AGENT, **(headers or {})}, follow_redirects=True, timeout=30
            )
        except httpx.HTTPError as exc:
            raise FetchError(f"{type(exc).__name__}: {exc}") from exc
        return resp

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            try:
                resp = await self._raw_get(f"{origin}/robots.txt")
            except FetchError:
                self._robots[origin] = _deny_all()
            else:
                if resp.status_code >= 500:
                    self._robots[origin] = _deny_all()
                elif resp.status_code >= 400:
                    self._robots[origin] = None
                else:
                    parser = RobotFileParser()
                    parser.parse(resp.text.splitlines())
                    self._robots[origin] = parser
        parser = self._robots[origin]
        return parser is None or parser.can_fetch(ROBOTS_AGENT, url)

    async def get(self, url: str, *, check_robots: bool = True, headers: dict[str, str] | None = None) -> httpx.Response:
        if check_robots and not await self.allowed(url):
            raise FetchError("disallowed by robots.txt")
        resp = await self._raw_get(url, headers)
        if resp.status_code >= 400:
            raise FetchError(f"HTTP {resp.status_code}")
        if len(resp.content) > self.max_bytes:
            raise FetchError(f"response too large ({len(resp.content)} bytes)")
        return resp


def _deny_all() -> RobotFileParser:
    parser = RobotFileParser()
    parser.parse(["User-agent: *", "Disallow: /"])
    return parser


def slugify(text: str, max_len: int = 70) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "doc"


def short_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def _extension(content_type: str) -> str:
    if "html" in content_type:
        return ".html"
    if "markdown" in content_type:
        return ".md"
    return ".txt"


_TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_FRONT_TITLE = re.compile(r"^title:\s*[\"']?(.+?)[\"']?\s*$", re.M)


def html_title(html: str) -> str | None:
    m = _TITLE_TAG.search(html)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else None


@dataclass
class FetchSummary:
    source_id: str
    ok: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class _Target:
    doc_id: str
    url: str
    title: str
    check_robots: bool
    tags: list[str] = field(default_factory=list)
    force_ext: str | None = None


async def _targets(source: SourceSpec, fetcher: PoliteFetcher) -> list[_Target]:
    """Work out which documents a source contains (may need one listing request)."""
    if source.kind == "html_pages":
        return [
            _Target(f"{source.id}/{slugify(urlsplit(u).path)}", u, u, check_robots=True)
            for u in source.urls[: source.max_docs]
        ]
    if source.kind == "github_dir":
        targets: list[_Target] = []
        for d in source.dirs:
            api = f"https://api.github.com/repos/{source.repo}/contents/{d}?ref={source.ref}"
            try:
                listing = (await fetcher.get(api, check_robots=False, headers={"Accept": "application/vnd.github+json"})).json()
            except FetchError as exc:  # one moved directory must not sink the whole source
                logger.warning("directory listing failed", extra={"dir": d, "error": str(exc)})
                continue
            for item in sorted(listing, key=lambda i: i["name"]):
                if item.get("type") == "file" and item["name"].endswith(".md"):
                    stem = item["name"].removesuffix(".md")
                    folder = d.rstrip("/").rsplit("/", 1)[-1]
                    targets.append(
                        _Target(
                            f"{source.id}/{slugify(f'{folder}-{stem}')}",
                            item["download_url"],
                            item["path"],
                            check_robots=False,
                            tags=[folder],
                            force_ext=".md",
                        )
                    )
        return targets[: source.max_docs]
    # link_index
    index = await fetcher.get(source.index_url, check_robots=False)
    links = balanced_sample(parse_postmortem_index(index.text), source.max_docs)
    return [
        _Target(
            f"{source.id}/{slugify(link.company, 40)}-{short_hash(link.url)}",
            link.url,
            f"{link.company} postmortem",
            check_robots=True,
            tags=[slugify(link.category)],
        )
        for link in links
    ]


async def fetch_source(
    source: SourceSpec, fetcher: PoliteFetcher, manifest: Manifest, *, refresh: bool = False
) -> FetchSummary:
    summary = FetchSummary(source.id)
    try:
        targets = await _targets(source, fetcher)
    except (FetchError, ValueError, KeyError, TypeError) as exc:
        summary.failed += 1
        summary.errors.append(f"listing failed: {exc}")
        return summary

    for t in targets:
        if not refresh and manifest.is_current(t.doc_id):
            summary.skipped += 1
            continue
        base = dict(
            doc_id=t.doc_id, source_id=source.id, url=t.url, license=source.license,
            redistributable=source.redistributable, attribution=source.attribution,
            synthetic=False, doc_type=source.doc_type, tags=t.tags, retrieved_at=now_iso(),
        )
        try:
            resp = await fetcher.get(t.url, check_robots=t.check_robots)
            ctype = resp.headers.get("content-type", "text/plain").split(";")[0].strip()
            if not (ctype.startswith("text/") or ctype in ("application/xhtml+xml",)):
                raise FetchError(f"unsupported content type {ctype}")
            ext = t.force_ext or _extension(ctype)
            title = t.title
            if ext == ".html":
                title = html_title(resp.text) or title
            elif m := _FRONT_TITLE.search(resp.text[:2000]):
                title = m.group(1)
            dest = RAW_DIR / source.id / f"{t.doc_id.split('/', 1)[1]}{ext}"
            rel, size, digest = store_document(resp.content, dest)
            manifest.upsert(ManifestRecord(**base, title=title, path=rel, content_type=ctype,
                                           sha256=digest, bytes=size, status="ok"))
            summary.ok += 1
        except FetchError as exc:
            manifest.upsert(ManifestRecord(**base, title=t.title, status="failed", error=str(exc)))
            summary.failed += 1
            summary.errors.append(f"{t.url}: {exc}")
            logger.info("fetch failed", extra={"url": t.url, "error": str(exc)})
    return summary
