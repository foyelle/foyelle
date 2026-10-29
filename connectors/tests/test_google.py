from __future__ import annotations

import base64
import json
from datetime import UTC
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from foyelle_connectors.calendar.account import CalendarAccount
from foyelle_connectors.email.account import MailAccount
from foyelle_connectors.email.backend import ImapSmtpBackend, MailConfig
from foyelle_connectors.sdk import google
from foyelle_connectors.sdk.account import AccountStore

CLIENT = google.GoogleClient("cid.apps.googleusercontent.com", "public-desktop-secret")
REDIRECT = "http://127.0.0.1:53111/"


def id_token(email: str) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"email": email, "email_verified": True}).encode()).rstrip(b"=").decode()
    return f"h.{body}.s"


class FakeGoogle:
    """Google's token endpoint, as an httpx transport."""

    def __init__(self, scope: str) -> None:
        self.scope = scope
        self.token_requests: list[dict[str, list[str]]] = []
        self.refresh_ok = True

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == google.TOKEN_URL:
            form = parse_qs(request.content.decode())
            self.token_requests.append(form)
            if form["grant_type"] == ["authorization_code"]:
                return httpx.Response(200, json={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3599,
                                                 "id_token": id_token("me@gmail.com"), "scope": f"openid email {self.scope}"})
            if self.refresh_ok:
                return httpx.Response(200, json={"access_token": f"at-{len(self.token_requests)}", "expires_in": 3599})
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(404)

    def http(self):
        return lambda: httpx.Client(transport=httpx.MockTransport(self.handler))


def started(out: dict) -> dict[str, str]:
    assert out["auth_url"].startswith(google.AUTH_URL + "?")
    return {k: v[0] for k, v in parse_qs(urlparse(out["auth_url"]).query).items()}


def test_desktop_sign_in_with_pkce_and_a_loopback_redirect() -> None:
    g = FakeGoogle(google.MAIL_SCOPE)
    flow = google.SignIn(CLIENT, http=g.http())
    s = started(flow.start([google.MAIL_SCOPE], REDIRECT, login_hint="me@gmail.com"))
    assert s["redirect_uri"] == REDIRECT and s["code_challenge_method"] == "S256"
    assert s["access_type"] == "offline" and s["login_hint"] == "me@gmail.com" and s["client_id"] == CLIENT.client_id
    assert s["scope"].split() == ["openid", "email", google.MAIL_SCOPE]
    got = flow.finish("4/code", s["state"])
    assert got["refresh_token"] == "rt-1" and got["email"] == "me@gmail.com"
    # The verifier went only to Google, and it matches the challenge in the URL the browser opened.
    form = g.token_requests[0]
    verifier = form["code_verifier"][0]
    import hashlib

    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == s["code_challenge"] and verifier not in json.dumps(s)
    assert form["redirect_uri"] == [REDIRECT] and form["client_secret"] == ["public-desktop-secret"]
    with pytest.raises(google.GoogleError, match="expired"):
        flow.finish("4/code", s["state"])  # used once


def test_loopback_only_and_state_must_match() -> None:
    flow = google.SignIn(CLIENT, http=FakeGoogle(google.MAIL_SCOPE).http())
    for bad in ("https://relay.example/callback", "http://localhost:5000/", "http://127.0.0.1:80/", "http://127.0.0.1:5000/x"):
        with pytest.raises(google.GoogleError, match="come back to this computer"):
            flow.start([google.MAIL_SCOPE], bad)
    flow.start([google.MAIL_SCOPE], REDIRECT)
    with pytest.raises(google.GoogleError, match="doesn't match"):
        flow.finish("4/code", "someone-elses-state")


def test_denied_and_access_token_renewal() -> None:
    g = FakeGoogle(google.MAIL_SCOPE)
    flow = google.SignIn(CLIENT, http=g.http())
    flow.start([google.MAIL_SCOPE], REDIRECT)
    with pytest.raises(google.GoogleError, match="didn't allow"):
        flow.finish(None, None, "access_denied")
    token = google.AccessToken(CLIENT, lambda: "rt-1", http=g.http())
    first = token()
    assert first.startswith("at-") and token() == first  # cached
    assert g.token_requests[-1]["grant_type"] == ["refresh_token"]
    g.refresh_ok = False
    stale = google.AccessToken(CLIENT, lambda: "rt-1", http=g.http())
    with pytest.raises(google.GoogleError, match="Sign in with Google again"):
        stale()


