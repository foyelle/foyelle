"""Real Foyelle connectors over sample data, for designing the canvas on a laptop (npm run dev).

Builds the Files, Shell (status, memory, settings, audit), System and Workshop connectors from
connectors/ against sample folders under canvas/.dev-state/ and answers the mock harness on
http://127.0.0.1:5175:

  GET  /surfaces                         every ui:// resource, like the gateway's surface.list
  GET  /resource?server=..&uri=..        {"html", "csp"} of a surface
  POST /call {server, name, arguments}   a CallToolResult: content, structuredContent, isError

Run it with the connectors' environment (scripts/dev.mjs does): uv run --project connectors
python canvas/dev/tools_host.py [--reset]. Nothing leaves the laptop: no model, no network.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx

from foyelle_connectors.bridge.server import VIEW_TITLES, RemoteService, view_uris
from foyelle_connectors.bridge.server import build_server as bridge_server
from foyelle_connectors.calendar.account import CalendarAccount, CheckError
from foyelle_connectors.calendar.server import build_server as calendar_server
from foyelle_connectors.email.account import MailAccount
from foyelle_connectors.email.backend import MailCheckError, RawMessage
from foyelle_connectors.email.server import build_server as email_server
from foyelle_connectors.files.core import FileService, parse_roots
from foyelle_connectors.sdk import google
from foyelle_connectors.sdk.account import AccountStore
from foyelle_connectors.sdk.oauth import ServiceSignIn
from foyelle_connectors.files.server import build_server as files_server
from foyelle_connectors.shell.server import ShellConfig
from foyelle_connectors.shell.server import build_server as shell_server
from foyelle_connectors.system.service import SystemConfig, SystemService
from foyelle_connectors.system.server import build_server as system_server
from foyelle_connectors.workshop.server import build_server as workshop_server
from foyelle_connectors.workshop.store import Store

PORT = int(os.environ.get("FOYELLE_TOOLS_PORT", "5175"))
# FOYELLE_DEV_STATE: another folder for the sample data (canvas/demo/record.mjs records from its own).
STATE = Path(os.environ.get("FOYELLE_DEV_STATE") or Path(__file__).resolve().parent.parent / ".dev-state")

def make_png(w: int = 64, h: int = 48) -> bytes:
    """A small warm gradient PNG, so the file browser shows a picture."""
    import struct
    import zlib

    rows = b"".join(b"\x00" + bytes(v for x in range(w) for v in (226, 120 + x, 60 + y * 2)) for y in range(h))
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300" viewBox="0 0 400 300">
  <rect width="400" height="300" fill="#fbf6ee"/>
  <circle cx="140" cy="150" r="80" fill="#e2783c"/>
  <rect x="230" y="70" width="120" height="160" rx="12" fill="#2c5e8a"/>
</svg>
"""

FAKE_NIX = r'''
import os, pathlib, sys, time
flake, root = sys.argv[1:3]
time.sleep(2)  # builds take a moment
rev = flake.split("rev=")[1].split("#")[0] if "rev=" in flake else "dev"
out = pathlib.Path(root) / f"{rev[:12]}-nixos-system-foyelle"
(out / "bin").mkdir(parents=True, exist_ok=True)
for name in ("kernel", "initrd"):
    (out / name).write_text(name)
(out / "kernel-params").write_text("quiet")
(out / "bin" / "switch-to-configuration").write_text("#!/bin/sh\n")
print(out)
'''
FAKE_NVD = r'''
import sys
print("Version changes:\n[U.]  #1  firefox  130.0 -> 131.0\n[A+]  #2  hello  2.12")
'''


def git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "Foyelle", "GIT_AUTHOR_EMAIL": "foyelle@localhost",
           "GIT_COMMITTER_NAME": "Foyelle", "GIT_COMMITTER_EMAIL": "foyelle@localhost", "GIT_CONFIG_GLOBAL": "/dev/null"}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env).stdout


