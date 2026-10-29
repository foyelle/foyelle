from __future__ import annotations

import json
import socket
import stat
import threading
from pathlib import Path
from typing import ClassVar

import pytest
from conftest import mcp_session, serving

from foyelle_connectors import providers
from foyelle_connectors.email.account import NOT_CONNECTED, MailAccount
from foyelle_connectors.email.backend import MailCheckError, MailConfig, special_use
from foyelle_connectors.email.server import build_server
from foyelle_connectors.sdk import net
from foyelle_connectors.sdk.account import AccountStore


class CheckBackend:
    """A backend whose check() accepts one user name and one password."""

    seen: ClassVar[list[tuple[str, str, str]]] = []

    def __init__(self, config: MailConfig, password, *, accept_user: str = "a@icloud.com", fail: str | None = None):
        self.config, self.password, self.accept_user, self.fail = config, password, accept_user, fail

    def check(self):
        CheckBackend.seen.append((self.config.username, self.config.smtp_username or "", self.config.imap_host))
        if self.fail:
            raise MailCheckError(self.fail, "nope")
        if self.config.username != self.accept_user or self.password() != "app-pw":
            raise MailCheckError("imap-login", "the mail server did not accept the user name and password")
        return {"drafts": "Drafts", "sent": "Sent Messages"}

    # The service never runs here; these only satisfy the protocol.
    def search(self, *a, **k):
        return []


def account(tmp_path: Path, **kw) -> MailAccount:
    return MailAccount(AccountStore(tmp_path / "account"), make_backend=lambda c, p: CheckBackend(c, p, **kw))


def test_store_keeps_settings_and_password_private(tmp_path: Path) -> None:
    store = AccountStore(tmp_path / "a")
    assert store.load() is None
    store.save({"address": "x@example.org"}, "s3cret")
    for name in ("account.json", "account.secret"):
        assert stat.S_IMODE((tmp_path / "a" / name).stat().st_mode) == 0o600
    assert store.load() == {"address": "x@example.org"} and store.password() == "s3cret"
    assert "s3cret" not in (tmp_path / "a" / "account.json").read_text()
    assert store.remove() and store.load() is None
    with pytest.raises(LookupError):
        store.password()


def test_connect_tries_user_names_and_keeps_the_one_that_works(tmp_path: Path) -> None:
    CheckBackend.seen = []
    acc = account(tmp_path, accept_user="a")  # iCloud mail may want the name before the @
    out = acc.connect("icloud", "a@icloud.com", "app-pw")
    assert out["connected"] and out["sent"] == "Sent Messages" and out["servers"] == ["imap.mail.me.com", "smtp.mail.me.com"]
    assert [s[0] for s in CheckBackend.seen] == ["a@icloud.com", "a"]
    assert CheckBackend.seen[-1][1] == "a@icloud.com"  # sending uses the full address
    saved = json.loads((tmp_path / "account" / "account.json").read_text())
    assert saved["username"] == "a" and saved["smtp"] == {"host": "smtp.mail.me.com", "port": 587, "security": "starttls"}
    assert "app-pw" not in json.dumps(saved) and "app-pw" not in json.dumps(acc.status())
    # A fresh process finds the account again.
    again = account(tmp_path)
    assert again.status()["connected"] and again.status()["address"] == "a@icloud.com"
    assert again.require().config.username == "a"


def test_gmail_keeps_no_extra_sent_copy(tmp_path: Path) -> None:
    acc = account(tmp_path, accept_user="me@gmail.com")
    assert acc.connect("gmail", "me@gmail.com", "app-pw")["sent"] is None


def test_failures_are_reported_by_step_and_nothing_is_kept(tmp_path: Path) -> None:
    acc = account(tmp_path)
    out = acc.connect("icloud", "a@icloud.com", "wrong")
    assert out == {"connected": False, "stage": "imap-login", "message": "the mail server did not accept the user name and password"}
    out = account(tmp_path, fail="smtp-login").connect("fastmail", "a@fastmail.com", "app-pw")
    assert out["stage"] == "smtp-login"
    assert AccountStore(tmp_path / "account").load() is None
    with pytest.raises(Exception, match="email address"):
        acc.connect("gmail", "not an address", "x")
    with pytest.raises(Exception, match="servers are needed"):
        acc.connect("other", "a@example.org", "app-pw")
    with pytest.raises(Exception, match="servers are needed"):
        acc.connect("other", "a@example.org", "app-pw", imap_host="imap.example.org", smtp_host="bad host!")


