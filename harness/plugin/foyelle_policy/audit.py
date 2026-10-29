"""Append-only audit log of every tool call, approval and surface-initiated call."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional


def args_digest(args: Any) -> str:
    """A stable digest of the arguments: the log proves what ran without copying message bodies."""
    try:
        blob = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        blob = str(args)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def append(self, record: dict) -> None:
        record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z", **record}
        line = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # O_APPEND: each write lands at the end even if another process holds the file open;
            # the file itself is chattr +a on the image, so nothing can rewrite earlier lines.
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
            try:
                os.write(fd, line.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)

    def read(self, limit: Optional[int] = None) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        if limit is not None:
            lines = lines[-limit:]
        return [json.loads(line) for line in lines if line.strip()]