def seed(state: Path) -> None:
    """Sample data, created once (--reset starts over)."""
    docs = state / "Documents"
    for sub in ("Letters", "Recipes", "images"):
        (docs / sub).mkdir(parents=True, exist_ok=True)
    (docs / "README.md").write_text("# My documents\n\nLetters, recipes and photos live here.\n")
    (docs / "Shopping list.txt").write_text("Milk\nBread\nApples\nCoffee\n")
    (docs / "Letters" / "Thank you, Rita.md").write_text("Dear Rita,\n\nThank you for the lovely dinner on Saturday.\n\nWarmly,\nSam\n")
    (docs / "Recipes" / "Orange cake.md").write_text("# Orange cake\n\n- 250 g flour\n- 200 g sugar\n- 2 oranges\n")
    (docs / "Budget 2027.csv").write_text("month,food,rent,fun\nJan,320,950,80\nFeb,300,950,120\n")
    (docs / "images" / "sunset.png").write_bytes(make_png())
    (docs / "images" / "shapes.svg").write_text(SVG)

    mem = state / "memories"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "USER.md").write_text("User's name is Sam.\n§\nLives in Lisbon; prefers Celsius and 24-hour time.\n")
    (mem / "MEMORY.md").write_text("Rita is Sam's friend; birthday 3 March.\n§\nThe dentist is Dr. Costa, Tuesdays.\n")

    (state / "settings").mkdir(exist_ok=True)
    (state / "settings" / "enforced.json").write_text(json.dumps({
        "model": {"route": "remote", "provider": "openrouter", "main": "deepseek/deepseek-v4.1-flash",
                  "local": "qwen3:4b-instruct"},
        "ui": {"theme": "system"}, "approvals": {"mode": "manual"},
    }, indent=2))
    (state / "settings" / "overrides.json").write_text(json.dumps({"ui": {"natural_scroll": True}}, indent=2))
    # A placeholder, so the remote route counts as set up; nothing here talks to a provider.
    (state / "harness.env").write_text("OPENROUTER_API_KEY=dev-placeholder\n")

    audit = state / "audit.jsonl"
    now = time.time()
    # A day of calls, oldest first; the last few are this morning, for the privacy panel.
    rows = [
        ("files", "mcp__files__list", "allow", "read tier", "read", None, "ok"),
        ("files", "mcp__files__write", "allow", "reversible tier", "reversible", None, "ok"),
        ("web", "mcp__web__search", "allow", "read tier", "read", None, "ok"),
        ("shell", "mcp__shell__memory_read", "allow", "read tier", "read", None, "ok"),
        ("system", "mcp__system__apply", "ask", "system change: review the diff before it is applied", "system",
         "approved", "ok"),
        ("weather", "mcp__weather__current_city", "allow", "read tier", "read", None, "ok"),
    ]
    today = [
        ("web", "mcp__web__search", "allow", "read tier", "read", None, "ok"),
        ("web", "mcp__web__fetch", "allow", "read tier", "read", None, "ok"),
        ("files", "mcp__files__read", "ask", "send files content to a remote model for this session", "read",
         "approved", "ok"),
        ("files", "mcp__files__list", "ask", "send files content to a remote model for this session", "read",
         "approved", "ok"),
        ("email", "mcp__email__send", "ask", "irreversible or outbound action", "outbound", "approved", "ok"),
        ("email", "mcp__email__send", "ask", "irreversible or outbound action", "outbound", "denied", None),
        ("web", "mcp__web__search", "allow", "read tier", "read", None, "ok"),
    ]
    midnight = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
    calls = [(now - 86400 + i * 1700, rows[i % len(rows)]) for i in range(24)]
    # Spread this morning's calls between midnight and now.
    calls += [(midnight + (now - midnight) * (i + 1) / (len(today) + 1), r) for i, r in enumerate(today)]
    with audit.open("w") as fh:
        for when, (connector, tool, decision, reason, tier, approval, status) in sorted(calls, key=lambda c: c[0]):
            ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(when)) + "Z"
            fh.write(json.dumps({"ts": ts, "connector": connector, "tool": tool, "decision": decision, "reason": reason,
                                 "origin": "model", "tier": tier, "session_id": "dev", "approval": approval,
                                 "args_digest": "sha256:dev", "duration_ms": 12, "status": status,
                                 "error_type": None}) + "\n")

    repo = state / "config"
    repo.mkdir(exist_ok=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "flake.nix").write_text("{ outputs = _: { }; }\n")
    (repo / "configuration.nix").write_text("{ pkgs, ... }:\n{\n  time.timeZone = \"Europe/Lisbon\";\n}\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "Initial configuration")


class FakeHelper(threading.Thread):
    """The apply helper's socket: every apply 'switches'; status reports the last one."""

    def __init__(self, path: Path) -> None:
        super().__init__(daemon=True)
        self.path, self.last = path, None
        path.unlink(missing_ok=True)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(path))
        self.sock.listen()

    def run(self) -> None:
        gen = 41
        while True:
            conn, _ = self.sock.accept()
            with conn:
                req = json.loads(conn.makefile().readline() or "null") or {}
                if req.get("status"):
                    reply = {"ok": True, "generation": gen, "last": self.last}
                elif req.get("disk") == "status":
                    reply = {"ok": True, "encrypted": True, "initial_passphrase": True, "prompt_open": False}
                elif req.get("recovery") == "status":
                    reply = {"ok": True, "enabled": True, "password_set": False, "prompt_open": False}
                elif "disk" in req or "recovery" in req:
                    reply = {"ok": True, "status": "prompt-open", "message": "(dev) The prompt would open on tty8."}
                else:
                    time.sleep(1.5)
                    gen += 1
                    reply = {"ok": True, "status": "switched", "generation": gen, "proposal_id": req.get("proposal_id"),
                             "commit": req.get("commit"), "health": "ok", "rolled_back": False}
                    self.last = reply
                conn.sendall((json.dumps(reply) + "\n").encode())


