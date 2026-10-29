from __future__ import annotations

import json

import httpx
import pytest
from conftest import check_surface, hints, mcp_session, serving

from foyelle_connectors.web.server import build_server
from foyelle_connectors.web.service import SearchConfig, WebError, WebService, check_url, default_client

PUBLIC = {"example.com": ["93.184.215.14"], "search.example": ["93.184.215.15"], "api.search.brave.com": ["1.1.1.1"]}
ARTICLE = (
    "<html><head><title>Hello  World</title><script>steal()</script></head><body>"
    "<nav>Menu Home About</nav><main><h1>Headline</h1><p>" + "Body text. " * 30 + "</p>"
    "<p><a href='/next'>Next page</a></p></main><footer>Footer junk</footer></body></html>"
)


def resolver(host: str, port: int) -> list[str]:
    return {"rebind.example": ["127.0.0.1"], "mixed.example": ["93.184.215.14", "10.0.0.5"]}.get(host) or PUBLIC.get(host, [])


def handler(request: httpx.Request) -> httpx.Response:
    url = request.url
    if url.host == "search.example":
        assert url.path == "/search" and url.params["format"] == "json"
        return httpx.Response(200, json={"results": [
            {"title": "One <b>bold</b>", "url": "https://example.com/1", "content": "First &amp; best"},
            {"title": "No URL"},
        ]})
    if url.host == "api.search.brave.com":
        assert request.headers["X-Subscription-Token"] == "brave-key"
        return httpx.Response(200, json={"web": {"results": [
            {"title": "Brave <strong>hit</strong>", "url": "https://example.com/b", "description": "desc"}]}})
    if url.path == "/article":
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=ARTICLE)
    if url.path == "/redirect-public":
        return httpx.Response(302, headers={"location": "/article"})
    if url.path == "/redirect-private":
        return httpx.Response(302, headers={"location": "http://127.0.0.1:7300/mcp"})
    if url.path == "/big":
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"a" * 5000)
    if url.path == "/image":
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG")
    return httpx.Response(404, text="not found")


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


@pytest.fixture
def svc() -> WebService:
    return WebService(SearchConfig("searxng", url="https://search.example/"), client_factory=client, resolver=resolver,
                      max_bytes=1000)


def test_check_url() -> None:
    assert check_url("https://example.com/a", resolver)
    for bad in ("file:///etc/passwd", "ftp://example.com", "http://127.0.0.1/", "http://[::1]/", "http://10.1.2.3/",
                "http://169.254.169.254/latest", "http://localhost:7300/mcp", "http://printer.local/",
                "http://rebind.example/", "http://mixed.example/", "http://user:pw@example.com/",
                "http://[::ffff:127.0.0.1]/", "http://100.64.0.1/", "http://unresolvable.example/"):
        with pytest.raises(WebError):
            check_url(bad, resolver)


async def test_searxng(svc: WebService) -> None:
    out = await svc.search("foyelle")
    assert out["results"] == [{"title": "One bold", "url": "https://example.com/1", "snippet": "First & best"}]


async def test_brave() -> None:
    svc = WebService(SearchConfig("brave", api_key=lambda: "brave-key"), client_factory=client, resolver=resolver)
    out = await svc.search("foyelle", count=3)
    assert out["provider"] == "brave" and out["results"][0]["title"] == "Brave hit"


async def test_fetch_readable(svc: WebService) -> None:
    svc.max_bytes = 100_000
    page = await svc.fetch("https://example.com/redirect-public")
    assert page["final_url"] == "https://example.com/article" and page["title"] == "Hello World"
    assert page["text"].startswith("Headline\n\nBody text.")
    assert "steal" not in page["text"] and "Menu" not in page["text"] and "Footer" not in page["text"]
    assert page["links"] == [{"url": "https://example.com/next", "text": "Next page"}]


async def test_fetch_guards(svc: WebService) -> None:
    with pytest.raises(WebError, match="private"):
        await svc.fetch("https://example.com/redirect-private")
    with pytest.raises(WebError, match="non-text"):
        await svc.fetch("https://example.com/image")
    big = await svc.fetch("https://example.com/big")
    assert big["truncated"] and len(big["text"]) == 1000


def test_default_client_uses_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:3128")
    c = default_client()
    assert c._trust_env is True  # noqa: SLF001
    pools = [type(t._pool).__name__ for t in c._mounts.values() if t is not None]  # noqa: SLF001
    assert pools == ["AsyncHTTPProxy"]
    assert c.follow_redirects is False


async def test_end_to_end(svc: WebService) -> None:
    with serving(build_server(svc)) as url:
        async with mcp_session(url) as session:
            tools = await check_surface(session, "search", "ui://web/results")
            assert hints(tools["search"]) == (True, False, True) and hints(tools["fetch"]) == (True, False, True)
            result = await session.call_tool("search", {"query": "foyelle"})
            assert result.structuredContent["results"][0]["url"] == "https://example.com/1"
            assert json.loads(result.content[0].text)["query"] == "foyelle"
            result = await session.call_tool("fetch", {"url": "http://127.0.0.1:7301/mcp"})
            assert result.isError
