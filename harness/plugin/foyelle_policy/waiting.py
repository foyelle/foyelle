"""Waiting items: what a routine wanted to do that needs the person.

A background run (a routine, a heartbeat turn) never waits for an approval nobody can give. A
call that would ask is saved here instead, with what it would do; the person approves it later
from the bot's conversation (the canvas runs it then, with the person as the origin, through the
usual approval card) or drops it. One JSON file per item, in the shell helper's state, which
lists and drops them; an item is offered for a day.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

WAITING_DIR = Path(os.environ.get("FOYELLE_WAITING_DIR", "/var/lib/foyelle/connectors/shell/waiting"))
WAITING_TTL_SECONDS = 24 * 3600


class Waiting:
    def __init__(self, directory: Path = WAITING_DIR) -> None:
        self.directory = directory

    def add(self, *, tool_name: str, connector: str | None, tool: str, tier: str, args: dict[str, Any],
            summary: str, profile: str, session_id: str, job_id: str | None, now: float | None = None) -> str:
        now = time.time() if now is None else now
        item_id = secrets.token_hex(8)
        item = {"id": item_id, "toolName": tool_name, "connector": connector, "tool": tool, "tier": tier,
                "args": args, "summary": summary, "bot": profile, "sessionId": session_id, "jobId": job_id,
                "created": now, "expires": now + WAITING_TTL_SECONDS}
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = self.directory / f".{item_id}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o660)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(item, fh, ensure_ascii=False, default=str)
        tmp.replace(self.directory / f"{item_id}.json")
        return item_id


def job_of(session_id: str) -> str | None:
    """The routine (cron job) running now: Hermes's cron execution identity, or a run's session id
    ``cron_<jobid>_<YYYYmmdd_HHMMSS>``."""
    try:
        from cron.execution_identity import current_cron_execution

        run = current_cron_execution()
        if run is not None and run.job_id:
            return str(run.job_id)
    except Exception:
        pass
    if session_id.startswith("cron_"):
        job = session_id[5:].split("_", 1)[0]
        return job or None
    return None