def test_other_provider_uses_the_given_servers(tmp_path: Path) -> None:
    CheckBackend.seen = []
    acc = account(tmp_path, accept_user="a@example.org")
    out = acc.connect("other", "a@example.org", "app-pw", imap_host="mail.example.org", smtp_host="mail.example.org", smtp_port=587)
    assert out["connected"]
    saved = AccountStore(tmp_path / "account").load()
    assert saved["smtp"] == {"host": "mail.example.org", "port": 587, "security": "starttls"}


def test_disconnect_and_the_not_connected_answer(tmp_path: Path) -> None:
    acc = account(tmp_path, accept_user="me@gmail.com")
    acc.connect("gmail", "me@gmail.com", "app-pw")
    assert acc.disconnect() == {"connected": False, "removed": True}
    with pytest.raises(Exception, match="isn't connected"):
        acc.require()
    assert not (tmp_path / "account" / "account.secret").exists()


async def test_mcp_tools_when_not_connected(tmp_path: Path) -> None:
    with serving(build_server(account(tmp_path))) as url:
        async with mcp_session(url) as session:
            result = await session.call_tool("search", {"query": "x"})
            assert result.isError and NOT_CONNECTED in result.content[0].text
            status = await session.call_tool("account_status", {})
            assert status.structuredContent == {"connected": False, "can_connect": True, "google_signin": False}
            listed = await session.call_tool("account_providers", {})
            ids = [p["id"] for p in listed.structuredContent["providers"]]
            assert ids == ["gmail", "icloud", "fastmail", "other"]


def test_provider_presets_are_complete() -> None:
    for p in providers.PROVIDERS.values():
        assert p.app_password_steps
        if p.id != "other":
            assert p.imap and p.smtp and p.app_password_url and p.app_password_url.startswith("https://")
    # Google's CalDAV takes only OAuth: Gmail connects mail, and says why not the calendar.
    gmail = providers.describe(providers.PROVIDERS["gmail"])
    assert gmail["mail"] and not gmail["calendar"] and "sign-in" in gmail["note"]
    assert providers.PROVIDERS["fastmail"].caldav == "https://caldav.fastmail.com/dav/"
    assert providers.usernames(providers.PROVIDERS["icloud"], "ann@icloud.com") == ["ann@icloud.com", "ann"]


def test_special_use_folders() -> None:
    lines = [b'(\\HasNoChildren \\Drafts) "/" "[Gmail]/Drafts"', b'(\\HasNoChildren) "/" INBOX',
             b'(\\Sent \\HasNoChildren) "." "Sent Items"', b"junk", None]
    assert special_use(lines) == {"drafts": "[Gmail]/Drafts", "sent": "Sent Items"}


# ---- the egress tunnel ----------------------------------------------------------------------


def _serve_once(handler) -> int:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def run() -> None:
        conn, _ = srv.accept()
        with conn:
            handler(conn)
        srv.close()

    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1]


def test_tunnel_through_a_connect_proxy(monkeypatch) -> None:
    requests: list[bytes] = []

    def proxy(conn: socket.socket) -> None:
        data = b""
        while b"\r\n\r\n" not in data:
            data += conn.recv(1024)
        requests.append(data)
        # The proxy's reply and the server's greeting arrive in one packet.
        conn.sendall(b"HTTP/1.0 200 Connection established\r\n\r\n* OK IMAP ready\r\n")
        conn.sendall(conn.recv(1024).upper())

    port = _serve_once(proxy)
    monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("NO_PROXY", "localhost")
    sock = net.connect("imap.example.org", 993, timeout=5)
    with sock:
        reader = sock.makefile("rb")
        assert reader.readline() == b"* OK IMAP ready\r\n"
        sock.sendall(b"a1 noop\r\n")
        assert reader.readline() == b"A1 NOOP\r\n"
    assert requests[0].startswith(b"CONNECT imap.example.org:993 HTTP/1.1\r\n")


