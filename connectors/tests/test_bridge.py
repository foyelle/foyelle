from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager

import httpx
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from foyelle_connectors import sdk
from foyelle_connectors.bridge.server import VIEW_TITLES, RemoteService, build_server, local_name, view_uris

URL = "https://mcp.service.example/mcp"


def fake_service() -> tuple[FastMCP, list[str]]:
    """A service's MCP server, like Kiwi's: one useful tool and one we don't want."""
    service = FastMCP("service", host="0.0.0.0", streamable_http_path="/mcp")
    feedback: list[str] = []

    @service.tool(name="search-flight", structured_output=True, meta={"ui": {"resourceUri": "ui://service/theirs"}})
    def search_flight(flyFrom: str, flyTo: str) -> dict[str, list[dict[str, str | int]]]:
        return {"flights": [{"from": flyFrom, "to": flyTo, "price": 120}]}

    @service.tool(name="feedback-to-devs")
    def feedback_to_devs(text: str) -> str:
        feedback.append(text)
        return "thanks"

    @service.tool(name="price-alert", annotations=sdk.annotations(read_only=True))
    def price_alert(route: str) -> str:
        return f"no alert for {route}"

    return service, feedback


@asynccontextmanager
async def bridged(tools: list[str], **options):
    service, feedback = fake_service()
    app = service.streamable_http_app()
    remote = RemoteService(URL, tools, http=lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=app)), **options)
    async with service.session_manager.run():
        server = build_server("kiwi", remote, port=0)
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            yield client, feedback


async def test_only_the_named_tools_pass_through_under_plain_names() -> None:
    async with bridged(["search-flight", "not-offered"]) as (client, feedback):
        tools = (await client.list_tools()).tools
        assert [t.name for t in tools] == ["search_flight"]
        out = await client.call_tool("search_flight", {"flyFrom": "ARN", "flyTo": "NCE"})
        assert not out.isError
        assert out.structuredContent == {"flights": [{"from": "ARN", "to": "NCE", "price": 120}]}
        # The service's other tools can't be reached under any name.
        for hidden in ("feedback_to_devs", "feedback-to-devs"):
            refused = await client.call_tool(hidden, {"text": "hello"})
            assert refused.isError and "Unknown tool" in refused.content[0].text
        assert feedback == []


async def test_a_by_hint_service_passes_unlisted_tools_with_their_hints_but_never_hidden_ones() -> None:
    async with bridged(["search-flight"], by_hint=True, hidden=["feedback-to-devs"]) as (client, feedback):
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert set(tools) == {"search_flight", "price_alert"}
        assert tools["price_alert"].annotations.readOnlyHint is True  # the policy reads it
        assert not (await client.call_tool("price_alert", {"route": "ARN-NCE"})).isError
        refused = await client.call_tool("feedback_to_devs", {"text": "hello"})
        assert refused.isError and feedback == []


async def test_a_service_that_is_down_lists_nothing_and_says_why() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    remote = RemoteService(URL, ["search-flight"], http=lambda: httpx.AsyncClient(transport=httpx.MockTransport(down)))
    server = build_server("kiwi", remote, port=0)
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        assert (await client.list_tools()).tools == []
        out = await client.call_tool("search_flight", {"flyFrom": "ARN", "flyTo": "NCE"})
        assert out.isError and "couldn't be reached" in out.content[0].text


async def test_a_tool_gets_foyelles_card_and_the_bridge_serves_it() -> None:
    service, _ = fake_service()
    app = service.streamable_http_app()
    remote = RemoteService(URL, ["search-flight"], http=lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=app)),
                           views=view_uris("kiwi", {"search-flight": "flights"}))
    async with service.session_manager.run():
        server = build_server("kiwi", remote, port=0, titles={"flights": "Flights"})
        async with create_connected_server_and_client_session(server) as client:
            (tool,) = (await client.list_tools()).tools
            assert tool.meta == {"ui": {"resourceUri": "ui://kiwi/flights"}}  # theirs is dropped
            (res,) = (await client.list_resources()).resources
            assert str(res.uri) == "ui://kiwi/flights" and res.title == "Flights"
            page = (await client.read_resource("ui://kiwi/flights")).contents[0]
            assert page.mimeType == "text/html;profile=mcp-app" and "foyelle-flights" in page.text
    # Two tools can share a card (Todoist's find-tasks and find-tasks-by-date): it is served once.
    shared = RemoteService(URL, ["a", "b"], views=view_uris("todoist", {"a": "tasks", "b": "tasks"}))
    async with create_connected_server_and_client_session(build_server("todoist", shared, port=0, titles=VIEW_TITLES)) as client:
        assert [(str(r.uri), r.title) for r in (await client.list_resources()).resources] == [("ui://todoist/tasks", "Tasks")]
    with pytest.raises(sdk.ConfigError, match="bad view name"):
        view_uris("kiwi", {"search-flight": "../x"})