def test_xoauth2_login_for_imap_and_smtp() -> None:
    seen = {}

    class IMAP:
        def authenticate(self, mech, fn):
            seen["imap"] = (mech, fn(b""))
            return "OK", [b""]

        def list(self):
            return "OK", [b'(\\Drafts) "/" "[Gmail]/Drafts"']

        def logout(self):
            pass

    class SMTP:
        def ehlo_or_helo_if_needed(self):
            pass

        def auth(self, mech, fn, initial_response_ok=True):
            seen["smtp"] = (mech, fn())

        def quit(self):
            pass

    config = MailConfig(address="me@gmail.com", imap_host="imap.gmail.com", smtp_host="smtp.gmail.com",
                        username="me@gmail.com", auth="xoauth2")
    backend = ImapSmtpBackend(config, password=lambda: "at-9", imap_factory=IMAP, smtp_factory=SMTP)
    assert backend.check()["drafts"] == "[Gmail]/Drafts"
    assert seen["imap"] == ("XOAUTH2", b"user=me@gmail.com\x01auth=Bearer at-9\x01\x01")
    assert seen["smtp"] == ("XOAUTH2", "user=me@gmail.com\x01auth=Bearer at-9\x01\x01")


class Checks:
    def __init__(self, config, password):
        self.config, self.password = config, password

    def check(self):
        assert self.config.auth == "xoauth2" and self.password() == "at-1"
        return {"drafts": "[Gmail]/Drafts", "sent": "[Gmail]/Sent Mail"}


def test_mail_account_with_google(tmp_path: Path) -> None:
    g = FakeGoogle(google.MAIL_SCOPE)
    acc = MailAccount(AccountStore(tmp_path / "m"), make_backend=Checks, google=CLIENT,
                      signin=google.SignIn(CLIENT, http=g.http()))
    assert acc.status()["google_signin"] is True
    s = started(acc.google_start(REDIRECT, "me@gmail.com"))
    out = acc.google_finish("4/code", s["state"], display_name="Me")
    assert out["connected"] and out["address"] == "me@gmail.com" and out["sent"] is None
    saved = json.loads((tmp_path / "m" / "account.json").read_text())
    assert saved["auth"] == "xoauth2" and "rt-1" not in json.dumps(saved) and "at-1" not in json.dumps(saved)
    assert (tmp_path / "m" / "account.secret").read_text() == "rt-1"  # only the refresh token is kept
    assert acc.require().config.auth == "xoauth2"


def test_mail_account_refuses_a_grant_without_mail(tmp_path: Path) -> None:
    g = FakeGoogle("https://www.googleapis.com/auth/calendar")  # the person unticked Gmail
    acc = MailAccount(AccountStore(tmp_path / "m"), make_backend=Checks, google=CLIENT,
                      signin=google.SignIn(CLIENT, http=g.http()))
    s = started(acc.google_start(REDIRECT))
    out = acc.google_finish("4/code", s["state"])
    assert out["connected"] is False and "allow Foyelle to use Gmail" in out["message"]
    assert AccountStore(tmp_path / "m").load() is None


def test_calendar_account_with_google(tmp_path: Path) -> None:
    g = FakeGoogle(google.CALENDAR_SCOPE)
    made = []

    class Dav:
        def __init__(self, url, user, password, calendar, bearer=False):
            made.append((url, user, bearer))
            self.password = password

        def check(self):
            assert self.password() == "at-1"
            return ["me@gmail.com", "Family"]

        def search(self, start, end):
            return []

    acc = CalendarAccount(AccountStore(tmp_path / "c"), UTC, make_backend=Dav, google=CLIENT,
                          signin=google.SignIn(CLIENT, http=g.http()))
    s = started(acc.google_start(REDIRECT))
    out = acc.google_finish("4/code", s["state"])
    assert out["connected"] and out["calendars"] == ["me@gmail.com", "Family"]
    assert made[0] == ("https://apidata.googleusercontent.com/caldav/v2/me@gmail.com/user", "me@gmail.com", True)
    assert made[-1][2] is True  # the kept account uses the bearer token too


def test_not_configured_and_client_from_env() -> None:
    assert google.client_from_env({}.get) is None
    env = {"FOYELLE_GOOGLE_CLIENT_ID": "c", "FOYELLE_GOOGLE_CLIENT_SECRET": "s"}
    assert google.client_from_env(env.get) == google.GoogleClient("c", "s")
    assert google.client_from_env({"FOYELLE_GOOGLE_CLIENT_ID": "c"}.get) == google.GoogleClient("c", None)
    with pytest.raises(Exception, match="isn't set up"):
        MailAccount(AccountStore(Path("/nonexistent"))).google_start(REDIRECT)
