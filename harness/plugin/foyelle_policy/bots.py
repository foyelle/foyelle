"""Bots: which connectors each of the person's helpers may use.

A bot is a Hermes profile. Foyelle itself is the default profile and uses every connector. Each
other bot uses the connectors it was given, kept by the shell helper in ``bots.json`` (the person
picks them when creating the bot), plus the shell helper's own tools; never the system connector
or the workshop. A profile with no entry in ``bots.json`` gets nothing: fail closed.

``bots.json``: ``{"bots": [{"id": "travel", "name": "Travel", "services": ["kiwi", "calendar"],
"shareOnline": ["calendar"]}, ...]}``. ``shareOnline`` lists the private connectors whose content
the bot's routines may send to the online model (nobody is there to ask during a routine).
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

BOTS_FILE = Path(os.environ.get("FOYELLE_BOTS_FILE", "/var/lib/foyelle/connectors/shell/bots.json"))
DEFAULT = "default"
# Never a bot's: changing the computer and making Foyelle's own surfaces stay with Foyelle.
NEVER_FOR_BOTS = frozenset({"system", "workshop"})
# Every bot's: the shell helper (its conversation's services, the themes).
ALWAYS_FOR_BOTS = frozenset({"shell"})


@dataclass(frozen=True)
class Bot:
    id: str
    name: str = ""
    services: frozenset[str] = frozenset()
    share_online: frozenset[str] = frozenset()


def _strings(value) -> frozenset[str]:
    return frozenset(s for s in value if isinstance(s, str)) if isinstance(value, list) else frozenset()


class Bots:
    """``bots.json``, re-read when it changes."""

    def __init__(self, path: Path = BOTS_FILE) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._mtime = -1.0
        self._bots: dict[str, Bot] = {}

    def get(self, profile: str) -> Optional[Bot]:
        with self._lock:
            try:
                mtime = self.path.stat().st_mtime
            except OSError:
                self._mtime, self._bots = -1.0, {}
                return None
            if mtime != self._mtime:
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    raw = {}
                bots = {}
                for entry in (raw.get("bots") if isinstance(raw, dict) else None) or []:
                    if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                        bots[entry["id"]] = Bot(id=entry["id"], name=str(entry.get("name") or ""),
                                                services=_strings(entry.get("services")),
                                                share_online=_strings(entry.get("shareOnline")))
                self._mtime, self._bots = mtime, bots
            return self._bots.get(profile)

    def allows(self, profile: str, connector: Optional[str]) -> bool:
        """Whether this profile's bot may use this connector (built-in tools: always)."""
        if connector is None or profile in (DEFAULT, ""):
            return True
        if connector in NEVER_FOR_BOTS:
            return False
        if connector in ALWAYS_FOR_BOTS:
            return True
        bot = self.get(profile)
        return bot is not None and connector in bot.services

    def shares_online(self, profile: str, connector: Optional[str]) -> bool:
        """Whether this bot's routines may send this connector's content to the online model.
        Foyelle's own answer is an entry with the id ``default`` (its ``services`` are ignored)."""
        bot = self.get(profile or DEFAULT)
        if bot is None or connector is None:
            return False
        return connector in bot.share_online and (profile in (DEFAULT, "") or connector in bot.services)


def current_profile() -> str:
    """The profile (bot) the current call runs for: a cron tick or a routed turn binds it."""
    try:
        from hermes_cli.profiles import current_profile_name

        return current_profile_name(default=DEFAULT) or DEFAULT
    except Exception:
        return DEFAULT
