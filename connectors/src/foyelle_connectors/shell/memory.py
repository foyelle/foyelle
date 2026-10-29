"""Hermes memory files (MEMORY.md, USER.md): browse, edit with backup, export."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..sdk.fsutil import atomic_write

MEMORY_FILES = ("MEMORY.md", "USER.md")
ENTRY_DELIMITER = "§"  # Hermes separates entries with a line holding only "§".
MAX_BYTES = 256 * 1024
BACKUP_DIR = ".foyelle-backup"
KEEP_BACKUPS = 20
# An entry that records the user's name ("User's name is Sam.", "The user's name is Alex").
NAME_ENTRY = re.compile(r"^(the )?user'?s name is\b", re.IGNORECASE)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def split_entries(content: str) -> list[str]:
    entries, current = [], []
    for line in content.splitlines():
        if line.strip() == ENTRY_DELIMITER:
            entries.append("\n".join(current).strip())
            current = []
        else:
            current.append(line)
    entries.append("\n".join(current).strip())
    return [e for e in entries if e]


def _check_name(name: str) -> str:
    if name not in MEMORY_FILES:
        raise ValueError(f"file must be one of {', '.join(MEMORY_FILES)}")
    return name


class MemoryStore:
    def __init__(self, directory: Path) -> None:
        self.dir = directory

    def _describe(self, name: str) -> dict[str, Any]:
        path = self.dir / name
        try:
            data = path.read_bytes()
            mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
        except FileNotFoundError:
            data, mtime = b"", None
        content = data.decode("utf-8", errors="replace")
        return {
            "name": name,
            "exists": mtime is not None,
            "content": content,
            "entries": split_entries(content),
            "sha256": _sha(data),
            "size": len(data),
            "modified": mtime,
        }

    def read(self, name: str | None = None) -> dict[str, Any]:
        names = [_check_name(name)] if name else list(MEMORY_FILES)
        return {"directory": str(self.dir), "files": [self._describe(n) for n in names]}

    def write(self, name: str, content: str, expected_sha256: str | None = None) -> dict[str, Any]:
        """Replace a memory file. The previous version is kept as a backup.

        ``expected_sha256`` guards against overwriting a concurrent change
        made by the harness since the caller read the file.
        """
        _check_name(name)
        data = content.encode("utf-8")
        if len(data) > MAX_BYTES:
            raise ValueError(f"memory file too large ({len(data)} bytes, max {MAX_BYTES})")
        path = self.dir / name
        old = path.read_bytes() if path.exists() else b""
        if expected_sha256 and expected_sha256 != _sha(old):
            raise ValueError("the file changed since it was read; reload and try again")
        backup = None
        if old:
            backup = self._backup(name, old)
        self.dir.mkdir(parents=True, exist_ok=True)
        atomic_write(path, data)
        return {"name": name, "sha256": _sha(data), "size": len(data), "backup": backup}

    def set_name(self, name: str) -> dict[str, Any]:
        """Record the user's name in USER.md, replacing an earlier name entry."""
        path = self.dir / "USER.md"
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        entries = [e for e in split_entries(text) if e and not NAME_ENTRY.match(e)]
        entries.insert(0, f"User's name is {name}.")
        return self.write("USER.md", f"\n{ENTRY_DELIMITER}\n".join(entries) + "\n")

    def _backup(self, name: str, data: bytes) -> str:
        bdir = self.dir / BACKUP_DIR
        bdir.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        target = bdir / f"{name}.{stamp}"
        target.write_bytes(data)
        backups = sorted(bdir.glob(f"{name}.*"))
        for stale in backups[:-KEEP_BACKUPS]:
            stale.unlink(missing_ok=True)
        return target.name

    def export(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        parts = [f"# Foyelle memory export\n\nExported {now.isoformat(timespec='seconds')}.\n"]
        for name in MEMORY_FILES:
            info = self._describe(name)
            parts.append(f"\n## {name}\n\n{info['content'].strip() or '_(empty)_'}\n")
        return {"filename": f"foyelle-memory-{now:%Y%m%d}.md", "mime_type": "text/markdown", "content": "".join(parts)}
