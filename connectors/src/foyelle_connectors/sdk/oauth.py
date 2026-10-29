"""Sign in to a service's MCP server, the way the MCP specification describes (OAuth 2.1).

The server names its authorization server (RFC 9728 protected-resource metadata); that server
publishes its endpoints (RFC 8414). Foyelle identifies itself in one of two ways: with Foyelle's
published client document (a Client ID Metadata Document: its URL is the client id, so nothing
is registered per computer) when the server supports that and Foyelle has one configured, or
else by registering itself for this one sign-in (dynamic client registration, RFC 7591). Either
way it is a public client whose redirect is the canvas's loopback listener. The sign-in itself is PKCE with a random state, for this resource only
(RFC 8707), opened in Foyelle's sign-in browser like Google's.

The code is useless without the verifier, which never leaves this process. What is kept: the
client ID, the token endpoint and the resource (account.json), and the tokens (account.secret,
0600, in the helper's own state directory).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from .account import AccountStore

LOOPBACK_RE = re.compile(r"^http://127\.0\.0\.1:(\d{1,5})/$")
SIGN_IN_SECONDS = 600


class SignInError(RuntimeError):
    """Signing in failed; the message is safe to show."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def loopback(redirect_uri: str) -> str:
    m = LOOPBACK_RE.match(redirect_uri or "")
    if not m or not 1024 <= int(m.group(1)) <= 65535:
        raise SignInError("The sign-in must come back to this computer (http://127.0.0.1:<port>/).")
    return redirect_uri


def _https(url: Any, what: str) -> str:
    if not isinstance(url, str) or urlsplit(url).scheme != "https" or not urlsplit(url).hostname:
        raise SignInError(f"The service's {what} isn't a secure address.")
    return url


@dataclass(frozen=True)
class Endpoints:
    resource: str
    authorization: str
    token: str
    registration: str | None
    scope: str | None
    # The server accepts a Client ID Metadata Document URL as the client id.
    client_metadata: bool = False


