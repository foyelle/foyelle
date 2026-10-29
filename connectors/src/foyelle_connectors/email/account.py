"""The mail account this connector works with.

Either it comes from the system configuration (FOYELLE_EMAIL_* in the environment, as before),
or the person connects one from the Accounts card: the card sends the provider, the address and
an app password, the account is checked by logging in to both servers, and only then saved in
the connector's own state directory (sdk.account). Until then every mail tool answers that mail
is not connected yet, instead of the connector failing to start.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from typing import Any

from .. import providers
from ..sdk.account import AccountStore
from ..sdk.google import MAIL_SCOPE, AccessToken, GoogleClient, GoogleError, SignIn
from .backend import ImapSmtpBackend, MailBackend, MailCheckError, MailConfig, MailError
from .service import EmailService

NOT_CONNECTED = "Email isn't connected yet. The user can connect it with the Connect your mail and calendar button, or by typing /accounts."
_ADDRESS_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,63}$")
SECURITY = ("ssl", "starttls")

MakeBackend = Callable[[MailConfig, Callable[[], str]], MailBackend]


def _make_backend(config: MailConfig, password: Callable[[], str]) -> MailBackend:
    return ImapSmtpBackend(config, password=password)


def config_from_settings(s: dict[str, Any]) -> MailConfig:
    return MailConfig(
        address=s["address"],
        imap_host=s["imap"]["host"],
        imap_port=int(s["imap"]["port"]),
        imap_security=s["imap"]["security"],
        smtp_host=s["smtp"]["host"],
        smtp_port=int(s["smtp"]["port"]),
        smtp_security=s["smtp"]["security"],
        username=s.get("username") or s["address"],
        smtp_username=s.get("smtp_username"),
        auth=s.get("auth", "password"),
        display_name=s.get("display_name"),
        drafts_folder=s.get("drafts") or "Drafts",
        sent_folder=s.get("sent"),
    )


class MailAccount:
    def __init__(
        self,
        store: AccountStore | None,
        *,
        make_backend: MakeBackend = _make_backend,
        fixed: EmailService | None = None,
        max_fetch: int = 20 * 1024 * 1024,
        google: GoogleClient | None = None,
        signin: SignIn | None = None,
    ) -> None:
        self.store = store
        self.google = google
        self.signin = signin or (SignIn(google) if google else None)
        self.make_backend = make_backend
        self.max_fetch = max_fetch
        self.fixed = fixed is not None
        self._lock = threading.Lock()
        self._service: EmailService | None = fixed
        self._settings: dict[str, Any] | None = None
        if fixed is None and store is not None:
            settings = store.load()
            if settings:
                try:
                    self._activate(settings)
                except (KeyError, TypeError, ValueError):
                    self._service = None  # a damaged account file: not connected

    def _activate(self, settings: dict[str, Any], first_token: tuple[str, int] | None = None) -> None:
        assert self.store is not None
        config = config_from_settings(settings)
        secret = self.store.password
        if config.auth == "xoauth2":
            if self.google is None:
                raise ValueError("a Google account without Google sign-in configured")
            secret = AccessToken(self.google, self.store.password, first=first_token)
        self._service = EmailService(self.make_backend(config, secret), config, max_fetch=self.max_fetch)
        self._settings = settings

    def require(self) -> EmailService:
        service = self._service
        if service is None:
            raise MailError(NOT_CONNECTED)
        return service

    def status(self) -> dict[str, Any]:
        service = self._service
        if service is None:
            return {"connected": False, "can_connect": self.store is not None, "google_signin": self.signin is not None}
        c = service.config
        s = self._settings or {}
        return {
            "connected": True,
            "address": c.address,
            "provider": s.get("provider", "configured"),
            "servers": [c.imap_host, c.smtp_host],
            "managed": self.fixed,  # set in the system configuration, not from the card
        }

    def connect(
        self,
        provider: str,
        address: str,
        password: str,
        display_name: str | None = None,
        imap_host: str | None = None,
        imap_port: int | None = None,
        imap_security: str | None = None,
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        smtp_security: str | None = None,
        username: str | None = None,
    ) -> dict[str, Any]:
        if self.fixed or self.store is None:
            raise MailError("This computer's mail account is set in its configuration; it can't be changed here.")
        address = address.strip()
        if not _ADDRESS_RE.match(address):
            raise MailError("That doesn't look like an email address.")
        if not password or len(password) > 1024 or any(c in password for c in "\r\n"):
            raise MailError("The password is missing or not valid.")
        p = providers.PROVIDERS.get(provider)
        if p is None:
            raise MailError(f"Unknown provider {provider!r}.")
        imap = (imap_host, imap_port or 993, imap_security or ("ssl" if (imap_port or 993) == 993 else "starttls"))
        smtp = (smtp_host, smtp_port or 465, smtp_security or ("ssl" if (smtp_port or 465) == 465 else "starttls"))
        if p.imap is not None and not imap_host:
            imap = (p.imap.host, p.imap.port, p.imap.security)
        if p.smtp is not None and not smtp_host:
            smtp = (p.smtp.host, p.smtp.port, p.smtp.security)
        for host, port, security in (imap, smtp):
            if not host or not re.match(r"^[A-Za-z0-9.-]{1,253}$", host):
                raise MailError("Both the mail (IMAP) and sending (SMTP) servers are needed.")
            if not 1 <= int(port) <= 65535 or security not in SECURITY:
                raise MailError("A server port or security setting is not valid.")
        names = [username.strip()] if username and username.strip() else providers.usernames(p, address)

        last: MailCheckError | None = None
        for name in names:
            config = MailConfig(
                address=address, imap_host=imap[0], imap_port=int(imap[1]), imap_security=imap[2],
                smtp_host=smtp[0], smtp_port=int(smtp[1]), smtp_security=smtp[2],
                username=name, smtp_username=username.strip() if username else address,
                display_name=(display_name or "").strip() or None,
            )
            try:
                folders = self.make_backend(config, lambda: password).check()
            except MailCheckError as exc:
                last = exc
                if exc.stage == "imap-login":
                    continue  # try the next way of writing the user name
                return {"connected": False, "stage": exc.stage, "message": str(exc)}
            settings = {
                "provider": p.id,
                "address": address,
                "display_name": config.display_name,
                "username": name,
                "smtp_username": config.smtp_username,
                "imap": {"host": imap[0], "port": int(imap[1]), "security": imap[2]},
                "smtp": {"host": smtp[0], "port": int(smtp[1]), "security": smtp[2]},
                "drafts": folders.get("drafts") or "Drafts",
                "sent": None if p.saves_sent else (folders.get("sent") or "Sent"),
            }
            with self._lock:
                self.store.save(settings, password)
                self._activate(settings)
            return {"connected": True, "address": address, "provider": p.id,
                    "drafts": settings["drafts"], "sent": settings["sent"], "servers": [imap[0], smtp[0]]}
        assert last is not None
        return {"connected": False, "stage": last.stage, "message": str(last)}

    # ---- Sign in with Google (mail only: its own grant, nothing else) ---------------------------

    def google_start(self, redirect_uri: str, address: str | None = None) -> dict[str, Any]:
        if self.fixed or self.store is None or self.signin is None:
            raise MailError("Signing in with Google isn't set up on this computer.")
        try:
            return self.signin.start([MAIL_SCOPE], redirect_uri, login_hint=(address or "").strip() or None)
        except GoogleError as exc:
            raise MailError(str(exc)) from None

    def google_finish(
        self, code: str | None = None, state: str | None = None, error: str | None = None,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        if self.signin is None or self.store is None or self.google is None:
            raise MailError("Signing in with Google isn't set up on this computer.")
        try:
            got = self.signin.finish(code, state, error)
        except GoogleError as exc:
            return {"connected": False, "stage": "google", "message": str(exc)}
        address = got.get("email")
        if not address or MAIL_SCOPE not in got.get("scope", "").split():
            return {"connected": False, "stage": "google",
                    "message": "Google didn't share access to mail. Sign in again and allow Foyelle to use Gmail."}
        p = providers.PROVIDERS["gmail"]
        assert p.imap and p.smtp
        config = MailConfig(
            address=address, imap_host=p.imap.host, imap_port=p.imap.port, imap_security=p.imap.security,
            smtp_host=p.smtp.host, smtp_port=p.smtp.port, smtp_security=p.smtp.security,
            username=address, smtp_username=address, auth="xoauth2", display_name=(display_name or "").strip() or None,
        )
        access = got["access_token"]
        try:
            folders = self.make_backend(config, lambda: access).check()
        except MailCheckError as exc:
            return {"connected": False, "stage": exc.stage, "message": str(exc)}
        settings = {
            "provider": "gmail", "auth": "xoauth2", "address": address, "display_name": config.display_name,
            "username": address, "smtp_username": address,
            "imap": {"host": p.imap.host, "port": p.imap.port, "security": p.imap.security},
            "smtp": {"host": p.smtp.host, "port": p.smtp.port, "security": p.smtp.security},
            "drafts": folders.get("drafts") or "Drafts", "sent": None,
        }
        with self._lock:
            self.store.save(settings, got["refresh_token"])
            # The first access token is fresh: use it rather than asking Google again at once.
            self._activate(settings, first_token=(access, int(got.get("expires_in", 3600))))
        return {"connected": True, "address": address, "provider": "gmail", "drafts": settings["drafts"], "sent": None,
                "servers": [p.imap.host, p.smtp.host]}

    def disconnect(self) -> dict[str, Any]:
        if self.fixed or self.store is None:
            raise MailError("This computer's mail account is set in its configuration; it can't be removed here.")
        with self._lock:
            removed = self.store.remove()
            self._service = None
            self._settings = None
        return {"connected": False, "removed": removed}
