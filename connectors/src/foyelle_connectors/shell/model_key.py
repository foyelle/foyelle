"""The remote model's API key, entered on the Settings surface.

The key goes into the harness's own ``.env`` (mode 0600, in its private home), never into Nix, git
or a tool result. The shell connector runs as the harness user, so it can write that file; the
policy marks these tools surface-only, so the model cannot call them.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..sdk.fsutil import atomic_write

# The variables of the configured provider (FOYELLE_REMOTE_KEY_ENV); the first one decides whether a
# key is configured.
DEFAULT_KEY_NAMES = ("OPENCODE_ZEN_API_KEY", "OPENCODE_GO_API_KEY")
_KEY_RE = re.compile(r"^[A-Za-z0-9._:\-]{8,512}$")


def key_names(spec: str | None) -> tuple[str, ...]:
    names = tuple(n for n in (spec or "").split() if re.fullmatch(r"[A-Z][A-Z0-9_]*", n))
    return names or DEFAULT_KEY_NAMES


def _lines_without_keys(env_file: Path, names: tuple[str, ...]) -> list[str]:
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [line for line in lines if line.split("=", 1)[0].strip() not in names]


def configured(env_file: Path, names: tuple[str, ...] = DEFAULT_KEY_NAMES) -> bool:
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, PermissionError):
        return False
    return any(line.split("=", 1)[0].strip() == names[0] and line.split("=", 1)[1].strip()
               for line in lines if "=" in line)


def save(env_file: Path, key: str, names: tuple[str, ...] = DEFAULT_KEY_NAMES) -> None:
    key = (key or "").strip()
    if not _KEY_RE.match(key):
        raise ValueError("that does not look like an API key (8–512 letters, digits, '.', '_', ':' or '-')")
    lines = _lines_without_keys(env_file, names) + [f"{name}={key}" for name in names]
    _write(env_file, lines)


def clear(env_file: Path, names: tuple[str, ...] = DEFAULT_KEY_NAMES) -> None:
    _write(env_file, _lines_without_keys(env_file, names))


def _write(env_file: Path, lines: list[str]) -> None:
    env_file.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(env_file, ("\n".join(lines) + ("\n" if lines else "")).encode())
    os.chmod(env_file, 0o600)