class ServiceSignIn:
    """One sign-in at a time: start() gives the page to open, finish() takes what came back."""

    def __init__(self, resource_url: str, store: AccountStore, http: Callable[[], httpx.AsyncClient],
                 client_name: str = "Foyelle", page: str | None = None,
                 client_metadata_url: str | None = None) -> None:
        self.resource_url = resource_url
        # The sign-in page named in the configuration: a service that points elsewhere is refused
        # (the canvas only opens the configured pages too).
        self.page = page
        self.store = store
        self.http = http
        self.client_name = client_name
        # Foyelle's published client document (https), when it has one: the client id for servers
        # that support Client ID Metadata Documents.
        self.client_metadata_url = _https(client_metadata_url, "client document") if client_metadata_url else None
        self._pending: dict[str, Any] | None = None

    async def _get_json(self, http: httpx.AsyncClient, url: str) -> dict[str, Any] | None:
        try:
            r = await http.get(url, headers={"Accept": "application/json"})
        except httpx.HTTPError:
            raise SignInError("The service couldn't be reached.") from None
        if r.status_code != 200:
            return None
        try:
            data = r.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    async def discover(self, http: httpx.AsyncClient) -> Endpoints:
        u = urlsplit(self.resource_url)
        origin = f"{u.scheme}://{u.netloc}"
        path = u.path.rstrip("/")
        meta = None
        for candidate in ([f"{origin}/.well-known/oauth-protected-resource{path}"] if path else []) + [
            f"{origin}/.well-known/oauth-protected-resource"
        ]:
            meta = await self._get_json(http, candidate)
            if meta:
                break
        if not meta or not meta.get("authorization_servers"):
            raise SignInError("The service doesn't say how to sign in.")
        issuer = _https(meta["authorization_servers"][0], "sign-in server")
        iu = urlsplit(issuer)
        ipath = iu.path.rstrip("/")
        server = None
        for candidate in [
            f"{iu.scheme}://{iu.netloc}/.well-known/oauth-authorization-server{ipath}",
            f"{iu.scheme}://{iu.netloc}/.well-known/openid-configuration{ipath}",
        ]:
            server = await self._get_json(http, candidate)
            if server:
                break
        if not server:
            raise SignInError("The service's sign-in server doesn't describe itself.")
        if "S256" not in server.get("code_challenge_methods_supported", ["S256"]):
            raise SignInError("The service's sign-in doesn't support PKCE.")
        client_metadata = server.get("client_id_metadata_document_supported") is True
        if not server.get("registration_endpoint") and not (client_metadata and self.client_metadata_url):
            raise SignInError("The service doesn't let Foyelle register for signing in.")
        scopes = meta.get("scopes_supported")
        authorization = _https(server.get("authorization_endpoint"), "sign-in page")
        a = urlsplit(authorization)
        if self.page is not None and f"{a.scheme}://{a.netloc}{a.path}" != self.page:
            raise SignInError("The service's sign-in page isn't the one Foyelle knows. Nothing was opened.")
        return Endpoints(
            resource=meta.get("resource") or self.resource_url,
            authorization=authorization,
            token=_https(server.get("token_endpoint"), "token address"),
            registration=_https(server["registration_endpoint"], "registration address")
            if server.get("registration_endpoint") else None,
            scope=" ".join(scopes) if isinstance(scopes, list) and scopes else None,
            client_metadata=client_metadata,
        )

    async def start(self, redirect_uri: str) -> dict[str, Any]:
        loopback(redirect_uri)
        async with self.http() as http:
            ep = await self.discover(http)
            if ep.client_metadata and self.client_metadata_url:
                # Foyelle's published document is the client id: no registration.
                client: dict[str, Any] = {"client_id": self.client_metadata_url}
            else:
                client = await self._register(http, ep, redirect_uri)
        verifier = _b64(secrets.token_bytes(48))
        state = _b64(secrets.token_bytes(32))
        params = {
            "response_type": "code",
            "client_id": client["client_id"],
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": _b64(hashlib.sha256(verifier.encode("ascii")).digest()),
            "code_challenge_method": "S256",
            "resource": ep.resource,
        }
        if ep.scope:
            params["scope"] = ep.scope
        self._pending = {"state": state, "verifier": verifier, "redirect_uri": redirect_uri, "endpoints": ep,
                         "client_id": client["client_id"], "until": time.time() + SIGN_IN_SECONDS}
        sep = "&" if urlsplit(ep.authorization).query else "?"
        return {"auth_url": f"{ep.authorization}{sep}{urlencode(params)}", "expires_in": SIGN_IN_SECONDS}

    async def _register(self, http: httpx.AsyncClient, ep: Endpoints, redirect_uri: str) -> dict[str, Any]:
        """Dynamic client registration (RFC 7591), for this one sign-in."""
        if not ep.registration:
            raise SignInError("The service doesn't let Foyelle register for signing in.")
        body = {
            "client_name": self.client_name,
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
        if ep.scope:
            body["scope"] = ep.scope
        try:
            r = await http.post(ep.registration, json=body)
        except httpx.HTTPError:
            raise SignInError("The service couldn't be reached.") from None
        client = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code not in (200, 201) or not client.get("client_id"):
            raise SignInError(f"The service refused to register Foyelle ({client.get('error', r.status_code)}).")
        return client

    async def finish(self, code: str | None, state: str | None, error: str | None = None) -> dict[str, Any]:
        pending, self._pending = self._pending, None
        if not pending or pending["until"] < time.time():
            raise SignInError("The sign-in has expired. Start again.")
        if error:
            if error == "access_denied":
                raise SignInError("You didn't allow Foyelle, so nothing was connected.")
            raise SignInError(f"The sign-in didn't finish ({error}). Start again.")
        if not code or not state or not secrets.compare_digest(state, pending["state"]):
            raise SignInError("This sign-in doesn't match the one Foyelle started. Start again.")
        ep: Endpoints = pending["endpoints"]
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pending["redirect_uri"],
            "client_id": pending["client_id"],
            "code_verifier": pending["verifier"],
            "resource": ep.resource,
        }
        tokens = await self._token(ep.token, form)
        self.store.save(
            {"client_id": pending["client_id"], "token_endpoint": ep.token, "resource": ep.resource},
            json.dumps(tokens),
        )
        return {"connected": True}

    async def _token(self, token_url: str, form: dict[str, str]) -> dict[str, Any]:
        try:
            async with self.http() as http:
                r = await http.post(token_url, data=form, headers={"Accept": "application/json"})
        except httpx.HTTPError:
            raise SignInError("The service couldn't be reached.") from None
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code != 200 or not data.get("access_token"):
            if data.get("error") == "invalid_grant":
                raise SignInError("The service no longer accepts Foyelle's access. Sign in again from Accounts.")
            raise SignInError(f"The service didn't give Foyelle access ({data.get('error', r.status_code)}).")
        expires = data.get("expires_in")
        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token"),
            "expires_at": time.time() + int(expires) if isinstance(expires, (int, float, str)) and str(expires).isdigit() else None,
        }

    def connected(self) -> bool:
        return self.store.load() is not None

    async def access_token(self, *, force_refresh: bool = False) -> str | None:
        """A usable access token, renewed when it is about to expire; None when not signed in."""
        settings = self.store.load()
        if not settings:
            return None
        try:
            tokens = json.loads(self.store.password())
        except (LookupError, ValueError):
            return None
        expires_at = tokens.get("expires_at")
        fresh = expires_at is None or time.time() < expires_at - 60
        if fresh and not force_refresh:
            return tokens["access_token"]
        if not tokens.get("refresh_token"):
            return None if force_refresh else tokens["access_token"]
        renewed = await self._token(settings["token_endpoint"], {
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": settings["client_id"],
            "resource": settings["resource"],
        })
        renewed["refresh_token"] = renewed.get("refresh_token") or tokens["refresh_token"]
        self.store.save(settings, json.dumps(renewed))
        return renewed["access_token"]

    def disconnect(self) -> bool:
        return self.store.remove()