def test_a_json_copy_of_the_data_is_dropped_and_the_summary_kept() -> None:
    from mcp import types as t

    from foyelle_connectors.bridge.server import without_json_copy

    data = {"tasks": [{"id": "1", "content": "Buy oat milk"}]}
    dual = t.CallToolResult(content=[t.TextContent(type="text", text=json.dumps(data)),
                                     t.TextContent(type="text", text="Found 1 task.")], structuredContent=data)
    out = without_json_copy(dual)
    assert [b.text for b in out.content] == ["Found 1 task."] and out.structuredContent == data
    # Only an exact copy goes: other text, and results without data, are untouched.
    other = t.CallToolResult(content=[t.TextContent(type="text", text='{"tasks": []}')], structuredContent=data)
    assert without_json_copy(other) is other
    plain = t.CallToolResult(content=[t.TextContent(type="text", text=json.dumps(data))])
    assert without_json_copy(plain) is plain


def test_names_and_configuration() -> None:
    assert local_name("search-flight") == "search_flight"
    assert local_name("find.tasks-v2") == "find_tasks_v2"
    with pytest.raises(sdk.ConfigError, match="https"):
        RemoteService("http://mcp.service.example/mcp", [])


# ---- Services that need an account (Todoist-style MCP sign-in) ------------------------------

import json  # noqa: E402
import os  # noqa: E402
from urllib.parse import parse_qs, urlparse  # noqa: E402

from mcp import types  # noqa: E402

from foyelle_connectors.sdk.account import AccountStore  # noqa: E402
from foyelle_connectors.sdk.oauth import ServiceSignIn  # noqa: E402

TASKS = "https://ai.tasks.example/mcp"
REDIRECT = "http://127.0.0.1:53111/"


class FakeAccountService(httpx.AsyncBaseTransport):
    """Protected-resource and authorization-server metadata, registration, tokens, and an MCP
    endpoint that wants a bearer token."""

    def __init__(self) -> None:
        mcp = FastMCP("tasks", host="0.0.0.0", streamable_http_path="/mcp")

        @mcp.tool(name="find-tasks", structured_output=True)
        def find_tasks(query: str) -> dict[str, list[str]]:
            return {"tasks": [f"Buy milk ({query})"]}

        self.mcp = mcp
        self.app = httpx.ASGITransport(app=mcp.streamable_http_app())
        self.registrations: list[dict] = []
        self.token_forms: list[dict] = []
        self.valid = {"at-1"}
        self.expires_in = 3600
        self.refresh_ok = True
        self.issued = 0
        self.metadata: dict = {}  # extra authorization-server metadata

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.url.path == "/mcp":
            auth = request.headers.get("authorization", "")
            if auth.removeprefix("Bearer ") not in self.valid:
                return httpx.Response(401, headers={"WWW-Authenticate": 'Bearer resource_metadata="x"'})
            return await self.app.handle_async_request(request)
        if url == "https://ai.tasks.example/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(200, json={"resource": TASKS, "authorization_servers": ["https://tasks.example"],
                                             "scopes_supported": ["data:read_write"]})
        if url == "https://tasks.example/.well-known/oauth-authorization-server":
            return httpx.Response(200, json={
                "issuer": "https://tasks.example", "authorization_endpoint": "https://tasks.example/oauth/authorize",
                "token_endpoint": "https://tasks.example/oauth/token",
                "registration_endpoint": "https://tasks.example/oauth/register",
                "code_challenge_methods_supported": ["S256"], **self.metadata})
        if url == "https://tasks.example/oauth/register":
            body = json.loads(request.content)
            self.registrations.append(body)
            return httpx.Response(201, json={"client_id": f"client-{len(self.registrations)}", **body})
        if url == "https://tasks.example/oauth/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_forms.append(form)
            if form["grant_type"] == "refresh_token" and not self.refresh_ok:
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.issued += 1
            token = f"at-{self.issued}"
            self.valid.add(token)
            return httpx.Response(200, json={"access_token": token, "refresh_token": "rt-1",
                                             "expires_in": self.expires_in, "token_type": "Bearer"})
        return httpx.Response(404)


