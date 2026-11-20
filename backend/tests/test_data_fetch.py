"""Index parsing, manifest, and polite fetching (network mocked with respx)."""

import httpx
import pytest
import respx

from ragnition.data.fetch import FetchError, PoliteFetcher, fetch_source, html_title, slugify
from ragnition.data.manifest import Manifest
from ragnition.data.postmortem_index import balanced_sample, parse_postmortem_index
from ragnition.data.sources import SourceSpec

README = """# A List of Post-mortems!

## Config Errors

[Acme](https://acme.example/pm1). A bad config took everything down.

[Beta](https://beta.example/pm). DNS change went wrong.

## Database

[Gamma](https://gamma.example/db). Replica lag.

[Acme](https://acme.example/pm1). Duplicate link is ignored.

## Other lists of postmortems

[Some list](https://list.example/). Not a postmortem.
"""


def test_parse_index_categories_dedup_and_stop():
    entries = parse_postmortem_index(README)
    assert [(e.company, e.category) for e in entries] == [
        ("Acme", "Config Errors"), ("Beta", "Config Errors"), ("Gamma", "Database")]


def test_balanced_sample_round_robins_categories():
    picked = balanced_sample(parse_postmortem_index(README), 2)
    assert {p.category for p in picked} == {"Config Errors", "Database"}


def test_slug_and_title():
    assert slugify("/sre-book/Effective Troubleshooting/") == "sre-book-effective-troubleshooting"
    assert html_title("<html><title>\n Hello  World </title>") == "Hello World"


class Clock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self):
        return self.t

    async def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


@respx.mock
async def test_robots_disallow_is_respected():
    respx.get("https://site.example/robots.txt").respond(200, text="User-agent: *\nDisallow: /private/")
    respx.get("https://site.example/public").respond(200, text="ok")
    clock = Clock()
    async with httpx.AsyncClient() as http:
        f = PoliteFetcher(http, clock=clock.now, sleep=clock.sleep)
        assert (await f.get("https://site.example/public")).text == "ok"
        with pytest.raises(FetchError, match="robots"):
            await f.get("https://site.example/private/x")


@respx.mock
async def test_missing_robots_allows_and_unreachable_robots_denies():
    respx.get("https://a.example/robots.txt").respond(404)
    respx.get("https://a.example/page").respond(200, text="ok")
    respx.get("https://b.example/robots.txt").respond(503)
    clock = Clock()
    async with httpx.AsyncClient() as http:
        f = PoliteFetcher(http, clock=clock.now, sleep=clock.sleep)
        assert await f.allowed("https://a.example/page")
        assert not await f.allowed("https://b.example/page")


@respx.mock
async def test_requests_to_one_host_are_spaced_out():
    respx.get("https://h.example/1").respond(200)
    respx.get("https://h.example/2").respond(200)
    clock = Clock()
    async with httpx.AsyncClient() as http:
        f = PoliteFetcher(http, min_interval_s=1.0, clock=clock.now, sleep=clock.sleep)
        await f.get("https://h.example/1", check_robots=False)
        await f.get("https://h.example/2", check_robots=False)
    assert clock.sleeps == [1.0]


@respx.mock
async def test_fetch_source_records_ok_and_failed(tmp_path, monkeypatch):
    import ragnition.data.fetch as fetch_mod
    import ragnition.data.manifest as manifest_mod

    monkeypatch.setattr(fetch_mod, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(manifest_mod, "PROJECT_ROOT", tmp_path)
    respx.get("https://p.example/robots.txt").respond(404)
    respx.get("https://p.example/good/").respond(200, html="<title>Good page</title><p>hi</p>")
    respx.get("https://p.example/gone/").respond(404)
    source = SourceSpec(id="test-src", title="t", kind="html_pages", doc_type="documentation",
                        license="CC-BY-4.0", license_evidence="test", redistributable=True,
                        urls=["https://p.example/good/", "https://p.example/gone/"])
    manifest = Manifest(tmp_path / "manifest.jsonl")
    clock = Clock()
    async with httpx.AsyncClient() as http:
        f = PoliteFetcher(http, clock=clock.now, sleep=clock.sleep)
        summary = await fetch_source(source, f, manifest)
        assert (summary.ok, summary.failed) == (1, 1)
        good = manifest.records["test-src/good"]
        assert good.title == "Good page" and good.license == "CC-BY-4.0" and not good.synthetic
        assert manifest.records["test-src/gone"].error == "HTTP 404"
        again = await fetch_source(source, f, manifest)  # unchanged file -> skipped
        assert again.skipped == 1

    manifest.save()
    reloaded = Manifest(tmp_path / "manifest.jsonl")
    assert reloaded.records["test-src/good"].sha256 == good.sha256