def test_tunnel_refusal_and_bad_hosts(monkeypatch) -> None:
    port = _serve_once(lambda c: (c.recv(1024), c.sendall(b"HTTP/1.0 403 Filtered\r\n\r\n")))
    monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{port}")
    with pytest.raises(net.TunnelError, match="403"):
        net.connect("imap.example.org", 993, timeout=5)
    with pytest.raises(net.TunnelError):
        net.connect("evil\r\nHost: x", 993, timeout=5)
    monkeypatch.setenv("NO_PROXY", "example.org")
    assert net._bypass("imap.example.org") and not net._bypass("example.com")


# ---- the calendar account ---------------------------------------------------------------------

from datetime import UTC  # noqa: E402

from foyelle_connectors.calendar.account import CalendarAccount, CheckError  # noqa: E402
from foyelle_connectors.calendar.account import NOT_CONNECTED as CAL_NOT_CONNECTED  # noqa: E402
from foyelle_connectors.calendar.server import build_server as build_calendar  # noqa: E402


class FakeDav:
    def __init__(self, url, username, password, calendar, *, accept=("a@icloud.com",), down=False):
        self.url, self.username, self.password, self.calendar = url, username, password, calendar
        self.accept, self.down = accept, down

    def check(self):
        if self.down:
            raise CheckError("connect", "cannot reach the calendar server (ConnectError)")
        if self.username not in self.accept or self.password() != "app-pw":
            raise CheckError("login", "the calendar server did not accept the user name and password")
        return ["Home", "Work"]

    def search(self, start, end):
        return []


def cal_account(tmp_path: Path, **kw) -> CalendarAccount:
    return CalendarAccount(AccountStore(tmp_path / "cal"), UTC, make_backend=lambda u, n, p, c: FakeDav(u, n, p, c, **kw))


def test_calendar_connect_picks_a_calendar_and_persists(tmp_path: Path) -> None:
    acc = cal_account(tmp_path, accept=("a",))
    out = acc.connect("icloud", "a@icloud.com", "app-pw", calendar="Work")
    assert out == {"connected": True, "address": "a@icloud.com", "provider": "icloud", "calendar": "Work",
                   "calendars": ["Home", "Work"]}
    again = cal_account(tmp_path)
    status = again.status()
    assert status["connected"] and status["calendar"] == "Work" and "app-pw" not in json.dumps(status)
    assert again.require().settings.user_address == "a@icloud.com"
    assert again.require().backend.url == "https://caldav.icloud.com/"


def test_calendar_gmail_url_and_failures(tmp_path: Path) -> None:
    seen = []

    def make(u, n, p, c):
        seen.append(u)
        return FakeDav(u, n, p, c, accept=("me@gmail.com",))

    acc = CalendarAccount(AccountStore(tmp_path / "cal"), UTC, make_backend=make)
    with pytest.raises(Exception, match="Google's own sign-in"):
        acc.connect("gmail", "me@gmail.com", "app-pw")
    assert seen == []
    assert acc.connect("fastmail", "me@gmail.com", "app-pw")["calendar"] == "Home"
    assert seen[0] == "https://caldav.fastmail.com/dav/"
    out = cal_account(tmp_path / "x", down=True).connect("fastmail", "a@fastmail.com", "app-pw")
    assert out["stage"] == "connect"
    out = cal_account(tmp_path / "y").connect("fastmail", "a@fastmail.com", "wrong")
    assert out["stage"] == "login"
    with pytest.raises(Exception, match="https://"):
        cal_account(tmp_path / "z").connect("other", "a@example.org", "app-pw")
    with pytest.raises(Exception, match="https://"):
        cal_account(tmp_path / "z").connect("other", "a@example.org", "app-pw", url="http://plain.example.org/")


async def test_calendar_tools_when_not_connected(tmp_path: Path) -> None:
    with serving(build_calendar(cal_account(tmp_path))) as url:
        async with mcp_session(url) as session:
            result = await session.call_tool("list_events", {})
            assert result.isError and CAL_NOT_CONNECTED in result.content[0].text
            status = await session.call_tool("account_status", {})
            assert status.structuredContent == {"connected": False, "can_connect": True, "google_signin": False}
