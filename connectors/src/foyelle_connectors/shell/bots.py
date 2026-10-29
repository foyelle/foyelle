"""The person's bots, and what their routines left waiting.

A bot is a named helper with a picture, a job and the services it may use. It is a Hermes profile
(its id); the canvas creates the profile through the harness and keeps the bot here, in
``bots.json``, which the policy plugin reads to decide what each bot may use (the harness runs as
the same user). Foyelle itself is the default profile; an entry with the id ``default`` holds only
which private services its routines may share with the online model.

Waiting items are written by the policy plugin when a routine wanted to do something that needs
the person (one JSON file each in ``waiting/``); the canvas lists them, runs one through the usual
approval when the person approves it, and drops it.
"""

from __future__ import annotations

import json
import re
import secrets
import time
import unicodedata
from pathlib import Path
from typing import Any

from ..sdk.fsutil import atomic_write

DEFAULT = "default"
MAX_BOTS = 12
# The connectors a bot can be given besides the connected services, with the names people use.
# Never the system connector or the workshop (Foyelle's own); the shell helper every bot has.
CORE = {"calendar": ("Calendar", True), "email": ("Mail", True), "files": ("Files", True),
        "weather": ("Weather", False), "web": ("Web search", False)}
_ID = re.compile(r"^bot-[a-z0-9][a-z0-9_-]{0,59}$")


def givable(enabled: list[str] | set[str], services: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What a bot can be given: Foyelle's own helpers that run here, and the connected services."""
    out = [{"id": c, "title": title, "private": private} for c, (title, private) in CORE.items() if c in enabled]
    out += [{"id": s["id"], "title": s["title"], "private": bool(s.get("private"))}
            for s in services if s.get("connected")]
    return out


def slug(name: str) -> str:
    """A profile id from a bot's name: ``bot-`` and its ASCII letters; a name with none (any script
    but Latin) gets a short random one."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    words = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")[:40].strip("-")
    return f"bot-{words}" if words else f"bot-{secrets.token_hex(3)}"


def _strings(value: Any) -> list[str]:
    return [s for s in value if isinstance(s, str)] if isinstance(value, list) else []


class BotStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, PermissionError, ValueError):
            return []
        bots = data.get("bots") if isinstance(data, dict) else None
        return [b for b in bots if isinstance(b, dict) and isinstance(b.get("id"), str)] if isinstance(bots, list) else []

    def _write(self, bots: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, (json.dumps({"bots": bots}, ensure_ascii=False, indent=2) + "\n").encode())

    def listing(self, can_give: list[dict[str, Any]]) -> dict[str, Any]:
        bots = self._read()
        foyelle = next((b for b in bots if b["id"] == DEFAULT), {"id": DEFAULT, "shareOnline": []})
        return {"bots": [b for b in bots if b["id"] != DEFAULT],
                "foyelle": {"shareOnline": _strings(foyelle.get("shareOnline"))},
                "givable": can_give}

    def save(self, can_give: list[dict[str, Any]], *, name: str, job: str = "", picture: str = "",
             services: list[str] | None = None, share_online: list[str] | None = None,
             bot_id: str | None = None) -> dict[str, Any]:
        name = " ".join((name or "").split())
        if not name or len(name) > 40:
            raise ValueError("a bot needs a name of 1 to 40 characters")
        job = " ".join((job or "").split())
        if len(job) > 240:
            raise ValueError("keep the job to one or two sentences (240 characters)")
        picture = (picture or "").strip()
        if len(picture) > 16:
            raise ValueError("the picture is one emoji or a colour name")
        ids = {g["id"] for g in can_give}
        private = {g["id"] for g in can_give if g.get("private")}
        chosen = sorted(set(_strings(services or [])))
        unknown = [s for s in chosen if s not in ids]
        if unknown:
            raise ValueError(f"not something a bot can use here: {', '.join(unknown)}")
        shared = sorted(set(_strings(share_online or [])) & set(chosen) & private)
        bots = self._read()
        if bot_id:
            existing = next((b for b in bots if b["id"] == bot_id and b["id"] != DEFAULT), None)
            if existing is None:
                raise ValueError(f"no bot {bot_id!r}")
        else:
            if sum(1 for b in bots if b["id"] != DEFAULT) >= MAX_BOTS:
                raise ValueError(f"at most {MAX_BOTS} bots")
            taken = {b["id"] for b in bots}
            bot_id = slug(name)
            base, n = bot_id, 2
            while bot_id in taken:
                bot_id, n = f"{base}-{n}"[:64], n + 1
            existing = {"id": bot_id, "created": time.time()}
            bots.append(existing)
        if not _ID.match(bot_id):
            raise ValueError(f"{bot_id!r} is not a bot id")
        existing.update(name=name, job=job, picture=picture, services=chosen, shareOnline=shared)
        self._write(bots)
        return dict(existing)

    def delete(self, bot_id: str) -> dict[str, Any]:
        bots = self._read()
        kept = [b for b in bots if b["id"] != bot_id or b["id"] == DEFAULT]
        if len(kept) == len(bots):
            raise ValueError(f"no bot {bot_id!r}")
        self._write(kept)
        return {"deleted": bot_id}

    def share_for_foyelle(self, can_give: list[dict[str, Any]], share_online: list[str]) -> dict[str, Any]:
        """Which private services Foyelle's own routines may share with the online model."""
        private = {g["id"] for g in can_give if g.get("private")}
        shared = sorted(set(_strings(share_online)) & private)
        bots = [b for b in self._read() if b["id"] != DEFAULT]
        self._write([{"id": DEFAULT, "shareOnline": shared}, *bots])
        return {"shareOnline": shared}


class WaitingStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def listing(self, now: float | None = None) -> dict[str, Any]:
        """What routines left for the person, newest first; expired items are removed."""
        now = time.time() if now is None else now
        items = []
        for path in sorted(self.directory.glob("*.json")) if self.directory.is_dir() else []:
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(item, dict) or float(item.get("expires") or 0) <= now:
                path.unlink(missing_ok=True)
                continue
            items.append(item)
        items.sort(key=lambda i: float(i.get("created") or 0), reverse=True)
        return {"items": items}

    def drop(self, item_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{16}", item_id or ""):
            raise ValueError("not a waiting item")
        path = self.directory / f"{item_id}.json"
        if not path.exists():
            raise ValueError("that item is no longer waiting")
        path.unlink()
        return {"dropped": item_id}
