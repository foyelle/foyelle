"""Environment and credential helpers.

Configuration reaches a connector through environment variables; secrets
through ``$CREDENTIALS_DIRECTORY/<name>`` (systemd ``LoadCredential=``).
"""

from __future__ import annotations

import os
from pathlib import Path


class ConfigError(RuntimeError):
    """A required setting or credential is missing or malformed."""


def env(name: str, default: str | None = None) -> str | None:
    """Return the variable, treating an empty value as unset."""
    value = os.environ.get(name)
    return value if value else default


def env_required(name: str) -> str:
    value = env(name)
    if value is None:
        raise ConfigError(f"{name} is not set")
    return value


def env_int(name: str, default: int) -> int:
    value = env(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {value!r}") from exc


def env_path(name: str, default: str) -> Path:
    return Path(env(name, default) or default)


def read_credential(name: str) -> str | None:
    """Read ``$CREDENTIALS_DIRECTORY/<name>``; ``None`` if absent.

    A single trailing newline is stripped, as editors and ``echo`` add one.
    """
    directory = env("CREDENTIALS_DIRECTORY")
    if directory is None:
        return None
    path = Path(directory) / name
    try:
        value = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return value.removesuffix("\n").removesuffix("\r")


def require_credential(name: str) -> str:
    value = read_credential(name)
    if not value:
        raise ConfigError(f"credential {name!r} not found in $CREDENTIALS_DIRECTORY")
    return value