def build(state: Path) -> dict[str, Any]:
    for name, body in (("fake_nix.py", FAKE_NIX), ("fake_nvd.py", FAKE_NVD)):
        (state / name).write_text(body)
    store = state / "store"
    store.mkdir(exist_ok=True)
    current = store / "current-system"
    if not current.exists():
        (current / "bin").mkdir(parents=True)
        for name in ("kernel", "initrd"):
            (current / name).write_text(name)
    profiles = state / "profiles"
    profiles.mkdir(exist_ok=True)
    if not (profiles / "system-41-link").exists():
        os.symlink(current, profiles / "system-41-link")
    sock = Path(f"/tmp/foyelle-dev-{os.getuid()}-{PORT}.sock")
    FakeHelper(sock).start()
    system = SystemService(SystemConfig(
        repo=state / "config", host="foyelle", apply_socket=sock, current_system=current, profiles_dir=profiles,
        build_command=[sys.executable, str(state / "fake_nix.py"), "{flake}", str(store)],
        nvd_command=[sys.executable, str(state / "fake_nvd.py"), "{current}", "{out}"], build_wait=5,
    ))
    if not system.proposals():
        system.propose("Install Firefox", "Adds the Firefox web browser.",
                       {"configuration.nix": "{ pkgs, ... }:\n{\n  time.timeZone = \"Europe/Lisbon\";\n"
                                              "  environment.systemPackages = [ pkgs.firefox ];\n}\n"})
    files = FileService(parse_roots(f"documents={state / 'Documents'}:rw"))
    if not files.versions("documents/Shopping list.txt")["versions"]:
        files.write("documents/Shopping list.txt", "Milk\nBread\nApples\nCoffee\nCardamom\n", overwrite=True)
    workshop = Store(state / "workshop")
    if not workshop.all():
        workshop.save("recent-documents", "Recent documents", RECENT_SURFACE, "The latest files in Documents")
    # Every connector "port" points at this host, so the status surface shows them running.
    shell = ShellConfig(
        memory_dir=state / "memories", settings_file=state / "settings" / "enforced.json",
        overrides_file=state / "settings" / "overrides.json", audit_log=state / "audit.jsonl",
        connectors={n: PORT for n in ("files", "shell", "system", "web", "workshop", "sysmon", "email", "calendar")},
        health_method="tcp", harness_env=state / "harness.env",
        remote_key_names=("OPENROUTER_API_KEY",), remote_label="OpenRouter",
    )
    # Mail and calendar run the real connector code (account checks, storage, tools) on dev
    # backends: nothing reaches a provider. Any password works except "wrong".
    # "Sign in with Google" against a stand-in token endpoint; the dev canvas fakes the browser
    # (FOYELLE_DEV_FAKE_SIGNIN) and answers with the code "4/dev".
    gclient = google.GoogleClient("dev.apps.googleusercontent.com", "dev")
    mail = MailAccount(AccountStore(state / "accounts" / "email"), make_backend=DevMail, google=gclient,
                       signin=google.SignIn(gclient, http=DevGoogle(google.MAIL_SCOPE).http))
    cal = CalendarAccount(AccountStore(state / "accounts" / "calendar"), ZoneInfo("Europe/Lisbon"),
                          make_backend=lambda url, user, password, name, bearer=False: DevCalendar(password),
                          google=gclient, signin=google.SignIn(gclient, http=DevGoogle(google.CALENDAR_SCOPE).http))
    # A service with an account (Todoist through its bridge): the real bridge and sign-in code
    # against stand-ins for Todoist's sign-in server and its MCP server (a few sample tasks).
    import httpx

    dev_tasks = DevTasks()
    tasks_http = lambda: httpx.AsyncClient(transport=dev_tasks)  # noqa: E731
    tasks_signin = ServiceSignIn(DevTasks.MCP, AccountStore(state / "accounts" / "todoist"), tasks_http,
                                 page="https://todoist.com/oauth/authorize")
    tasks = RemoteService(DevTasks.MCP, ["find-tasks", "find-tasks-by-date", "find-projects", "add-tasks", "update-tasks",
                                         "complete-tasks", "uncomplete-tasks"],
                          http=tasks_http, token=tasks_signin.access_token, title="Todoist",
                          views=view_uris("todoist", {"find-tasks": "tasks", "find-tasks-by-date": "tasks"}))
    BACKGROUND.append(dev_tasks.mcp.session_manager.run)
    # Flight search through the bridge, against Kiwi's real server (no account; needs the network).
    kiwi = bridge_server("kiwi", RemoteService("https://mcp.kiwi.com", ["search-flight"], title="Kiwi.com",
                                               views=view_uris("kiwi", {"search-flight": "flights"})),
                         port=0, titles=VIEW_TITLES)
    return {"files": files_server(files, port=0), "shell": shell_server(shell, port=0), "design": design_server(),
            "todoist": bridge_server("todoist", tasks, port=0, signin=tasks_signin, titles=VIEW_TITLES), "kiwi": kiwi,
            "system": system_server(system, port=0), "workshop": workshop_server(workshop, port=0),
            "email": email_server(mail, port=0), "calendar": calendar_server(cal, port=0)}


