"""Sign in with Google, the way Google expects a desktop app to (OAuth 2.0 for installed apps).

The connector builds Google's sign-in URL with PKCE and a random `state`. The canvas opens it in
Foyelle's sign-in browser (Firefox, full screen over the canvas) and listens on a loopback port for
Google's redirect (`http://127.0.0.1:<port>/`), which carries a one-time code. The canvas hands the
code back here, and the connector exchanges it with Google, directly, for tokens.

The code is useless without the PKCE verifier, which never leaves this process, and the `state`
ties it to the sign-in this connector started. Google treats desktop clients as public: their
"secret" is not confidential, so shipping it with Foyelle is expected.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
MAIL_SCOPE = "https://mail.google.com/"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
LOOPBACK_RE = re.compile(r"^http://127\.0\.0\.1:(\d{1,5})/$")
SIGN_IN_SECONDS = 600


class GoogleError(RuntimeError):
    """Signing in with Google failed; the message is safe to show."""


@dataclass(frozen=True)
class GoogleClient:
    client_id: str
    client_secret: str | None = None  # a desktop client's "secret" (public; Google may still ask for it)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def email_from_id_token(id_token: str) -> str | None:
    """The address in an ID token that came straight from Google's token endpoint (over TLS, so
    the signature need not be checked here)."""
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return None
    if claims.get("email_verified") is False:
        return None
    email = claims.get("email")
    return email if isinstance(email, str) and "@" in email else None


def loopback(redirect_uri: str) -> str:
    """Only a loopback redirect on this computer, as Google's desktop flow uses."""
    m = LOOPBACK_RE.match(redirect_uri or "")
    if not m or not 1024 <= int(m.group(1)) <= 65535:
        raise GoogleError("The sign-in must come back to this computer (http://127.0.0.1:<port>/).")
    return redirect_uri


class SignIn:
    """One sign-in at a time per connector: start() gives the URL to open, finish() takes the code."""

    def __init__(self, client: GoogleClient, http: Callable[[], httpx.Client] | None = None) -> None:
        self.client = client
        self.http = http or (lambda: httpx.Client(timeout=20))
        self._pending: dict[str, Any] | None = None
        self._lock = threading.Lock()

    def start(self, scopes: list[str], redirect_uri: str, login_hint: str | None = None) -> dict[str, Any]:
        verifier = _b64(secrets.token_bytes(48))
        state = _b64(secrets.token_bytes(32))
        params = {
            "client_id": self.client.client_id,
            "redirect_uri": loopback(redirect_uri),
            "response_type": "code",
            "scope": " ".join(["openid", "email", *scopes]),
            "state": state,
            "code_challenge": _b64(hashlib.sha256(verifier.encode("ascii")).digest()),
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",  # so Google returns a refresh token every time
        }
        if login_hint:
            params["login_hint"] = login_hint
        with self._lock:
            self._pending = {"state": state, "verifier": verifier, "redirect_uri": redirect_uri,
                             "until": time.time() + SIGN_IN_SECONDS}
        return {"auth_url": f"{AUTH_URL}?{urlencode(params)}", "expires_in": SIGN_IN_SECONDS}

    def finish(self, code: str | None, state: str | None, error: str | None = None) -> dict[str, Any]:
        """The tokens for the code Google sent back to the loopback address."""
        with self._lock:
            pending, self._pending = self._pending, None
        if not pending or pending["until"] < time.time():
            raise GoogleError("The sign-in has expired. Start again.")
        if error:
            if error == "access_denied":
                raise GoogleError("You didn't allow Foyelle, so nothing was connected.")
            raise GoogleError(f"The sign-in didn't finish ({error}). Start again.")
        if not code or not state or not secrets.compare_digest(state, pending["state"]):
            raise GoogleError("This sign-in doesn't match the one Foyelle started. Start again.")
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": pending["verifier"],
            "redirect_uri": pending["redirect_uri"],
            "client_id": self.client.client_id,
        }
        if self.client.client_secret:
            form["client_secret"] = self.client.client_secret
        try:
            with self.http() as http:
                t = http.post(TOKEN_URL, data=form)
        except httpx.HTTPError as exc:
            raise GoogleError(f"Google couldn't be reached ({type(exc).__name__}).") from None
        tokens = t.json() if t.headers.get("content-type", "").startswith("application/json") else {}
        if t.status_code != 200 or "refresh_token" not in tokens:
            raise GoogleError(f"Google didn't give Foyelle access ({tokens.get('error', t.status_code)}).")
        return {
            "refresh_token": tokens["refresh_token"],
            "access_token": tokens.get("access_token"),
            "expires_in": int(tokens.get("expires_in", 3600)),
            "email": email_from_id_token(tokens.get("id_token", "")),
            "scope": tokens.get("scope", ""),
        }


class AccessToken:
    """A fresh access token on demand, from a stored refresh token (refreshed a minute early)."""

    def __init__(
        self,
        client: GoogleClient,
        refresh_token: Callable[[], str],
        http: Callable[[], httpx.Client] | None = None,
        first: tuple[str, int] | None = None,
    ) -> None:
        self.client = client
        self.refresh_token = refresh_token
        self.http = http or (lambda: httpx.Client(timeout=20))
        self._lock = threading.Lock()
        self._token: str | None = None
        self._until = 0.0
        if first and first[0]:
            self._token, self._until = first[0], time.time() + first[1]

    def __call__(self) -> str:
        with self._lock:
            if self._token and time.time() < self._until - 60:
                return self._token
            form = {"grant_type": "refresh_token", "refresh_token": self.refresh_token(),
                    "client_id": self.client.client_id}
            if self.client.client_secret:
                form["client_secret"] = self.client.client_secret
            try:
                with self.http() as http:
                    r = http.post(TOKEN_URL, data=form)
            except httpx.HTTPError as exc:
                raise GoogleError(f"Google couldn't be reached ({type(exc).__name__}).") from None
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code != 200 or "access_token" not in data:
                if data.get("error") == "invalid_grant":
                    raise GoogleError("Google no longer accepts Foyelle's access. Sign in with Google again from Accounts.")
                raise GoogleError(f"Google refused to renew access ({data.get('error', r.status_code)}).")
            self._token = data["access_token"]
            self._until = time.time() + int(data.get("expires_in", 3600))
            return self._token


def xoauth2(user: str, token: str) -> str:
    """The SASL XOAUTH2 string IMAP and SMTP take for Google accounts."""
    return f"user={user}\x01auth=Bearer {token}\x01\x01"


def client_from_env(env: Callable[[str], str | None]) -> GoogleClient | None:
    cid = env("FOYELLE_GOOGLE_CLIENT_ID")
    if not cid:
        return None
    return GoogleClient(cid, env("FOYELLE_GOOGLE_CLIENT_SECRET") or None)
