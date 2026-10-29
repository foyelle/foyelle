"""What the routines said: each run's result, for the bot's conversation.

Hermes writes every routine (cron job) run to ``<profile>/cron/output/<job id>/<time>.md``: a
header (``# Cron Job: <name>``, the run time, the schedule, the prompt) and the response, framed
by ``**Response Characters:** <n>`` and ``## Response``. Foyelle's own routines are in the harness
home, each bot's in ``profiles/<bot>/``. The shell helper runs as the harness user and reads them,
so the canvas shows results without opening every run as a conversation.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

DEFAULT = "default"
MAX_RESPONSE = 6000
_TITLE = re.compile(r"^# Cron Job: (.+)$", re.M)
_FRAME = re.compile(r"\*\*Response Characters:\*\* (\d+)\n## Response\n\n")
_HEADING = "## Response\n\n"
_PROFILE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def parse(text: str) -> dict[str, Any]:
    """The run's name, whether it failed, and its response (the error for a failed run)."""
    title = _TITLE.search(text)
    name = title.group(1).strip() if title else ""
    failed = name.endswith("(FAILED)") or "**Status:** BLOCKED" in text
    name = name.removesuffix("(FAILED)").strip()
    frame = _FRAME.search(text)
    if frame:
        start = frame.end()
        response = text[start:start + int(frame.group(1))]
    elif _HEADING in text:
        response = text.split(_HEADING, 1)[1]
    else:
        body = text.split("\n\n", 1)
        response = body[1] if failed and len(body) > 1 else ""
    response = response.strip()
    if len(response) > MAX_RESPONSE:
        response = response[:MAX_RESPONSE] + "…"
    return {"name": name, "failed": failed, "response": response}


def results(harness_home: Path, *, since: float = 0.0, limit: int = 40) -> dict[str, Any]:
    """Runs newer than ``since`` (seconds), newest first, from Foyelle and every bot."""
    homes = [(DEFAULT, harness_home)]
    profiles = harness_home / "profiles"
    if profiles.is_dir():
        homes += [(p.name, p) for p in sorted(profiles.iterdir()) if p.is_dir() and _PROFILE.match(p.name)]
    runs = []
    for bot, home in homes:
        output = home / "cron" / "output"
        if not output.is_dir():
            continue
        for path in output.glob("*/*.md"):
            try:
                ran = path.stat().st_mtime
                if ran <= since:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            runs.append({"bot": bot, "job": path.parent.name, "run": path.stem, "ran": ran, **parse(text)})
    runs.sort(key=lambda r: r["ran"], reverse=True)
    return {"runs": runs[:max(1, min(limit, 200))]}
