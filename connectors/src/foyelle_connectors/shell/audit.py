"""Read-only queries over the append-only audit log (JSON lines), newest first."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

MAX_LIMIT = 500
_BLOCK = 64 * 1024


def iter_lines_reversed(path: Path) -> Iterator[bytes]:
    """Yield the file's lines from last to first without reading it whole."""
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        rest = b""
        while pos > 0:
            step = min(_BLOCK, pos)
            pos -= step
            fh.seek(pos)
            chunk = fh.read(step) + rest
            lines = chunk.split(b"\n")
            rest = lines.pop(0)
            for line in reversed(lines):
                if line.strip():
                    yield line
        if rest.strip():
            yield rest


def query(
    path: Path,
    *,
    session_id: str | None = None,
    tool: str | None = None,
    connector: str | None = None,
    decision: str | None = None,
    origin: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    limit = max(1, min(limit, MAX_LIMIT))
    filters = {"session_id": session_id, "connector": connector, "decision": decision, "origin": origin}
    entries: list[dict[str, Any]] = []
    matched = 0
    malformed = 0
    has_more = False
    if path.exists():
        for raw in iter_lines_reversed(path):
            try:
                entry = json.loads(raw)
            except ValueError:
                malformed += 1
                continue
            if not isinstance(entry, dict):
                malformed += 1
                continue
            if any(v and entry.get(k) != v for k, v in filters.items()):
                continue
            if tool and tool not in str(entry.get("tool", "")):
                continue
            matched += 1
            if matched <= offset:
                continue
            if len(entries) == limit:
                has_more = True
                break
            entries.append(entry)
    return {
        "log": str(path),
        "exists": path.exists(),
        "entries": entries,
        "offset": offset,
        "has_more": has_more,
        "skipped_malformed": malformed,
    }