def _mail(uid: str, sender: str, subject: str, body: str, days_ago: int) -> RawMessage:
    date = time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime(time.time() - days_ago * 86400))
    data = (f"From: {sender}\r\nTo: you@example.org\r\nSubject: {subject}\r\nDate: {date}\r\n"
            f"Message-ID: <{uid}@dev.foyelle>\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n").encode()
    return RawMessage(uid, [] if days_ago < 1 else ["\\Seen"], len(data), data)


class DevGoogle:
    """Google's token endpoint for the dev canvas."""

    def __init__(self, scope: str) -> None:
        self.scope = scope

    def handler(self, request):
        import httpx

        if str(request.url) == google.TOKEN_URL:
            import base64 as b64
            claims = b64.urlsafe_b64encode(json.dumps({"email": "you@gmail.com", "email_verified": True}).encode()).decode()
            return httpx.Response(200, json={"access_token": "dev-at", "refresh_token": "dev-rt", "expires_in": 3599,
                                             "id_token": f"h.{claims}.s", "scope": f"openid email {self.scope}"})
        return httpx.Response(404)

    def http(self):
        import httpx

        return httpx.Client(transport=httpx.MockTransport(self.handler))


def design_server() -> Any:
    """Dev only: design explorations (canvas/dev/design/) as cards."""
    from foyelle_connectors import sdk

    server = sdk.create_server("design", "Design explorations for Foyelle (development only).", port=0)
    uri = "ui://design/directions"
    sdk.register_html_page(server, "directions", uri, Path(__file__).parent / "design" / "directions.html",
                           title="Design directions")

    @server.tool(title="Design directions", meta=sdk.ui_meta(uri), structured_output=True)
    def show_directions() -> dict[str, int]:
        """Show the design directions page."""
        return {"directions": 12}

    glass = "ui://design/glass"
    sdk.register_html_page(server, "glass", glass, Path(__file__).parent / "design" / "glass.html",
                           title="Ember glass variations")

    @server.tool(title="Ember glass variations", meta=sdk.ui_meta(glass), structured_output=True)
    def show_glass_variations() -> dict[str, int]:
        """Show the Ember glass variations page."""
        return {"variations": 10}

    y2026 = "ui://design/2026"
    sdk.register_html_page(server, "2026", y2026, Path(__file__).parent / "design" / "2026.html",
                           title="Six directions from 2026")

    @server.tool(title="Six directions from 2026", meta=sdk.ui_meta(y2026), structured_output=True)
    def show_2026_directions() -> dict[str, int]:
        """Show the six directions drawn from 2026's visual culture."""
        return {"directions": 6}

    ember = "ui://design/ember"
    sdk.register_html_page(server, "ember", ember, Path(__file__).parent / "design" / "ember.html",
                           title="The ember, ten characters")

    @server.tool(title="The ember, ten characters", meta=sdk.ui_meta(ember), structured_output=True)
    def show_ember_characters() -> dict[str, int]:
        """Show ten takes on the ember as a character."""
        return {"characters": 10}

    return server


