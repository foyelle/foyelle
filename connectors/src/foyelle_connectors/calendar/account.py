"""The calendar account this connector works with.

Like mail (email/account.py): either set in the system configuration (FOYELLE_CALDAV_* in the
environment) or connected from the Accounts card, checked by listing the account's calendars,
and kept in the connector's own state directory.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from datetime import tzinfo
from typing import Any, Protocol

from .. import providers
from ..sdk.account import AccountStore
from ..sdk.google import CALENDAR_SCOPE, AccessToken, GoogleClient, GoogleError, SignIn

GOOGLE_CALDAV = "https://apidata.googleusercontent.com/caldav/v2/{address}/user"
from .service import CalendarBackend, CalendarError, CalendarService, CalendarSettings

NOT_CONNECTED = "The calendar isn't connected yet. The user can connect it with the Connect your mail and calendar button, or by typing /accounts."
_ADDRESS_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,63}$")


class CheckError(CalendarError):
    """Connecting failed at one step: "connect" (server not reached) or "login" (not accepted)."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


class CheckableBackend(CalendarBackend, Protocol):
    def check(self) -> list[str]: ...


MakeBackend = Callable[..., CheckableBackend]


def _make_backend(
    url: str, username: str, password: Callable[[], str], calendar: str | None, bearer: bool = False
) -> CheckableBackend:
    from .backend import CaldavBackend

    return CaldavBackend(url=url, username=username, password=password, calendar_name=calendar, bearer=bearer)


