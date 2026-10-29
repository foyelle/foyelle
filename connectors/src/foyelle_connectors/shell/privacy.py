"""Today's privacy summary: what left the computer, what the online model was shown, what was held back.

Built from the audit log alone. The log keeps a digest of each call's arguments, never the
arguments themselves, so the summary says *what kind* of thing went out and when, not its content.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .audit import iter_lines_reversed

# Tools that send something off the computer (outbound tier or egress in the default policy).
# The image passes its own list (FOYELLE_OUTBOUND_TOOLS), so connectors added later count too.
DEFAULT_OUTBOUND = frozenset({
    "email.send", "email.draft", "email.delete_draft",
    "calendar.create_event", "calendar.update_event", "calendar.respond_invite", "calendar.delete_event",
    "web.search", "web.fetch",
})

# What each tool did, in plain words: (one, many).
LABELS: dict[str, tuple[str, str]] = {
    "email.send": ("Sent an email", "Sent {n} emails"),
    "email.draft": ("Saved a draft in your mail account", "Saved {n} drafts in your mail account"),
    "email.delete_draft": ("Deleted a draft in your mail account", "Deleted {n} drafts in your mail account"),
    "calendar.create_event": ("Added an event to your online calendar", "Added {n} events to your online calendar"),
    "calendar.update_event": ("Changed an event in your online calendar", "Changed {n} events in your online calendar"),
    "calendar.respond_invite": ("Answered an invitation", "Answered {n} invitations"),
    "calendar.delete_event": ("Removed an event from your online calendar", "Removed {n} events from your online calendar"),
    "web.search": ("Searched the web", "Searched the web {n} times"),
    "web.fetch": ("Opened a web page", "Opened {n} web pages"),
}

# The thing itself, for what was held back: "An email (you said not now)".
NOUNS = {
    "email.send": "An email", "email.draft": "A draft for your mail account", "email.delete_draft": "Deleting a draft",
    "calendar.create_event": "A new calendar event", "calendar.update_event": "A calendar change",
    "calendar.respond_invite": "An answer to an invitation", "calendar.delete_event": "Removing a calendar event",
    "web.search": "A web search", "web.fetch": "A web page",
}

# What each connector holds, for "the online model read your …".
HOLDS = {
    "files": "files", "email": "email", "calendar": "calendar", "shell": "settings and memories",
    "system": "computer settings", "sysmon": "computer details", "workshop": "cards",
}

# Scan at most this many log lines for one day; a day is far fewer.
MAX_LINES = 50_000


def parse_outbound(value: str | None) -> frozenset[str]:
    names = {v.strip() for v in (value or "").split(",") if v.strip()}
    return frozenset(names) if names else DEFAULT_OUTBOUND


def _key(entry: dict[str, Any]) -> str:
    """``connector.tool`` from a record (the tool may be the harness's ``mcp__files__list``)."""
    tool = str(entry.get("tool") or "")
    connector = str(entry.get("connector") or "")
    for prefix in (f"mcp__{connector}__", f"mcp_{connector}_"):
        if connector and tool.startswith(prefix):
            tool = tool[len(prefix):]
            break
    return f"{connector}.{tool}" if connector else tool


def _label(key: str, n: int) -> str:
    one, many = LABELS.get(key, (f"Used {key.split('.')[0]}", f"Used {key.split('.')[0]} {{n}} times"))
    return one if n == 1 else many.format(n=n)


def _when(entry: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(entry.get("ts", "")).replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def today(
    audit_log: Path,
    *,
    outbound: frozenset[str] = DEFAULT_OUTBOUND,
    model: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now()).astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    went: dict[str, list[str]] = {}
    shared: dict[str, list[str]] = {}
    held: list[dict[str, Any]] = []
    calls = 0
    if audit_log.exists():
        for i, raw in enumerate(iter_lines_reversed(audit_log)):
            if i >= MAX_LINES:
                break
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            when = _when(entry)
            if when is None:
                continue
            if when < start:
                break  # the log is in time order: everything further back is an earlier day
            if when > now + timedelta(minutes=5):
                continue
            calls += 1
            key = _key(entry)
            ts = when.isoformat(timespec="seconds")
            ran = (entry.get("decision") in ("allow", "ask") and entry.get("approval") != "denied"
                   and (entry.get("status") or "ok") == "ok")
            reason = str(entry.get("reason") or "")
            if "remote model" in reason:
                # A private connector's result shown to the online model (asked once per conversation).
                if ran:
                    shared.setdefault(str(entry.get("connector") or ""), []).append(ts)
                else:
                    what = HOLDS.get(str(entry.get("connector")), str(entry.get("connector")))
                    held.append({"label": f"Your {what}, kept from the online model", "ts": ts})
                continue
            if key not in outbound:
                continue
            if ran:
                went.setdefault(key, []).append(ts)
            elif entry.get("approval") == "denied" or entry.get("decision") == "deny":
                why = "you said not now" if entry.get("approval") == "denied" else "not allowed"
                held.append({"label": f"{NOUNS.get(key, f'Using {key}')} ({why})", "ts": ts})

    m = model or {}
    online = m.get("route", "remote") != "local" and bool(m.get("remote_key_configured", True))
    return {
        "date": start.date().isoformat(),
        "calls": calls,
        "model": {
            "online": online,
            "provider": m.get("provider_label") or m.get("provider") or "",
            "name": (m.get("main") if online else m.get("local")) or "",
        },
        "went_out": [
            {"key": k, "label": _label(k, len(v)), "count": len(v), "times": sorted(v)}
            for k, v in sorted(went.items(), key=lambda kv: -len(kv[1]))
        ],
        "shared": [
            {"connector": c, "label": HOLDS.get(c, c), "count": len(v), "times": sorted(v)}
            for c, v in sorted(shared.items())
        ],
        "held_back": sorted(held, key=lambda e: e["ts"]),
    }
