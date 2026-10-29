"""The services the person connected, for the Services card.

The directory (``foyelle_connectors.services.directory``) lists what can be connected. Connecting
one only records the choice in ``services.json`` next to the settings overrides; the system's
``foyelle-services-sync`` (bridges.nix) watches that file and starts or stops the service's helper
and egress proxy, for services in the directory only. The canvas then asks the harness to reload
its MCP servers, so the service's tools reach the agent.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..sdk.fsutil import atomic_write
from ..services import directory


class ServicesState:
    def __init__(self, connected_file: Path, system_file: Path = Path("/etc/foyelle/services.json"),
                 directory_path: Path = directory.DIRECTORY) -> None:
        self.connected_file = connected_file
        self.system_file = system_file
        self.directory_path = directory_path

    def entries(self) -> dict[str, dict[str, Any]]:
        return directory.load(self.directory_path)

    def _system(self) -> dict[str, Any]:
        """What this machine built: every service's helper, and those always on (development)."""
        try:
            data = json.loads(self.system_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, PermissionError, ValueError):
            return {"available": None, "always": []}
        return data if isinstance(data, dict) else {"available": None, "always": []}

    def connected(self) -> list[str]:
        try:
            data = json.loads(self.connected_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, PermissionError, ValueError):
            return []
        ids = data.get("connected") if isinstance(data, dict) else None
        return [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else []

    def _write(self, ids: list[str]) -> None:
        self.connected_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.connected_file, (json.dumps({"connected": sorted(set(ids))}, indent=2) + "\n").encode())

    def _rows(self) -> dict[str, dict[str, Any]]:
        """Every service this machine can connect: the directory's, and those added by URL (known
        from the machine's services file, which carries their names)."""
        rows: dict[str, dict[str, Any]] = {}
        for eid, e in self.entries().items():
            rows[eid] = {"id": eid, "title": e["title"], "category": e["category"], "summary": e["summary"],
                         "account": e["auth"]["kind"] == "oauth", "private": e["sensitivity"] == "private",
                         "reviewed": e["reviewed"]}
        available = self._system().get("available")
        if isinstance(available, dict):
            for eid, meta in available.items():
                if eid in rows or not isinstance(meta, dict) or not isinstance(meta.get("title"), str):
                    continue
                rows[eid] = {"id": eid, "title": meta["title"], "category": str(meta.get("category") or "other"),
                             "summary": str(meta.get("summary") or ""), "account": bool(meta.get("account")),
                             "private": meta.get("private") is not False, "reviewed": None}
            rows = {eid: r for eid, r in rows.items() if eid in available}  # only what this machine built
        return rows

    def listing(self, state_of: Callable[[str], str] | None = None) -> dict[str, Any]:
        """Every service this machine can connect, with whether it is connected and its helper's state."""
        always = set(self._system().get("always") or [])
        connected = set(self.connected())
        out = []
        for eid, row in sorted(self._rows().items(), key=lambda kv: (kv[1]["category"], kv[1]["title"].lower())):
            on = eid in connected or eid in always
            out.append({**row, "connected": on, "always": eid in always,
                        "state": (state_of(eid) if state_of and on else ("active" if on else "inactive"))})
        return {"services": out}

    def set(self, service_id: str, connected: bool) -> dict[str, Any]:
        rows = self._rows()
        if service_id not in rows:
            raise ValueError(f"{service_id!r} is not a service this computer can connect")
        if service_id in set(self._system().get("always") or []):
            raise ValueError(f"{rows[service_id]['title']} is always on on this computer")
        ids = set(self.connected())
        (ids.add if connected else ids.discard)(service_id)
        self._write(sorted(ids))
        return {"id": service_id, "connected": connected}


_SESSION = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class ConversationServices:
    """Which services a conversation turned on or off: one JSON file per conversation, the format the
    policy plugin's conversation.py reads (and writes when the model calls use_service)."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not _SESSION.match(session_id):
            raise ValueError("session_id must be a conversation's stored id")
        return self.directory / f"{session_id}.json"

    def get(self, session_id: str) -> dict[str, list[str]]:
        path = self._path(session_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, PermissionError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        return {k: sorted(s for s in data.get(k, []) if isinstance(s, str)) for k in ("on", "off")}

    def set(self, session_id: str, service: str, on: bool) -> dict[str, list[str]]:
        state = {k: set(v) for k, v in self.get(session_id).items()}
        (state["on"] if on else state["off"]).add(service)
        (state["off"] if on else state["on"]).discard(service)
        path = self._path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps({k: sorted(v) for k, v in state.items()}).encode())
        return self.get(session_id)
