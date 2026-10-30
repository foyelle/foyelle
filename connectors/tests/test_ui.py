"""Every ui:// view is a self-contained MCP App, and the shared runtime speaks SEP-1865."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP

from foyelle_connectors.calendar.server import build_server as calendar_server
from foyelle_connectors.calendar.service import CalendarService, CalendarSettings
from foyelle_connectors.email.backend import MailConfig
from foyelle_connectors.email.server import build_server as email_server
from foyelle_connectors.email.service import EmailService
from foyelle_connectors.files.core import FileService, parse_roots
from foyelle_connectors.files.server import build_server as files_server
from foyelle_connectors.sdk import RESOURCE_MIME_TYPE
from foyelle_connectors.shell.server import ShellConfig
from foyelle_connectors.shell.server import build_server as shell_server
from foyelle_connectors.system.server import build_server as system_server
from foyelle_connectors.system.service import SystemConfig, SystemService
from foyelle_connectors.ui import asset
from foyelle_connectors.web.server import build_server as web_server
from foyelle_connectors.web.service import SearchConfig, WebService

NODE = shutil.which("node")


def all_servers(tmp_path: Path) -> dict[str, FastMCP]:
    return {
        "shell": shell_server(ShellConfig(tmp_path, tmp_path / "s.json", tmp_path / "o.json", tmp_path / "a.jsonl", {}, "none")),
        "files": files_server(FileService(parse_roots(f"t={tmp_path}"))),
        "email": email_server(EmailService(None, MailConfig("a@b.c", "imap.b.c"))),  # type: ignore[arg-type]
        "calendar": calendar_server(CalendarService(None, CalendarSettings())),  # type: ignore[arg-type]
        "web": web_server(WebService(SearchConfig("searxng", url="http://127.0.0.1:8888"))),
        "system": system_server(SystemService(SystemConfig(tmp_path, "h"))),
    }


async def views(tmp_path: Path) -> list[tuple[str, str, str]]:
    out = []
    for name, server in all_servers(tmp_path).items():
        tool_uris = {t.meta["ui"]["resourceUri"] for t in await server.list_tools() if t.meta}
        resources = {str(r.uri): r for r in await server.list_resources()}
        assert tool_uris <= set(resources), f"{name}: tool points at a missing view"
        for uri, res in resources.items():
            assert uri.startswith(f"ui://{name}/")
            assert res.mimeType == RESOURCE_MIME_TYPE
            [content] = list(await server.read_resource(uri))
            assert content.mime_type == RESOURCE_MIME_TYPE
            out.append((name, uri, content.content))
    return out


async def test_views_are_self_contained(tmp_path: Path) -> None:
    pages = await views(tmp_path)
    assert {uri for _, uri, _ in pages} == {
        "ui://shell/status", "ui://shell/memory", "ui://shell/settings", "ui://shell/themes", "ui://shell/audit", "ui://shell/privacy",
        "ui://files/browser",
        "ui://email/inbox", "ui://email/compose", "ui://calendar/agenda", "ui://web/results", "ui://system/review",
    }
    for _, uri, html in pages:
        assert isinstance(html, str) and html.startswith("<!doctype html>"), uri
        lowered = html.lower()
        assert not re.search(r"""(src|href|action)\s*=\s*["']?\s*(https?:)?//""", lowered), uri
        assert not re.search(r"<script[^>]*\bsrc\s*=", lowered), uri
        assert "<link" not in lowered and "@import" not in lowered and "<iframe" not in lowered, uri
        assert not re.search(r"url\(\s*['\"]?(https?:)?//", lowered), uri
        assert not re.search(r"\b(fetch|xmlhttprequest|websocket|eventsource|sendbeacon)\s*\(", lowered.replace("fetchpriority", "")), uri
        assert not re.search(r"\.(inner|outer)html\s*=|insertadjacenthtml|document\.write", lowered), uri
        assert "prefers-color-scheme" in lowered and "system-ui" in lowered, uri


async def test_every_card_says_what_it_shows(tmp_path: Path) -> None:
    # The canvas's card gallery shows each card's title and, in one line, its description.
    for name, server in all_servers(tmp_path).items():
        for res in await server.list_resources():
            assert res.title and not str(res.title).startswith("ui://"), res.uri
            text = res.description or ""
            assert text.endswith(".") and "\n" not in text and 20 <= len(text) <= 110, (res.uri, text)