@asynccontextmanager
async def account_bridge(tmp_path):
    service = FakeAccountService()
    service.valid = set()
    http = lambda: httpx.AsyncClient(transport=service)  # noqa: E731
    signin = ServiceSignIn(TASKS, AccountStore(tmp_path / "account"), http)
    remote = RemoteService(TASKS, ["find-tasks"], http=http, token=signin.access_token, title="Tasks")
    notices: list = []

    async def on_message(message) -> None:
        notices.append(message)

    async with service.mcp.session_manager.run():
        server = build_server("tasks", remote, port=0, signin=signin)
        async with create_connected_server_and_client_session(server, message_handler=on_message) as client:
            yield client, service, notices


async def call(client, tool: str, args: dict | None = None) -> dict:
    out = await client.call_tool(tool, args or {})
    return out.structuredContent if out.structuredContent is not None else {"_text": out.content[0].text}


async def test_signing_in_to_a_service_and_using_its_tools(tmp_path) -> None:
    async with account_bridge(tmp_path) as (client, service, notices):
        names = [t.name for t in (await client.list_tools()).tools]
        assert "find_tasks" not in names and "account_signin_start" in names
        assert (await call(client, "account_status"))["connected"] is False
        refused = await client.call_tool("find_tasks", {"query": "x"})
        assert refused.isError and "Tasks isn't connected" in refused.content[0].text

        started = await call(client, "account_signin_start", {"redirect_uri": REDIRECT})
        q = {k: v[0] for k, v in parse_qs(urlparse(started["auth_url"]).query).items()}
        assert started["auth_url"].startswith("https://tasks.example/oauth/authorize?")
        assert q["client_id"] == "client-1" and q["redirect_uri"] == REDIRECT and q["code_challenge_method"] == "S256"
        assert q["resource"] == TASKS and q["scope"] == "data:read_write"
        reg = service.registrations[0]
        assert reg["redirect_uris"] == [REDIRECT] and reg["token_endpoint_auth_method"] == "none"

        assert (await call(client, "account_signin_finish", {"code": "c-1", "state": q["state"]}))["connected"] is True
        form = service.token_forms[0]
        assert form["code_verifier"] and form["client_id"] == "client-1" and form["resource"] == TASKS
        assert any(isinstance(getattr(n, "root", None), types.ToolListChangedNotification) for n in notices)

        assert "find_tasks" in [t.name for t in (await client.list_tools()).tools]
        assert (await call(client, "find_tasks", {"query": "home"})) == {"tasks": ["Buy milk (home)"]}
        secret = tmp_path / "account" / "account.secret"
        assert oct(os.stat(secret).st_mode & 0o777) == "0o600" and "rt-1" in secret.read_text()
        assert "at-1" not in (tmp_path / "account" / "account.json").read_text()

        assert (await call(client, "account_disconnect"))["removed"] is True
        assert "find_tasks" not in [t.name for t in (await client.list_tools()).tools]


async def test_a_sign_in_must_match_and_come_back_here(tmp_path) -> None:
    async with account_bridge(tmp_path) as (client, service, _):
        bad = await call(client, "account_signin_start", {"redirect_uri": "https://elsewhere.example/"})
        assert bad["connected"] is False and "come back to this computer" in bad["message"]
        await call(client, "account_signin_start", {"redirect_uri": REDIRECT})
        out = await call(client, "account_signin_finish", {"code": "c-1", "state": "not-the-state"})
        assert out["connected"] is False and "doesn't match" in out["message"]
        await call(client, "account_signin_start", {"redirect_uri": REDIRECT})
        out = await call(client, "account_signin_finish", {"error": "access_denied"})
        assert "didn't allow" in out["message"] and service.token_forms == []


async def test_a_sign_in_page_other_than_the_configured_one_is_refused(tmp_path) -> None:
    service = FakeAccountService()
    http = lambda: httpx.AsyncClient(transport=service)  # noqa: E731
    pinned = ServiceSignIn(TASKS, AccountStore(tmp_path / "a"), http, page="https://tasks.example/oauth/authorize")
    assert (await pinned.start(REDIRECT))["auth_url"].startswith("https://tasks.example/oauth/authorize?")
    other = ServiceSignIn(TASKS, AccountStore(tmp_path / "b"), http, page="https://login.tasks.example/authorize")
    with pytest.raises(Exception, match="isn't the one Foyelle knows"):
        await other.start(REDIRECT)
    assert len(service.registrations) == 1  # refused before registering


