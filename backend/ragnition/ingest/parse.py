"""Parse stored documents into clean text organised by heading sections.

HTML  -> trafilatura pulls out the main article (drops menus, banners, footers)
         and returns Markdown, so headings survive -> parsed like Markdown.
Markdown -> front matter, Hugo shortcodes, HTML comments and the synthetic banner
         are removed; text is split at headings into sections with a heading path,
         e.g. ("Runbook: payments-api", "Diagnosis", "PgBouncer waits").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_FRONT_MATTER = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_SHORTCODE_NOTE = re.compile(r"\{\{[<%]\s*(note|caution|warning)\s*[>%]\}\}", re.I)
_SHORTCODE = re.compile(r"\{\{[<%].*?[>%]\}\}", re.S)
_BANNER = re.compile(r"^> SYNTHETIC DOCUMENT.*$", re.M)
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass
class Section:
    path: tuple[str, ...]  # heading path, outermost first ("" path = text before any heading)
    text: str


@dataclass
class ParsedDoc:
    title: str
    sections: list[Section] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return sum(len(s.text.split()) for s in self.sections)


# Look-alike characters that break keyword search: "payments‑api" (non-breaking
# hyphen) does not match "payments-api". Map them to plain ASCII.
_LOOKALIKES = str.maketrans({
    "‐": "-", "‑": "-", "‒": "-", "−": "-",   # hyphens, minus
    " ": " ", " ": " ", " ": " ", " ": " ",   # special spaces
    "​": "", "﻿": "",                                   # zero-width, BOM
    "‘": "'", "’": "'", "“": '"', "”": '"',   # smart quotes
})


def normalize_chars(text: str) -> str:
    return text.translate(_LOOKALIKES)


def clean_markdown(text: str) -> str:
    text = normalize_chars(text.replace("\r\n", "\n"))
    text = _FRONT_MATTER.sub("", text, count=1)
    text = _HTML_COMMENT.sub("", text)
    text = _BANNER.sub("", text)
    text = _SHORTCODE_NOTE.sub(lambda m: f"{m.group(1).capitalize()}:", text)
    text = _SHORTCODE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def split_sections(markdown: str) -> list[Section]:
    """Split at headings (ignoring '#' lines inside code fences)."""
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []  # (level, heading)
    buf: list[str] = []
    in_code = False

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            sections.append(Section(tuple(h for _, h in stack), body))
        buf.clear()

    for line in markdown.split("\n"):
        if _FENCE.match(line):
            in_code = not in_code
        m = None if in_code else _HEADING.match(line)
        if m:
            flush()
            level = len(m.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, m.group(2).strip()))
        else:
            buf.append(line)
    flush()
    return sections


def parse_markdown(text: str, fallback_title: str) -> ParsedDoc:
    md = clean_markdown(text)
    sections = split_sections(md)
    # A single H1 that wraps every section is the document title, not a section.
    title = fallback_title
    h1s = re.findall(r"^#\s+(.+?)\s*#*\s*$", md, re.M)
    if len(h1s) == 1 and all(s.path and s.path[0] == h1s[0].strip() for s in sections):
        title = h1s[0].strip()
        sections = [Section(s.path[1:], s.text) for s in sections]
    return ParsedDoc(title=title, sections=sections)


def html_to_markdown(html: str) -> str:
    import trafilatura  # imported lazily: heavy, only needed for HTML

    out = trafilatura.extract(
        html,
        output_format="markdown",
        include_formatting=True,  # keeps headings, so sections survive
        include_comments=False,   # reader comments are not evidence
        include_tables=True,
        favor_precision=True,     # prefer dropping boilerplate over keeping it
    )
    return out or ""


def parse_document(raw: bytes, suffix: str, fallback_title: str) -> ParsedDoc:
    text = raw.decode("utf-8", errors="replace")
    if suffix in (".html", ".htm"):
        text = html_to_markdown(text)
    return parse_markdown(text, fallback_title)
