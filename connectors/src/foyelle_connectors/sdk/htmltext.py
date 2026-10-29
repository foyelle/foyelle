"""HTML to readable plain text with the stdlib parser (no scripts, no styles)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin

_SKIP = {"script", "style", "noscript", "template", "svg", "math", "iframe", "object", "canvas", "head"}
_BOILERPLATE = {"nav", "footer", "aside", "form", "button", "select"}
_MAIN = {"main", "article"}
_BLOCK = {
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "figcaption", "figure",
    "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre",
    "section", "table", "tr", "ul",
}  # fmt: skip
_PRE_SPACE = "\ue000"  # protects <pre> indentation from line tidying
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


@dataclass
class Readable:
    title: str
    text: str
    links: list[dict[str, str]] = field(default_factory=list)


class _Extractor(HTMLParser):
    def __init__(self, base_url: str | None, drop_boilerplate: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.drop_boilerplate = drop_boilerplate
        self.stack: list[str] = []
        self.skip_depth = 0
        self.main_depth = 0
        self.in_title = False
        self.in_pre = 0
        self.title: list[str] = []
        self.all: list[str] = []
        self.main: list[str] = []
        self.links: list[dict[str, str]] = []
        self._link: dict[str, str] | None = None
        self._link_text: list[str] = []

    def _emit(self, text: str) -> None:
        self.all.append(text)
        if self.main_depth:
            self.main.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self.in_title = True
        skipping = tag in _SKIP or (self.drop_boilerplate and tag in _BOILERPLATE)
        if tag not in _VOID:
            self.stack.append(tag)
            if skipping or self.skip_depth:
                self.skip_depth += 1
                return
            if tag in _MAIN:
                self.main_depth += 1
            if tag == "pre":
                self.in_pre += 1
        if self.skip_depth:
            return
        if tag in _BLOCK:
            self._emit("\n")
        if tag == "li":
            self._emit("- ")
        if tag == "a":
            href = dict(attrs).get("href")
            if href and not href.startswith(("javascript:", "#", "data:")):
                self._link = {"url": urljoin(self.base_url, href) if self.base_url else href}
                self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        if tag in _VOID or tag not in self.stack:
            return
        while self.stack:
            open_tag = self.stack.pop()
            if self.skip_depth:
                self.skip_depth -= 1
            else:
                if open_tag in _MAIN:
                    self.main_depth -= 1
                if open_tag == "pre":
                    self.in_pre -= 1
                if open_tag in _BLOCK:
                    self._emit("\n")
                if open_tag == "a" and self._link is not None:
                    self._link["text"] = " ".join("".join(self._link_text).split())
                    self.links.append(self._link)
                    self._link = None
            if open_tag == tag:
                break

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title.append(data)
            return
        if self.skip_depth:
            return
        if self._link is not None:
            self._link_text.append(data)
        self._emit(data.replace(" ", _PRE_SPACE) if self.in_pre else re.sub(r"\s+", " ", data))


def _tidy(parts: list[str]) -> str:
    text = "".join(parts)
    lines = [line.strip() for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip().replace(_PRE_SPACE, " ")


def html_to_text(html: str, *, base_url: str | None = None, readable: bool = True) -> Readable:
    """Extract visible text. ``readable`` prefers <main>/<article> and drops nav/footer."""
    parser = _Extractor(base_url, drop_boilerplate=readable)
    parser.feed(html)
    parser.close()
    main = _tidy(parser.main)
    text = main if readable and len(main) >= 200 else _tidy(parser.all)
    return Readable(title=" ".join("".join(parser.title).split()), text=text, links=parser.links)


def strip_tags(fragment: str) -> str:
    """Plain text of a short HTML fragment (search snippets, titles)."""
    return " ".join(html_to_text(fragment, readable=False).text.split())
