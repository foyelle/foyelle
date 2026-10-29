"""CalDAV backend with the ``caldav`` library (proxy taken from HTTPS_PROXY/HTTP_PROXY)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any

from .service import CalendarError


class CaldavBackend:
    def __init__(
        self,
        url: str,
        username: str,
        password: Callable[[], str],
        calendar_name: str | None = None,
        timeout: int = 30,
        bearer: bool = False,
    ) -> None:
        self.url = url
        self.username = username
        self._password = password
        self.calendar_name = calendar_name
        self.timeout = timeout
        self._calendar: Any = None
        self._lock = threading.Lock()
        # With Google sign-in the secret is an access token (renewed hourly), sent as a bearer token.
        self.bearer = bearer
        self._token: str | None = None

    def _secret(self) -> str:
        from ..sdk.google import GoogleError

        try:
            return self._password()
        except GoogleError as exc:
            raise CalendarError(str(exc)) from None

    def _client(self, secret: str) -> Any:
        import caldav  # imported lazily: it pulls in lxml and niquests

        if self.bearer:
            return caldav.DAVClient(url=self.url, headers={"Authorization": f"Bearer {secret}"},
                                    timeout=self.timeout, enable_rfc6764=False)
        return caldav.DAVClient(url=self.url, username=self.username, password=secret,
                                timeout=self.timeout, enable_rfc6764=False)

    def _cal(self) -> Any:
        with self._lock:
            token = self._secret() if self.bearer else None
            if self._calendar is None or token != self._token:
                try:
                    client = self._client(token if self.bearer else self._secret())  # type: ignore[arg-type]
                    calendars = client.principal().calendars()
                except Exception as exc:  # caldav raises many error types
                    raise CalendarError(f"cannot reach the CalDAV server: {type(exc).__name__}") from None
                if not calendars:
                    raise CalendarError("the CalDAV account has no calendars")
                if self.calendar_name:
                    match = [c for c in calendars if (c.get_display_name() or "") == self.calendar_name]
                    if not match:
                        raise CalendarError(f"no calendar named {self.calendar_name!r}")
                    self._calendar = match[0]
                else:
                    self._calendar = calendars[0]
                self._token = token
            return self._calendar

    def check(self) -> list[str]:
        """Log in and list the account's calendars by name (the Accounts card's check)."""
        from caldav.lib import error as dav_error

        from .account import CheckError

        try:
            client = self._client(self._secret())
            calendars = client.principal().calendars()
        except dav_error.AuthorizationError:
            raise CheckError("login", "the calendar server did not accept the user name and password") from None
        except Exception as exc:  # caldav raises many error types
            raise CheckError("connect", f"cannot reach the calendar server ({type(exc).__name__})") from None
        names = []
        for c in calendars:
            try:
                name = c.get_display_name() or ""
            except Exception:  # noqa: BLE001 - a calendar without a readable name
                name = ""
            if name and name not in names:
                names.append(name)
        return names

    def _by_uid(self, uid: str) -> Any:
        try:
            return self._cal().get_event_by_uid(uid)
        except CalendarError:
            raise
        except Exception:
            raise CalendarError(f"no event with uid {uid!r}") from None

    def search(self, start: datetime, end: datetime) -> list[str]:
        try:
            objs = self._cal().search(start=start, end=end, event=True, expand=True)
        except CalendarError:
            raise
        except Exception as exc:
            raise CalendarError(f"calendar search failed: {type(exc).__name__}") from None
        return [o.data for o in objs]

    def get(self, uid: str) -> str:
        return self._by_uid(uid).data

    def create(self, ical: str) -> None:
        try:
            self._cal().add_event(ical, no_overwrite=True)
        except Exception as exc:
            raise CalendarError(f"could not create the event: {type(exc).__name__}") from None

    def update(self, uid: str, ical: str) -> None:
        obj = self._by_uid(uid)
        obj.data = ical
        try:
            obj.save(increase_seqno=False)
        except Exception as exc:
            raise CalendarError(f"could not save the event: {type(exc).__name__}") from None

    def delete(self, uid: str) -> None:
        try:
            self._by_uid(uid).delete()
        except CalendarError:
            raise
        except Exception as exc:
            raise CalendarError(f"could not delete the event: {type(exc).__name__}") from None