async def test_an_expired_token_is_renewed_and_a_revoked_one_asks_to_sign_in(tmp_path) -> None:
    async with account_bridge(tmp_path) as (client, service, _):
        service.expires_in = 30  # already inside the renewal margin
        started = await call(client, "account_signin_start", {"redirect_uri": REDIRECT})
        state = parse_qs(urlparse(started["auth_url"]).query)["state"][0]
        await call(client, "account_signin_finish", {"code": "c-1", "state": state})
        assert (await call(client, "find_tasks", {"query": "a"}))["tasks"]
        assert service.token_forms[-1]["grant_type"] == "refresh_token"
        service.refresh_ok = False
        service.valid = set()
        out = await client.call_tool("find_tasks", {"query": "a"})
        assert out.isError and "connected on the Accounts card" in out.content[0].text


# ---- One session per service, kept open --------------------------------------------------------


class Counting(httpx.AsyncBaseTransport):
    """The service's MCP app, counting sessions, and able to end a session or refuse a token once."""

    def __init__(self, app) -> None:
        self.inner = httpx.ASGITransport(app=app)
        self.initializes = 0
        self.end_next = False
        self.refuse_next = False
        self.fail_next = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = request.content.decode() if request.content else ""
            if '"method":"initialize"' in body.replace(" ", ""):
                self.initializes += 1
            elif '"method":"tools/call"' in body.replace(" ", ""):
                if self.end_next:
                    self.end_next = False
                    return httpx.Response(404)  # the service forgot the session (it restarted)
                if self.refuse_next:
                    self.refuse_next = False
                    return httpx.Response(401)
                if self.fail_next:
                    self.fail_next = False
                    return httpx.Response(502)
        return await self.inner.handle_async_request(request)


@asynccontextmanager
async def kept(tools=("search-flight",), token=None):
    service, _ = fake_service()
    counting = Counting(service.streamable_http_app())
    remote = RemoteService(URL, list(tools), http=lambda: httpx.AsyncClient(transport=counting), token=token)
    async with service.session_manager.run():
        try:
            yield remote, counting
        finally:
            await remote.aclose()


async def test_calls_share_one_session() -> None:
    async with kept() as (remote, counting):
        await remote.tools()
        for _ in range(3):
            out = await remote.call("search_flight", {"flyFrom": "ARN", "flyTo": "NCE"})
            assert not out.isError
        assert counting.initializes == 1


async def test_an_ended_session_is_replaced_without_losing_the_call() -> None:
    async with kept() as (remote, counting):
        await remote.call("search_flight", {"flyFrom": "ARN", "flyTo": "NCE"})
        counting.end_next = True
        out = await remote.call("search_flight", {"flyFrom": "ARN", "flyTo": "OSL"})
        assert not out.isError and out.structuredContent["flights"][0]["to"] == "OSL"
        assert counting.initializes == 2


async def test_a_refused_token_is_renewed_once() -> None:
    renewals = []

    async def token(force_refresh: bool = False) -> str:
        renewals.append(force_refresh)
        return "new" if force_refresh else "old"

    async with kept(token=token) as (remote, counting):
        await remote.call("search_flight", {"flyFrom": "ARN", "flyTo": "NCE"})
        counting.refuse_next = True
        out = await remote.call("search_flight", {"flyFrom": "ARN", "flyTo": "NCE"})
        assert not out.isError and renewals[-1] is True


async def test_a_failed_request_fails_the_call_at_once_and_the_next_call_works() -> None:
    async with kept() as (remote, counting):
        await remote.call("search_flight", {"flyFrom": "ARN", "flyTo": "NCE"})
        counting.fail_next = True
        started = time.monotonic()
        out = await remote.call("search_flight", {"flyFrom": "ARN", "flyTo": "NCE"})
        assert out.isError and time.monotonic() - started < 5  # never retried: it may have reached the service
        again = await remote.call("search_flight", {"flyFrom": "ARN", "flyTo": "NCE"})
        assert not again.isError


# ---- The tool list, answered at once ------------------------------------------------------------


class Slow(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport, delay: float) -> None:
        self.inner, self.delay = inner, delay

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        import asyncio

        await asyncio.sleep(self.delay)
        return await self.inner.handle_async_request(request)