# Async context managers the tools host keeps open (stand-in servers' session managers).
BACKGROUND: list[Any] = []


class DevTasks(httpx.AsyncBaseTransport):
    """Todoist for the dev canvas: its sign-in server (discovery, registration, tokens) and an MCP
    server with Todoist's tool names and result shapes over a few tasks kept in memory."""

    MCP = "https://ai.todoist.net/mcp"

    def __init__(self) -> None:
        import datetime as dt

        import httpx
        from mcp.server.fastmcp import FastMCP

        today = dt.date.today()
        day = lambda n: (today + dt.timedelta(days=n)).isoformat()  # noqa: E731
        self.projects = [{"id": "inbox", "name": "Inbox", "inboxProject": True},
                         {"id": "home", "name": "Home", "inboxProject": False},
                         {"id": "trip", "name": "Istanbul trip", "inboxProject": False}]
        self.tasks = [
            {"content": "Pay the electricity bill", "dueDate": day(-1), "priority": "p1", "projectId": "home", "labels": ["bills"]},
            {"content": "Call the dentist", "description": "Ask about Tuesday or Thursday morning.", "dueDate": day(0) + "T09:00:00",
             "priority": "p2", "projectId": "inbox"},
            {"content": "Buy milk, bread and coffee", "dueDate": day(0), "priority": "p4", "projectId": "home", "labels": ["shopping"]},
            {"content": "Water the plants", "dueDate": day(1), "priority": "p4", "projectId": "home", "recurring": "every 3 days"},
            {"content": "Check passport expiry", "dueDate": day(3), "priority": "p3", "projectId": "trip", "deadlineDate": day(20)},
            {"content": "Book airport transfer", "dueDate": day(6), "priority": "p4", "projectId": "trip"},
            {"content": "Ideas for Rita's birthday", "priority": "p4", "projectId": "inbox"},
        ] + [{"content": f"Someday: {thing}", "priority": "p4", "projectId": "inbox"} for thing in (
            "learn to make bread", "repaint the hall", "sort the photo albums", "visit the botanical garden",
            "try the new ramen place", "fix the bike light", "plan a picnic", "read Middlemarch", "frame the map")]
        for i, t in enumerate(self.tasks):
            t.setdefault("description", "")
            t.setdefault("recurring", False)
            t.update(id=f"t{i + 1}", checked=False)
        self.mcp = FastMCP("todoist-dev", host="0.0.0.0", streamable_http_path="/mcp")
        self.app = httpx.ASGITransport(app=self.mcp.streamable_http_app())
        open_tasks = lambda: [t for t in self.tasks if not t["checked"]]  # noqa: E731

        def listed(ts: list[dict], limit: int, cursor: str | None) -> dict:
            """Like Todoist: a page of `limit` (10 unless asked), and a cursor for the next one."""
            start = int(cursor or 0)
            page = ts[start:start + limit]
            more = start + limit < len(ts)
            out = {"tasks": page, "totalCount": len(page), "hasMore": more, "appliedFilters": {}}
            if more:
                out["nextCursor"] = str(start + limit)
            return out

        @self.mcp.tool(name="find-tasks")
        def find_tasks(searchText: str = "", projectId: str = "", filter: str = "", limit: int = 10, cursor: str | None = None) -> dict:
            if not (searchText or projectId or filter):  # Todoist refuses a search without a filter
                raise ValueError("At least one filter must be provided: searchText, projectId, sectionId, parentId, "
                                 "responsibleUser, labels, filter, or filterIdOrName")
            before = filter[len("due before: "):] if filter.startswith("due before: ") else None
            return listed([t for t in open_tasks() if searchText.lower() in t["content"].lower()
                           and (not projectId or t["projectId"] == projectId)
                           and (before is None or (t.get("dueDate") or "9999")[:10] < before)], limit, cursor)

        @self.mcp.tool(name="find-tasks-by-date")
        def find_tasks_by_date(startDate: str = "today", daysCount: int = 1, overdueOption: str = "include-overdue",
                               limit: int = 10, cursor: str | None = None) -> dict:
            start = today if startDate == "today" else dt.date.fromisoformat(startDate)
            end = (start + dt.timedelta(days=daysCount)).isoformat()
            keep = lambda d: d and ((overdueOption != "exclude-overdue" and d[:10] < start.isoformat())  # noqa: E731
                                    or start.isoformat() <= d[:10] < end)
            return listed([t for t in open_tasks() if keep(t.get("dueDate"))], limit, cursor)

        @self.mcp.tool(name="find-projects")
        def find_projects(limit: int = 50) -> dict:
            return {"projects": self.projects, "totalCount": len(self.projects), "hasMore": False, "appliedFilters": {}}

        @self.mcp.tool(name="add-tasks")
        def add_tasks(tasks: list[dict]) -> dict:
            made = []
            for spec in tasks:
                due_string = spec.get("dueString") or ""
                due = {"today": day(0), "tomorrow": day(1), "next week": day(7)}.get(due_string.lower(), due_string or None)
                t = {"id": f"t{len(self.tasks) + 1}", "content": spec["content"], "description": "", "priority": "p4",
                     "projectId": spec.get("projectId") or "inbox", "recurring": False, "checked": False}
                if due:
                    t["dueDate"] = due
                self.tasks.append(t)
                made.append(t)
            return {"tasks": made, "totalCount": len(made), "failures": [], "totalRequested": len(tasks),
                    "successCount": len(made), "failureCount": 0}

        def mark(ids: list[str], checked: bool) -> dict:
            done = [t["id"] for t in self.tasks if t["id"] in ids]
            for t in self.tasks:
                if t["id"] in ids:
                    t["checked"] = checked
            return {"completed": done, "failures": [], "totalRequested": len(ids), "successCount": len(done), "failureCount": 0}

        @self.mcp.tool(name="complete-tasks")
        def complete_tasks(ids: list[str]) -> dict:
            return mark(ids, True)

        @self.mcp.tool(name="uncomplete-tasks")
        def uncomplete_tasks(ids: list[str]) -> dict:
            return mark(ids, False)

        @self.mcp.tool(name="update-tasks")
        def update_tasks(tasks: list[dict]) -> dict:
            changed = []
            for spec in tasks:
                for t in self.tasks:
                    if t["id"] == spec["id"] and spec.get("dueString"):
                        words = {"today": day(0), "tomorrow": day(1), "next week": day(7)}
                        t["dueDate"] = words.get(spec["dueString"], spec["dueString"])
                        changed.append(t)
            return {"tasks": changed, "totalCount": len(changed)}

    async def handle_async_request(self, request):
        if request.url.host == "ai.todoist.net" and request.url.path == "/mcp":
            return await self.app.handle_async_request(request)
        return self.handler(request)

    def handler(self, request):
        import httpx

        url = str(request.url)
        if url.startswith("https://ai.todoist.net/.well-known/oauth-protected-resource"):
            return httpx.Response(200, json={"resource": self.MCP, "authorization_servers": ["https://todoist.com"],
                                             "scopes_supported": ["data:read_write"]})
        if url == "https://todoist.com/.well-known/oauth-authorization-server":
            return httpx.Response(200, json={"authorization_endpoint": "https://todoist.com/oauth/authorize",
                                             "token_endpoint": "https://todoist.com/oauth/access_token",
                                             "registration_endpoint": "https://todoist.com/oauth/register",
                                             "code_challenge_methods_supported": ["S256"]})
        if url == "https://todoist.com/oauth/register":
            return httpx.Response(201, json={"client_id": "dev-client"})
        if url == "https://todoist.com/oauth/access_token":
            return httpx.Response(200, json={"access_token": "dev-at", "refresh_token": "dev-rt", "expires_in": 3600})
        return httpx.Response(503)


