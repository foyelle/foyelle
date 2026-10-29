"""Calendar tool logic over an injectable CalDAV backend, using icalendar."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any, Protocol

import icalendar

PRODID = "-//Foyelle//Calendar connector//EN"
MAX_DESCRIPTION = 4000
PARTSTAT = {"accept": "ACCEPTED", "decline": "DECLINED", "tentative": "TENTATIVE"}


class CalendarError(RuntimeError):
    """A calendar operation failed or was refused."""


class CalendarBackend(Protocol):
    """Raw iCalendar access to one calendar collection."""

    def search(self, start: datetime, end: datetime) -> list[str]: ...
    def get(self, uid: str) -> str: ...
    def create(self, ical: str) -> None: ...
    def update(self, uid: str, ical: str) -> None: ...
    def delete(self, uid: str) -> None: ...


@dataclass
class CalendarSettings:
    timezone: tzinfo = UTC
    user_address: str | None = None  # the user's attendee address (mailto: optional)


def _addr(value: Any) -> str:
    return str(value).removeprefix("mailto:").removeprefix("MAILTO:").strip().lower()


def _when(value: date | datetime | None, tz: tzinfo) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=tz)).astimezone(tz).isoformat()
    return value.isoformat()


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def parse_time(value: str, tz: tzinfo, *, all_day: bool = False) -> date | datetime:
    """ISO date or datetime; naive datetimes are in the configured time zone."""
    try:
        if all_day or len(value) == 10:
            parsed = date.fromisoformat(value[:10])
            return parsed if all_day else datetime.combine(parsed, time(0), tz)
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise CalendarError(f"not an ISO date or date-time: {value!r}") from None
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)


class CalendarService:
    def __init__(self, backend: CalendarBackend, settings: CalendarSettings, now: Callable[[], datetime] | None = None):
        self.backend = backend
        self.settings = settings
        self.now = now or (lambda: datetime.now(UTC))

    # ---- reading ---------------------------------------------------------

    def _event(self, comp: icalendar.Event) -> dict[str, Any]:
        tz = self.settings.timezone
        start = comp.decoded("dtstart") if "DTSTART" in comp else None
        end = comp.decoded("dtend") if "DTEND" in comp else None
        if end is None and start is not None and "DURATION" in comp:
            end = start + comp.decoded("duration")
        me = _addr(self.settings.user_address) if self.settings.user_address else None
        attendees = []
        my_status = None
        for att in _list(comp.get("attendee")):
            entry = {
                "email": _addr(att),
                "name": str(att.params.get("CN", "")),
                "status": str(att.params.get("PARTSTAT", "NEEDS-ACTION")),
                "role": str(att.params.get("ROLE", "REQ-PARTICIPANT")),
            }
            attendees.append(entry)
            if me and entry["email"] == me:
                my_status = entry["status"]
        organizer = comp.get("organizer")
        description = str(comp.get("description", ""))
        return {
            "uid": str(comp.get("uid", "")),
            "summary": str(comp.get("summary", "")),
            "start": _when(start, tz),
            "end": _when(end, tz),
            "all_day": start is not None and not isinstance(start, datetime),
            "location": str(comp.get("location", "")),
            "description": description[:MAX_DESCRIPTION],
            "status": str(comp.get("status", "")),
            "organizer": _addr(organizer) if organizer else None,
            "attendees": attendees,
            "my_status": my_status,
            "is_invite": bool(organizer) and me is not None and _addr(organizer) != me and my_status is not None,
            "recurring": "RRULE" in comp or "RECURRENCE-ID" in comp,
            "recurrence_id": _when(comp.decoded("recurrence-id"), tz) if "RECURRENCE-ID" in comp else None,
        }

    def _sort_key(self, ev: dict[str, Any]) -> str:
        return ev["start"] or ""

    def list_events(self, start: str | None = None, end: str | None = None, days: int = 7) -> dict[str, Any]:
        tz = self.settings.timezone
        begin = parse_time(start, tz) if start else datetime.combine(self.now().astimezone(tz).date(), time(0), tz)
        if not isinstance(begin, datetime):
            begin = datetime.combine(begin, time(0), tz)
        finish = parse_time(end, tz) if end else begin + timedelta(days=max(1, min(days, 366)))
        if not isinstance(finish, datetime):
            finish = datetime.combine(finish, time(0), tz)
        if finish <= begin:
            raise CalendarError("end must be after start")
        events = []
        for text in self.backend.search(begin, finish):
            for comp in icalendar.Calendar.from_ical(text).walk("VEVENT"):
                events.append(self._event(comp))
        events.sort(key=self._sort_key)
        return {"start": begin.isoformat(), "end": finish.isoformat(), "timezone": str(tz), "events": events}

    def _load(self, uid: str) -> icalendar.Calendar:
        if not uid or any(c in uid for c in "\r\n"):
            raise CalendarError("invalid event uid")
        return icalendar.Calendar.from_ical(self.backend.get(uid))

    @staticmethod
    def _master(cal: icalendar.Calendar) -> icalendar.Event:
        events = cal.walk("VEVENT")
        if not events:
            raise CalendarError("calendar object has no event")
        return next((e for e in events if "RECURRENCE-ID" not in e), events[0])

    def get_event(self, uid: str) -> dict[str, Any]:
        cal = self._load(uid)
        master = self._master(cal)
        out = self._event(master)
        out["overrides"] = len(cal.walk("VEVENT")) - 1
        return out

    # ---- writing ---------------------------------------------------------

    def _span(self, s: date | datetime, e: date | datetime | None, all_day: bool) -> tuple[date | datetime, date | datetime]:
        """Validate a start/end pair; timed events are stored in UTC."""
        tz = self.settings.timezone
        if e is None:
            e = s + (timedelta(days=1) if all_day else timedelta(hours=1))
        if not all_day:
            s = (s if isinstance(s, datetime) else datetime.combine(s, time(0), tz))
            e = (e if isinstance(e, datetime) else datetime.combine(e, time(0), tz))
            s = (s if s.tzinfo else s.replace(tzinfo=tz)).astimezone(UTC)
            e = (e if e.tzinfo else e.replace(tzinfo=tz)).astimezone(UTC)
        if e <= s:  # type: ignore[operator]
            raise CalendarError("end must be after start")
        return s, e

    def create_event(
        self,
        summary: str,
        start: str,
        end: str | None = None,
        location: str | None = None,
        description: str | None = None,
        all_day: bool = False,
        status: str = "TENTATIVE",
    ) -> dict[str, Any]:
        status = status.upper()
        if status not in ("TENTATIVE", "CONFIRMED"):
            raise CalendarError("status must be TENTATIVE or CONFIRMED")
        tz = self.settings.timezone
        s, e = self._span(
            parse_time(start, tz, all_day=all_day), parse_time(end, tz, all_day=all_day) if end else None, all_day
        )
        cal = icalendar.Calendar()
        cal.add("prodid", PRODID)
        cal.add("version", "2.0")
        ev = icalendar.Event()
        ev.add("uid", f"{uuid.uuid4()}@foyelle")
        ev.add("dtstamp", self.now().astimezone(UTC))
        ev.add("dtstart", s)
        ev.add("dtend", e)
        ev.add("summary", summary)
        ev.add("status", status)
        ev.add("sequence", 0)
        if location:
            ev.add("location", location)
        if description:
            ev.add("description", description)
        cal.add_component(ev)
        self.backend.create(cal.to_ical().decode())
        return self._event(ev)

    def update_event(
        self,
        uid: str,
        summary: str | None = None,
        start: str | None = None,
        end: str | None = None,
        location: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        cal = self._load(uid)
        ev = self._master(cal)
        if ev.get("attendee"):
            raise CalendarError(
                "this event has attendees; changing it would notify them. Use respond_invite for invitations."
            )
        all_day = "DTSTART" in ev and not isinstance(ev.decoded("dtstart"), datetime)
        if start or end:
            tz = self.settings.timezone
            cur_start = ev.decoded("dtstart")
            cur_end = ev.decoded("dtend") if "DTEND" in ev else None
            new_start = parse_time(start, tz, all_day=all_day) if start else cur_start
            if end:
                new_end = parse_time(end, tz, all_day=all_day)
            elif cur_end is not None:
                new_end = new_start + (cur_end - cur_start)
            else:
                new_end = None
            s, e = self._span(new_start, new_end, all_day)
            for key, value in (("dtstart", s), ("dtend", e)):
                ev.pop(key, None)
                ev.add(key, value)
            ev.pop("duration", None)
        for key, value in (("summary", summary), ("location", location), ("description", description)):
            if value is not None:
                ev.pop(key, None)
                if value:
                    ev.add(key, value)
        if status is not None:
            if status.upper() not in ("TENTATIVE", "CONFIRMED", "CANCELLED"):
                raise CalendarError("status must be TENTATIVE, CONFIRMED or CANCELLED")
            ev.pop("status", None)
            ev.add("status", status.upper())
        self._touch(ev)
        self.backend.update(uid, cal.to_ical().decode())
        return self._event(ev)

    def _touch(self, ev: icalendar.Event) -> None:
        seq = int(ev.get("sequence", 0)) + 1
        for key, value in (("sequence", seq), ("dtstamp", self.now().astimezone(UTC)), ("last-modified", self.now().astimezone(UTC))):
            ev.pop(key, None)
            ev.add(key, value)

    def respond_invite(self, uid: str, response: str, comment: str | None = None) -> dict[str, Any]:
        partstat = PARTSTAT.get(response.lower())
        if partstat is None:
            raise CalendarError("response must be accept, decline or tentative")
        me = _addr(self.settings.user_address) if self.settings.user_address else None
        if not me:
            raise CalendarError("FOYELLE_CALENDAR_EMAIL is not configured; cannot identify the user's attendee entry")
        cal = self._load(uid)
        found = False
        for ev in cal.walk("VEVENT"):
            for att in _list(ev.get("attendee")):
                if _addr(att) == me:
                    att.params["PARTSTAT"] = partstat
                    att.params.pop("RSVP", None)
                    found = True
            if comment:
                ev.pop("comment", None)
                ev.add("comment", comment)
        if not found:
            raise CalendarError("you are not an attendee of this event")
        # Attendee replies don't bump SEQUENCE (RFC 5546); just DTSTAMP.
        master = self._master(cal)
        master.pop("dtstamp", None)
        master.add("dtstamp", self.now().astimezone(UTC))
        self.backend.update(uid, cal.to_ical().decode())
        return self._event(master) | {"response": partstat}

    def delete_event(self, uid: str) -> dict[str, Any]:
        info = self.get_event(uid)
        self.backend.delete(uid)
        return {"deleted": uid, "summary": info["summary"], "start": info["start"]}
