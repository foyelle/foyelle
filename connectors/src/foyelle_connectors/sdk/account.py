"""One connected account, kept in the connector's own state directory.

The settings (servers, address, folders) are a JSON file; the password is a separate file. Both
are 0600 in a directory only this connector's service user can open (systemd DynamicUser with a
0700 StateDirectory), on the encrypted disk. The password never leaves this process except to
log in to the account's own servers: it is not returned by any tool and not logged.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .fsutil import atomic_write

SETTINGS = "account.json"
SECRET = "account.secret"


class AccountStore:
    def __init__(self, directory: Path) -> None:
        self.dir = directory

    def load(self) -> dict[str, Any] | None:
        try:
            data = json.loads((self.dir / SETTINGS).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except ValueError:
            return None  # a damaged file reads as "not connected" rather than a crash
        return data if isinstance(data, dict) else None

    def password(self) -> str:
        try:
            return (self.dir / SECRET).read_text(encoding="utf-8")
        except FileNotFoundError:
            raise LookupError("no password is stored for this account") from None

    def save(self, settings: dict[str, Any], password: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        old = os.umask(0o077)
        try:
            atomic_write(self.dir / SECRET, password.encode("utf-8"))
            atomic_write(self.dir / SETTINGS, (json.dumps(settings, indent=2, sort_keys=True) + "\n").encode())
        finally:
            os.umask(old)
        for name in (SECRET, SETTINGS):
            os.chmod(self.dir / name, 0o600)

    def remove(self) -> bool:
        removed = False
        for name in (SECRET, SETTINGS):
            try:
                (self.dir / name).unlink()
                removed = True
            except FileNotFoundError:
                pass
        return removed
