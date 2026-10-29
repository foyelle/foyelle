"""Web search (SearXNG or Brave) and fetch with SSRF guards, through the egress proxy."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from ..sdk.htmltext import html_to_text, strip_tags

USER_AGENT = "Foyelle-web-connector/0.1"
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
MAX_REDIRECTS = 5
_HTML_TYPES = {"text/html", "application/xhtml+xml"}
_TEXT_TYPES = {"application/json", "application/xml", "application/rss+xml", "application/atom+xml", "application/javascript"}

Resolver = Callable[[str, int], list[str]]


class WebError(RuntimeError):
    """A refused or failed web request."""


def system_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise WebError(f"cannot resolve {host}: {exc.strerror}") from None
    return sorted({str(info[4][0]) for info in infos})


def is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_loopback or ip.is_link_local)


def check_url(url: str, resolver: Resolver) -> str:
    """Allow only http(s) URLs whose host resolves solely to public addresses."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise WebError(f"malformed URL: {url!r}") from None
    if parts.scheme not in ("http", "https"):
        raise WebError("only http and https URLs can be fetched")
    if not parts.hostname:
        raise WebError("URL has no host")
    if parts.username or parts.password:
        raise WebError("URLs with credentials are not allowed")
    host = parts.hostname
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        if host.endswith((".localhost", ".local", ".internal")) or host == "localhost":
            raise WebError(f"{host} is a local name") from None
        addresses = resolver(host, port or (443 if parts.scheme == "https" else 80))
    if not addresses:
        raise WebError(f"cannot resolve {host}")
    for address in addresses:
        if not is_public(address):
            raise WebError(f"{host} resolves to a private, loopback or reserved address")
    return url


def default_client(timeout: float = 20.0) -> httpx.AsyncClient:
    # trust_env=True: HTTPS_PROXY/HTTP_PROXY/NO_PROXY from the unit environment.
    return httpx.AsyncClient(
        trust_env=True, timeout=timeout, follow_redirects=False, headers={"User-Agent": USER_AGENT}
    )


@dataclass
class SearchConfig:
    provider: str  # "searxng" | "brave"
    url: str | None = None  # SearXNG base URL
    api_key: Callable[[], str] | None = None  # Brave subscription token


class WebService:
    def __init__(
        self,
        search: SearchConfig,
        *,
        client_factory: Callable[[], httpx.AsyncClient] = default_client,
        resolver: Resolver = system_resolver,
        max_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        if search.provider not in ("searxng", "brave"):
            raise ValueError("FOYELLE_SEARCH_PROVIDER must be 'searxng' or 'brave'")
        # A SearXNG provider without a URL is "not configured": fetch still works and search says so.
        self.config = search
        self.client_factory = client_factory
        self.resolver = resolver
        self.max_bytes = max_bytes

    async def search(self, query: str, count: int = 10) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise WebError("empty query")
        count = max(1, min(count, 20))
        if self.config.provider == "searxng" and not self.config.url:
            raise WebError("web search is not configured: set FOYELLE_SEARCH_URL (SearXNG) or use the Brave provider")
        async with self.client_factory() as client:
            try:
                if self.config.provider == "brave":
                    results = await self._brave(client, query, count)
                else:
                    results = await self._searxng(client, query, count)
            except httpx.HTTPError as exc:
                raise WebError(f"search provider request failed: {type(exc).__name__}") from None
        return {"query": query, "provider": self.config.provider, "results": results}

    async def _searxng(self, client: httpx.AsyncClient, query: str, count: int) -> list[dict[str, str]]:
        resp = await client.get(
            f"{self.config.url.rstrip('/')}/search",  # type: ignore[union-attr]
            params={"q": query, "format": "json", "safesearch": "1"},
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            raise WebError(f"SearXNG returned HTTP {resp.status_code}")
        items = resp.json().get("results", [])
        return [
            {"title": strip_tags(str(r.get("title", ""))), "url": str(r.get("url", "")),
             "snippet": strip_tags(str(r.get("content", "")))}
            for r in items[:count] if r.get("url")
        ]

    async def _brave(self, client: httpx.AsyncClient, query: str, count: int) -> list[dict[str, str]]:
        if self.config.api_key is None:
            raise WebError("no Brave API key configured")
        resp = await client.get(
            BRAVE_URL,
            params={"q": query, "count": count, "safesearch": "moderate"},
            headers={"Accept": "application/json", "X-Subscription-Token": self.config.api_key()},
        )
        if resp.status_code != 200:
            raise WebError(f"Brave Search returned HTTP {resp.status_code}")
        items = resp.json().get("web", {}).get("results", [])
        return [
            {"title": strip_tags(str(r.get("title", ""))), "url": str(r.get("url", "")),
             "snippet": strip_tags(str(r.get("description", "")))}
            for r in items[:count] if r.get("url")
        ]

    async def fetch(self, url: str, max_chars: int = 20000) -> dict[str, Any]:
        max_chars = max(500, min(max_chars, 200_000))
        current = url
        async with self.client_factory() as client:
            for _ in range(MAX_REDIRECTS + 1):
                await asyncio.to_thread(check_url, current, self.resolver)  # DNS lookups block
                try:
                    async with client.stream("GET", current, headers={"Accept": "text/html,text/plain;q=0.9,*/*;q=0.5"}) as resp:
                        if resp.is_redirect and "location" in resp.headers:
                            current = urljoin(current, resp.headers["location"])
                            continue
                        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                        if not (ctype in _HTML_TYPES or ctype.startswith("text/") or ctype in _TEXT_TYPES
                                or ctype.endswith(("+json", "+xml")) or not ctype):
                            raise WebError(f"refusing non-text content ({ctype})")
                        body, over = bytearray(), False
                        async for chunk in resp.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > self.max_bytes:
                                del body[self.max_bytes:]
                                over = True
                                break
                        encoding = resp.charset_encoding or "utf-8"
                        status = resp.status_code
                except httpx.HTTPError as exc:
                    raise WebError(f"request failed: {type(exc).__name__}") from None
                break
            else:
                raise WebError("too many redirects")
        try:
            text = bytes(body).decode(encoding, errors="replace")
        except LookupError:
            text = bytes(body).decode("utf-8", errors="replace")
        title, links = "", []
        if ctype in _HTML_TYPES or (not ctype and "<html" in text[:2000].lower()):
            readable = html_to_text(text, base_url=current)
            title, text, links = readable.title, readable.text, readable.links
        return {
            "url": url,
            "final_url": current,
            "status": status,
            "content_type": ctype,
            "title": title,
            "text": text[:max_chars],
            "truncated": over or len(text) > max_chars,
            "links": [link for link in links if link["url"].startswith(("http://", "https://"))][:40],
        }