@pytest.mark.skipif(NODE is None, reason="node not installed")
async def test_view_scripts_parse(tmp_path: Path) -> None:
    for _, uri, html in await views(tmp_path):
        for i, script in enumerate(re.findall(r"<script>(.*?)</script>", html, re.S)):
            path = tmp_path / f"{uri.replace('/', '_').replace(':', '')}_{i}.js"
            path.write_text(script)
            proc = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True)
            assert proc.returncode == 0, f"{uri}: {proc.stderr}"


HARNESS = r"""
const posted = [];
const listeners = [];
const parent = { postMessage: (m, origin) => posted.push({ m, origin }) };
const style = { setProperty() {} };
global.window = { parent, addEventListener: (t, f) => listeners.push(f) };
global.document = { documentElement: { style, setAttribute(k, v) { this[k] = v; }, scrollWidth: 300, scrollHeight: 200 },
                    body: {}, querySelector: () => null };
global.requestAnimationFrame = (f) => { f(); return 1; };
global.cancelAnimationFrame = () => {};
global.Node = function () {};
const fromHost = (data) => listeners.forEach((f) => f({ source: parent, data }));
const fromElsewhere = (data) => listeners.forEach((f) => f({ source: {}, data }));
eval(require("fs").readFileSync(process.argv[2], "utf8"));
const V = window.FoyelleView;
(async () => {
  const seen = [];
  V.onToolResult = (r) => seen.push(["result", r]);
  V.onToolInput = (a) => seen.push(["input", a]);
  const ready = V.connect({ name: "t", version: "1" });
  const init = posted[0].m;
  fromElsewhere({ jsonrpc: "2.0", id: init.id, result: {} });  // ignored: wrong source
  fromHost({ jsonrpc: "2.0", id: init.id, result: { protocolVersion: "2026-01-26", hostInfo: { name: "canvas" },
    hostCapabilities: {}, hostContext: { theme: "dark", toolInfo: { tool: { name: "x" } } } } });
  await ready;
  fromHost({ jsonrpc: "2.0", method: "ui/notifications/tool-input", params: { arguments: { a: 1 } } });
  fromHost({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: { content: [], structuredContent: { ok: 1 } } });
  fromHost({ jsonrpc: "2.0", id: 77, method: "ping", params: {} });
  fromHost({ jsonrpc: "2.0", id: 78, method: "ui/resource-teardown", params: {} });
  fromHost({ jsonrpc: "2.0", id: 79, method: "bogus", params: {} });
  const call = V.callTool("list", { path: "" });
  const req = posted[posted.length - 1].m;
  fromHost({ jsonrpc: "2.0", id: req.id, result: { content: [{ type: "text", text: "{\"x\":2}" }] } });
  const res = await call;
  const msg = V.sendMessage("hello");
  const mreq = posted[posted.length - 1].m;
  fromHost({ jsonrpc: "2.0", id: mreq.id, error: { code: -32000, message: "denied" } });
  let err = null;
  try { await msg; } catch (e) { err = e.message; }
  console.log(JSON.stringify({ posted: posted.map((p) => p.m), origins: posted.map((p) => p.origin), seen,
    data: V.data(res), err, theme: document.documentElement["data-theme"] }));
})();
"""


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_runtime_protocol(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime.js"
    runtime.write_text(asset("runtime.js"))
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS)
    proc = subprocess.run([NODE, str(harness), str(runtime)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    posted = out["posted"]
    init = posted[0]
    assert init["method"] == "ui/initialize" and init["params"]["protocolVersion"] == "2026-01-26"
    assert init["params"]["appInfo"] == {"name": "t", "version": "1"} and "appCapabilities" in init["params"]
    methods = [m.get("method") for m in posted]
    assert methods.index("ui/notifications/initialized") > 0
    assert "ui/notifications/size-changed" in methods
    assert {"jsonrpc": "2.0", "id": 77, "result": {}} in posted
    assert {"jsonrpc": "2.0", "id": 78, "result": {}} in posted
    assert any(m.get("id") == 79 and m.get("error", {}).get("code") == -32601 for m in posted)
    call = next(m for m in posted if m.get("method") == "tools/call")
    assert call["params"] == {"name": "list", "arguments": {"path": ""}}
    message = next(m for m in posted if m.get("method") == "ui/message")
    assert message["params"] == {"role": "user", "content": [{"type": "text", "text": "hello"}]}
    assert out["seen"] == [["input", {"a": 1}], ["result", {"content": [], "structuredContent": {"ok": 1}}]]
    assert out["data"] == {"x": 2} and out["err"] == "denied" and out["theme"] == "dark"
    assert all(o == "*" for o in out["origins"])
