"""Surfaces the agent writes: HTML views saved under the connector's home, with earlier versions.

Layout under ``root``::

    surfaces/<name>.json             {"name", "title", "description", "body", "saved"}
    versions/<name>/<saved>.json     the content each save replaced
    trash/<name>-<removed>.json      removed surfaces (save the same name to bring one back)
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..sdk.fsutil import atomic_write

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
MAX_BODY = 200 * 1024


class WorkshopError(Exception):
    pass


@dataclass
class Surface:
    name: str
    title: str
    description: str
    body: str
    saved: float

    def summary(self) -> dict[str, Any]:
        return {"name": self.name, "title": self.title, "description": self.description,
                "saved": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.saved)), "uri": uri_of(self.name)}


def uri_of(name: str) -> str:
    return f"ui://workshop/s/{name}"


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        for sub in ("surfaces", "versions", "trash"):
            (root / sub).mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, name: str) -> Path:
        if not NAME_RE.match(name or ""):
            raise WorkshopError("name: 1-40 lowercase letters, digits or '-', starting with a letter or digit")
        return self.root / "surfaces" / f"{name}.json"

    def get(self, name: str) -> Surface:
        path = self._path(name)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise WorkshopError(f"no surface named {name!r}; list shows the saved ones") from None
        return Surface(**data)

    def all(self) -> list[Surface]:
        out = []
        for path in sorted((self.root / "surfaces").glob("*.json")):
            try:
                out.append(Surface(**json.loads(path.read_text(encoding="utf-8"))))
            except (ValueError, TypeError):
                continue
        return out

    def save(self, name: str, title: str, body: str, description: str = "") -> tuple[Surface, bool]:
        """Create or replace a surface; the replaced content is kept as a version."""
        path = self._path(name)
        if not title.strip():
            raise WorkshopError("title is required")
        if len(body.encode("utf-8")) > MAX_BODY:
            raise WorkshopError(f"body too large (max {MAX_BODY // 1024} KiB)")
        replaced = path.exists()
        if replaced:
            old = path.read_text(encoding="utf-8")
            vdir = self.root / "versions" / name
            vdir.mkdir(exist_ok=True, mode=0o700)
            atomic_write(vdir / f"{time.time_ns()}.json", old.encode("utf-8"))
        surface = Surface(name, title.strip(), description.strip(), body, time.time())
        atomic_write(path, json.dumps(surface.__dict__).encode("utf-8"))
        return surface, replaced

    def versions(self, name: str) -> list[dict[str, Any]]:
        self._path(name)
        vdir = self.root / "versions" / name
        out = []
        for path in sorted(vdir.glob("*.json"), reverse=True) if vdir.exists() else []:
            data = json.loads(path.read_text(encoding="utf-8"))
            out.append({"version_id": path.stem, "title": data.get("title"),
                        "saved": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(data.get("saved", 0)))})
        return out

    def restore(self, name: str, version_id: str) -> Surface:
        path = self.root / "versions" / name / f"{version_id}.json"
        if not re.fullmatch(r"[0-9]+", version_id or "") or not path.exists():
            raise WorkshopError(f"{name!r} has no version {version_id!r}; versions lists them")
        data = json.loads(path.read_text(encoding="utf-8"))
        surface, _ = self.save(name, data["title"], data["body"], data.get("description", ""))
        return surface

    def remove(self, name: str) -> None:
        path = self._path(name)
        if not path.exists():
            raise WorkshopError(f"no surface named {name!r}")
        path.rename(self.root / "trash" / f"{name}-{time.time_ns()}.json")