async def test_a_restart_answers_from_the_saved_list_at_once(tmp_path) -> None:
    service, _ = fake_service()
    app = service.streamable_http_app()
    cache = tmp_path / "tools.json"
    async with service.session_manager.run():
        first = RemoteService(URL, ["search-flight"], http=lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=app)), cache=cache)
        assert [t.name for t in await first.tools()] == ["search_flight"] and cache.exists()
        await first.aclose()

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    later = RemoteService(URL, ["search-flight"], http=lambda: httpx.AsyncClient(transport=httpx.MockTransport(down)), cache=cache)
    started = time.monotonic()
    assert [t.name for t in await later.tools()] == ["search_flight"]
    assert time.monotonic() - started < 0.5


async def test_a_slow_first_listing_answers_then_tells_the_harness(tmp_path, monkeypatch) -> None:
    import asyncio

    from foyelle_connectors.bridge import server as bridge

    monkeypatch.setattr(bridge, "LIST_WAIT_SECONDS", 0.2)
    service, _ = fake_service()
    slow = Slow(httpx.ASGITransport(app=service.streamable_http_app()), 0.15)
    told = asyncio.Event()

    async def changed() -> None:
        told.set()

    async with service.session_manager.run():
        remote = RemoteService(URL, ["search-flight"], http=lambda: httpx.AsyncClient(transport=slow), cache=tmp_path / "t.json")
        remote.on_tools_changed = changed
        started = time.monotonic()
        assert await remote.tools() == []  # the harness isn't kept waiting
        assert time.monotonic() - started < 0.6
        await asyncio.wait_for(told.wait(), 5)
        assert [t.name for t in await remote.tools()] == ["search_flight"]
        await remote.aclose()


async def test_no_account_no_tools_even_with_a_saved_list(tmp_path) -> None:
    cache = tmp_path / "tools.json"
    cache.write_text(json.dumps([{"name": "find-tasks", "inputSchema": {"type": "object"}}]))
    remote = RemoteService(URL, ["find-tasks"], cache=cache, signed_in=lambda: False)
    assert await remote.tools() == []
    remote.signed_in = lambda: True
    assert [t.name for t in await remote.tools()] == ["find_tasks"]


FOYELLE_CLIENT = "https://foyelle.example/oauth/client.json"


async def test_a_published_client_document_replaces_registration_where_the_server_supports_it(tmp_path) -> None:
    service = FakeAccountService()
    service.metadata = {"client_id_metadata_document_supported": True}
    http = lambda: httpx.AsyncClient(transport=service)  # noqa: E731
    signin = ServiceSignIn(TASKS, AccountStore(tmp_path / "a"), http, client_metadata_url=FOYELLE_CLIENT)
    started = await signin.start(REDIRECT)
    q = {k: v[0] for k, v in parse_qs(urlparse(started["auth_url"]).query).items()}
    assert q["client_id"] == FOYELLE_CLIENT and service.registrations == []
    await signin.finish("c-1", q["state"])
    assert service.token_forms[0]["client_id"] == FOYELLE_CLIENT
    # Without a document configured, or on a server without support, Foyelle registers as before.
    plain = ServiceSignIn(TASKS, AccountStore(tmp_path / "b"), http)
    await plain.start(REDIRECT)
    assert len(service.registrations) == 1
    service.metadata = {}
    other = ServiceSignIn(TASKS, AccountStore(tmp_path / "c"), http, client_metadata_url=FOYELLE_CLIENT)
    await other.start(REDIRECT)
    assert len(service.registrations) == 2


async def test_a_server_with_only_client_documents_needs_foyelles(tmp_path) -> None:
    service = FakeAccountService()
    service.metadata = {"client_id_metadata_document_supported": True, "registration_endpoint": None}
    http = lambda: httpx.AsyncClient(transport=service)  # noqa: E731
    with pytest.raises(Exception, match="doesn't let Foyelle register"):
        await ServiceSignIn(TASKS, AccountStore(tmp_path / "a"), http).start(REDIRECT)
    started = await ServiceSignIn(TASKS, AccountStore(tmp_path / "b"), http, client_metadata_url=FOYELLE_CLIENT).start(REDIRECT)
    assert "client_id=https" in started["auth_url"]
    with pytest.raises(Exception, match="isn't a secure address"):
        ServiceSignIn(TASKS, AccountStore(tmp_path / "c"), http, client_metadata_url="http://foyelle.example/c.json")