class DevMail:
    """A mailbox with a few messages, for the dev canvas."""

    def __init__(self, config, password) -> None:
        self.config, self.password = config, password
        self.messages = {
            "INBOX": {m.uid: m for m in (
                _mail("3", "Rita <rita@example.org>", "Dinner on Saturday?", "Are you free on Saturday at seven?", 0),
                _mail("2", "Dentist <reception@example.org>", "Appointment reminder", "Tuesday at nine.", 1),
                _mail("1", "Library <news@example.org>", "New books this week", "Five new books arrived.", 3),
            )},
            "Drafts": {}, "Sent": {},
        }

    def check(self):
        if self.password() == "wrong":
            raise MailCheckError("imap-login", "the mail server did not accept the user name and password")
        return {"drafts": "Drafts", "sent": "Sent"}

    def search(self, folder, criteria, limit):
        return list(self.messages.get(folder, {}).values())[:limit]

    def fetch(self, folder, uid, max_bytes):
        return self.messages[folder][uid]

    def append(self, folder, message, flags):
        uid = str(100 + len(self.messages[folder]))
        self.messages[folder][uid] = RawMessage(uid, flags.strip("()").split(), len(message), message)
        return uid

    def delete_draft(self, folder, uid):
        self.messages[folder].pop(uid, None)

    def send(self, message):
        pass


