"""Read-only settings from the Nix-generated JSON, plus a small set of user overrides."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..sdk.fsutil import atomic_write


@dataclass(frozen=True)
class Setting:
    kind: str  # "enum" | "string" | "number" | "bool"
    description: str
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    max_length: int = 200

    def validate(self, key: str, value: Any) -> Any:
        match self.kind:
            case "enum":
                if value not in self.choices:
                    raise ValueError(f"{key} must be one of {', '.join(self.choices)}")
            case "string":
                if not isinstance(value, str) or not value.strip() or len(value) > self.max_length:
                    raise ValueError(f"{key} must be a non-empty string of at most {self.max_length} characters")
                if any(ord(c) < 32 for c in value):
                    raise ValueError(f"{key} must not contain control characters")
                value = value.strip()
            case "number":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(f"{key} must be a number")
                if not (self.minimum <= value <= self.maximum):  # type: ignore[operator]
                    raise ValueError(f"{key} must be between {self.minimum} and {self.maximum}")
            case "bool":
                if not isinstance(value, bool):
                    raise ValueError(f"{key} must be true or false")
        return value

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "description": self.description}
        if self.choices:
            out["choices"] = list(self.choices)
        if self.kind == "number":
            out["minimum"], out["maximum"] = self.minimum, self.maximum
        return out


# The only keys settings_set may change. Everything else is Nix-managed.
ALLOWED: dict[str, Setting] = {
    "ui.theme": Setting("enum", "Light or dark (time: light by day, dark in the evening)",
                        choices=("system", "light", "dark", "time")),
    "ui.style": Setting("string", "The canvas theme's id (see theme_list; theme_use changes it)", max_length=40),
    "ui.text_scale": Setting("number", "Canvas text size multiplier", minimum=0.75, maximum=2.0),
    "ui.locale": Setting("string", "Locale for dates and numbers, e.g. en-GB", max_length=35),
    "ui.natural_scroll": Setting("bool", "Natural scrolling, like on a Mac (the screen restarts to apply it)"),
    "ui.welcomed": Setting("bool", "The first-start welcome is done"),
    "user.name": Setting("string", "What Foyelle calls the user", max_length=60),
    "model.route": Setting("enum", "Default model route", choices=("local", "remote")),
    "model.main": Setting("string", "Main model name on the remote route"),
    "model.local": Setting("string", "Model name on the local route (as the local server lists it, e.g. qwen3:4b-instruct)"),
    "model.auxiliary": Setting("string", "Auxiliary model name on the remote route"),
}


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _set_dotted(target: dict[str, Any], key: str, value: Any) -> None:
    *parents, leaf = key.split(".")
    for part in parents:
        node = target.get(part)
        if not isinstance(node, dict):
            node = target[part] = {}
        target = node
    target[leaf] = value


def _flatten(overrides: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for k, v in overrides.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            flat.update(_flatten(v, key + "."))
        else:
            flat[key] = v
    return flat


class Settings:
    def __init__(self, settings_file: Path, overrides_file: Path) -> None:
        self.settings_file = settings_file
        self.overrides_file = overrides_file

    def overrides(self) -> dict[str, Any]:
        """Flat ``{dotted.key: value}`` overrides, keeping only allowlisted keys."""
        flat = _flatten(_load(self.overrides_file))
        return {k: v for k, v in flat.items() if k in ALLOWED}

    def effective(self) -> dict[str, Any]:
        merged = copy.deepcopy(_load(self.settings_file))
        for key, value in self.overrides().items():
            _set_dotted(merged, key, value)
        return merged

    def get(self) -> dict[str, Any]:
        return {
            "settings_file": str(self.settings_file),
            "overrides_file": str(self.overrides_file),
            "effective": self.effective(),
            "overrides": self.overrides(),
            "allowed": {k: s.describe() for k, s in ALLOWED.items()},
        }

    def set(self, key: str, value: Any) -> dict[str, Any]:
        """Set (or with ``None``, clear) one allowlisted override."""
        spec = ALLOWED.get(key)
        if spec is None:
            raise ValueError(f"{key!r} cannot be changed here; allowed keys: {', '.join(ALLOWED)}")
        flat = self.overrides()
        previous = flat.get(key)
        if value is None:
            flat.pop(key, None)
        else:
            flat[key] = spec.validate(key, value)
        nested: dict[str, Any] = {}
        for k, v in sorted(flat.items()):
            _set_dotted(nested, k, v)
        self.overrides_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.overrides_file, (json.dumps(nested, indent=2, sort_keys=True) + "\n").encode())
        return {"key": key, "value": flat.get(key), "previous": previous, "overrides": flat}