class CalendarAccount:
    def __init__(
        self,
        store: AccountStore | None,
        timezone: tzinfo,
        *,
        make_backend: MakeBackend = _make_backend,
        fixed: CalendarService | None = None,
        google: GoogleClient | None = None,
        signin: SignIn | None = None,
    ) -> None:
        self.store = store
        self.google = google
        self.signin = signin or (SignIn(google) if google else None)
        self.timezone = timezone
        self.make_backend = make_backend
        self.fixed = fixed is not None
        self._lock = threading.Lock()
        self._service: CalendarService | None = fixed
        self._settings: dict[str, Any] | None = None
        if fixed is None and store is not None:
            settings = store.load()
            if settings:
                try:
                    self._activate(settings)
                except (KeyError, TypeError, ValueError):
                    self._service = None

    def _activate(self, s: dict[str, Any], first_token: tuple[str, int] | None = None) -> None:
        assert self.store is not None
        if s.get("auth") == "bearer":
            if self.google is None:
                raise ValueError("a Google account without Google sign-in configured")
            token = AccessToken(self.google, self.store.password, first=first_token)
            backend = self.make_backend(s["url"], s["username"], token, s.get("calendar"), bearer=True)
        else:
            backend = self.make_backend(s["url"], s["username"], self.store.password, s.get("calendar"))
        settings = CalendarSettings(timezone=self.timezone, user_address=s.get("address"))
        self._service = CalendarService(backend, settings)
        self._settings = s

    def require(self) -> CalendarService:
        service = self._service
        if service is None:
            raise CalendarError(NOT_CONNECTED)
        return service

    def status(self) -> dict[str, Any]:
        if self._service is None:
            return {"connected": False, "can_connect": self.store is not None, "google_signin": self.signin is not None}
        s = self._settings or {}
        return {
            "connected": True,
            "address": s.get("address") or self._service.settings.user_address,
            "provider": s.get("provider", "configured"),
            "calendar": s.get("calendar"),
            "calendars": s.get("calendars", []),
            "managed": self.fixed,
        }

    def connect(
        self,
        provider: str,
        address: str,
        password: str,
        url: str | None = None,
        username: str | None = None,
        calendar: str | None = None,
    ) -> dict[str, Any]:
        if self.fixed or self.store is None:
            raise CalendarError("This computer's calendar is set in its configuration; it can't be changed here.")
        address = address.strip()
        if not _ADDRESS_RE.match(address):
            raise CalendarError("That doesn't look like an email address.")
        if not password or len(password) > 1024 or any(c in password for c in "\r\n"):
            raise CalendarError("The password is missing or not valid.")
        p = providers.PROVIDERS.get(provider)
        if p is None:
            raise CalendarError(f"Unknown provider {provider!r}.")
        if not (url or "").strip() and p.caldav is None and p.id != "other":
            raise CalendarError(p.note or f"{p.name} calendars can't be connected with an app password.")
        start = (url or "").strip() or (p.caldav.format(address=address) if p.caldav else "")
        if not re.match(r"^https://[A-Za-z0-9.-]+(:\d+)?(/\S*)?$", start):
            raise CalendarError("The calendar (CalDAV) address must start with https://.")
        names = [username.strip()] if username and username.strip() else providers.usernames(p, address)

        last: CheckError | None = None
        for name in names:
            try:
                calendars = self.make_backend(start, name, lambda: password, None).check()
            except CheckError as exc:
                last = exc
                if exc.stage == "login":
                    continue
                return {"connected": False, "stage": exc.stage, "message": str(exc)}
            if not calendars:
                return {"connected": False, "stage": "empty", "message": "the account has no calendars"}
            chosen = calendar if calendar in calendars else calendars[0]
            settings = {"provider": p.id, "address": address, "url": start, "username": name,
                        "calendar": chosen, "calendars": calendars}
            with self._lock:
                self.store.save(settings, password)
                self._activate(settings)
            return {"connected": True, "address": address, "provider": p.id, "calendar": chosen, "calendars": calendars}
        assert last is not None
        return {"connected": False, "stage": last.stage, "message": str(last)}

    # ---- Sign in with Google (calendar only: its own grant) -------------------------------------

    def google_start(self, redirect_uri: str, address: str | None = None) -> dict[str, Any]:
        if self.fixed or self.store is None or self.signin is None:
            raise CalendarError("Signing in with Google isn't set up on this computer.")
        try:
            return self.signin.start([CALENDAR_SCOPE], redirect_uri, login_hint=(address or "").strip() or None)
        except GoogleError as exc:
            raise CalendarError(str(exc)) from None

    def google_finish(self, code: str | None = None, state: str | None = None, error: str | None = None) -> dict[str, Any]:
        if self.signin is None or self.store is None or self.google is None:
            raise CalendarError("Signing in with Google isn't set up on this computer.")
        try:
            got = self.signin.finish(code, state, error)
        except GoogleError as exc:
            return {"connected": False, "stage": "google", "message": str(exc)}
        address = got.get("email")
        if not address or CALENDAR_SCOPE not in got.get("scope", "").split():
            return {"connected": False, "stage": "google",
                    "message": "Google didn't share access to the calendar. Sign in again and allow Foyelle to use Google Calendar."}
        url = GOOGLE_CALDAV.format(address=address)
        access = got["access_token"]
        try:
            calendars = self.make_backend(url, address, lambda: access, None, bearer=True).check()
        except CheckError as exc:
            return {"connected": False, "stage": exc.stage, "message": str(exc)}
        if not calendars:
            return {"connected": False, "stage": "empty", "message": "the account has no calendars"}
        settings = {"provider": "gmail", "auth": "bearer", "address": address, "url": url, "username": address,
                    "calendar": calendars[0], "calendars": calendars}
        with self._lock:
            self.store.save(settings, got["refresh_token"])
            self._activate(settings, first_token=(access, int(got.get("expires_in", 3600))))
        return {"connected": True, "address": address, "provider": "gmail", "calendar": calendars[0], "calendars": calendars}

    def disconnect(self) -> dict[str, Any]:
        if self.fixed or self.store is None:
            raise CalendarError("This computer's calendar is set in its configuration; it can't be removed here.")
        with self._lock:
            removed = self.store.remove()
            self._service = None
            self._settings = None
        return {"connected": False, "removed": removed}