class DevCalendar:
    def __init__(self, password) -> None:
        self.password = password

    def check(self):
        if self.password() == "wrong":
            raise CheckError("login", "the calendar server did not accept the user name and password")
        return ["Home", "Work"]

    def search(self, start, end):
        day = time.strftime("%Y%m%d", time.localtime(time.time() + 86400))
        return [f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:dev-1\r\nSUMMARY:Dinner with Rita\r\n"
                f"DTSTART:{day}T170000Z\r\nDTEND:{day}T190000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"]

    def get(self, uid):
        return self.search(None, None)[0]

    def create(self, ical):
        pass

    def update(self, uid, ical):
        pass

    def delete(self, uid):
        pass


RECENT_SURFACE = """<main>
  <div class="toolbar"><h1>Recent documents</h1><span class="spacer"></span><button id="refresh" type="button">Refresh</button></div>
  <div id="status" class="status" role="status" aria-live="polite"></div>
  <ul id="list" class="list"></ul>
</main>
<script>
(function () {
  var V = FoyelleView, h = V.h;
  function load() {
    return V.run("files.list", { path: "documents" }, "Loading…").then(function (d) {
      if (!d) return;
      var list = V.clear(V.$("#list"));
      d.entries.filter(function (e) { return e.type === "file"; }).forEach(function (e) {
        list.appendChild(h("li", null, h("strong", { text: e.name }), " ", h("span", { class: "muted", text: V.fmtDate(e.modified) })));
      });
    });
  }
  V.$("#refresh").addEventListener("click", load);
  V.start({ name: "recent-documents", load: load });
})();
</script>
"""


class Host:
    def __init__(self, servers: dict[str, Any]) -> None:
        self.servers = servers
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    def run(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=120)

    async def surfaces(self) -> list[dict[str, Any]]:
        out = []
        for name, server in self.servers.items():
            for res in await server.list_resources():
                uri = str(res.uri)
                if uri.startswith("ui://") and uri != "ui://workshop/show":
                    out.append({"server": name, "uri": uri, "name": res.name, "title": res.title or res.name})
        return out

    async def resource(self, server: str, uri: str) -> dict[str, Any]:
        (page,) = await self.servers[server].read_resource(uri)
        title = next((r.title or r.name for r in await self.servers[server].list_resources() if str(r.uri) == uri), None)
        return {"html": page.content, "title": title, "csp": {"connectDomains": [], "resourceDomains": []}}

    async def call(self, server: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """A tool's result as the harness hands it to a card: rendered the harness's way."""
        if server == "workshop" and "." in name:  # a workshop surface calling another connector
            server, name = name.split(".", 1)
        remote = getattr(self.servers[server], "bridge_remote", None)
        if remote is not None and name in remote.names:  # a service's tool, through its bridge
            result = (await remote.call(name, arguments)).model_dump(mode="json", exclude_none=True)
            return as_harness_gives_it(result)
        try:
            content, structured = await self.servers[server].call_tool(name, arguments)
        except Exception as exc:  # noqa: BLE001 - a tool error goes back to the surface
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        texts = [{"type": "text", "text": c.text} for c in content if getattr(c, "type", "") == "text"]
        return as_harness_gives_it({"content": texts, "structuredContent": structured, "isError": False})


def as_harness_gives_it(result: dict[str, Any]) -> dict[str, Any]:
    """The harness's handling of an MCP tool result, so dev cards see what real ones see: Hermes's
    ``_render_call_tool_result`` (tools/mcp_tool_handlers.py) then Foyelle's ``tool_result_for_app``
    (tools/mcp_surfaces.py). Text blocks are joined with newlines; structured content is dropped
    when a text block repeats it as JSON (and there is text); a card gets the joined text, plus
    the structured content when it survived."""
    blocks = [b for b in result.get("content") or [] if b.get("type") == "text"]
    if result.get("isError"):
        return {"content": [{"type": "text", "text": "".join(b.get("text", "") for b in blocks) or "MCP tool returned an error"}],
                "isError": True}
    text = "\n".join(b.get("text", "") for b in blocks)
    usable = sum(1 for b in blocks if b.get("text", "").strip())
    structured = result.get("structuredContent")

    def repeats(b: dict[str, Any]) -> bool:
        try:
            return json.loads(b.get("text", "")) == structured
        except ValueError:
            return False

    if structured is not None and usable and any(repeats(b) for b in blocks):
        structured = None
    out: dict[str, Any] = {"content": []}
    if text:
        out["content"].append({"type": "text", "text": text})
        if structured is not None:
            out["structuredContent"] = structured
    elif structured is not None:  # no text: the data becomes the result
        out["structuredContent"] = structured
        out["content"].append({"type": "text", "text": json.dumps(structured, ensure_ascii=False)})
    return out


def seed_bots(state: Path) -> None:
    """A bot, two routine runs and something waiting: what a proactive Foyelle shows (once)."""
    settings = state / "settings"
    if (settings / "bots.json").exists():
        return
    settings.mkdir(parents=True, exist_ok=True)
    (settings / "bots.json").write_text(json.dumps({"bots": [
        {"id": "bot-travel", "name": "Travel", "job": "Watches flight prices for our December trip to Istanbul.",
         "picture": "✈️", "services": ["web"], "shareOnline": []},
    ]}, indent=2))

    def run(home: Path, job: str, name: str, schedule: str, prompt: str, response: str, ago: float) -> None:
        out = home / "cron" / "output" / job
        out.mkdir(parents=True, exist_ok=True)
        when = time.time() - ago
        path = out / (time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(when)) + ".md")
        path.write_text(f"# Cron Job: {name}\n\n**Job ID:** {job}\n**Schedule:** {schedule}\n\n## Prompt\n\n{prompt}\n\n"
                        f"**Response Characters:** {len(response)}\n## Response\n\n{response}\n")
        os.utime(path, (when, when))

    run(state, "brief01", "Morning brief", "every day at 07:30", "Tell me what's on today.",
        "Good morning! Today: **dentist at 10:00** (Dr. Costa), and Rita's dinner at 19:00.\n\n"
        "Two tasks are due: *pay the electricity bill* and *book the car service*.", 3 * 3600)
    run(state / "profiles" / "bot-travel", "fare01", "Fare watch", "every 6 hours",
        "Check Lisbon to Istanbul, 18 to 27 December, under 450 euros.",
        "The cheapest return is now **€412** (Pegasus, one stop), down from €468 yesterday. "
        "That's under your €450 limit.", 40 * 60)
    waiting = settings / "waiting"
    waiting.mkdir(exist_ok=True)
    now = time.time()
    item = {"id": "5a1e5a1e5a1e5a1e", "toolName": "mcp__files__write", "connector": "files", "tool": "write",
            "tier": "reversible", "args": {"path": "documents/Shopping list.txt",
                                           "content": "Milk\nBread\nApples\nCoffee\nCardamom\nCandles\n", "overwrite": True},
            "summary": "Add candles to the shopping list", "bot": "default", "sessionId": "cron_brief01_x",
            "jobId": "brief01", "created": now - 3 * 3600, "expires": now + 20 * 3600}
    (waiting / f"{item['id']}.json").write_text(json.dumps(item, indent=2))


def main() -> None:
    if "--reset" in sys.argv:
        shutil.rmtree(STATE, ignore_errors=True)
    if not (STATE / "config" / ".git").exists():
        STATE.mkdir(parents=True, exist_ok=True)
        seed(STATE)
    seed_bots(STATE)
    host = Host(build(STATE))
    for manager in BACKGROUND:  # keep the stand-in servers' session managers running
        async def keep(cm=manager) -> None:
            async with cm():
                await asyncio.Event().wait()
        asyncio.run_coroutine_threadsafe(keep(), host.loop)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def send_json(self, data: Any, code: int = 200) -> None:
            body = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/surfaces":
                    return self.send_json({"surfaces": host.run(host.surfaces())})
                if url.path == "/resource":
                    return self.send_json(host.run(host.resource(q["server"], q["uri"])))
                if url.path == "/health":
                    return self.send_json({"ok": True})
            except Exception as exc:  # noqa: BLE001
                return self.send_json({"error": str(exc)}, 500)
            self.send_json({"error": "not found"}, 404)

        def do_POST(self) -> None:
            if self.path != "/call":
                return self.send_json({"error": "not found"}, 404)
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            try:
                self.send_json(host.run(host.call(req["server"], req["name"], req.get("arguments") or {})))
            except Exception as exc:  # noqa: BLE001
                self.send_json({"content": [{"type": "text", "text": str(exc)}], "isError": True})

    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[tools] real connectors on sample data at http://127.0.0.1:{PORT} (state: {STATE})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
