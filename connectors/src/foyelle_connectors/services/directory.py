"""The Services directory: the services a person can connect, one reviewed JSON file each.

An entry says where the service's MCP server is, which hosts its helper may reach, how the
person signs in, and Foyelle's tier for each of its tools (docs/services-directory.md). Entries are
data reviewed in git and shipped read-only with Foyelle: NixOS builds every entry's helper and
egress proxy from them (nixos/modules/bridges.nix), the policy reads their tiers, and the
Services card lists them. ``python -m foyelle_connectors.services.directory`` checks them all.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DIRECTORY = Path(__file__).with_name("directory")
TIERS = ("read", "reversible", "outbound", "system")
CATEGORIES = ("tasks", "travel", "notes", "files", "fitness", "knowledge", "home", "other")
# Foyelle's cards for services' results (connectors/…/ui/views/<service>_<view>.html).
VIEWS = ("flights", "tasks")
# A slot gives the helper its port (7320 + slot) and its egress proxy 127.0.1.<20 + slot>. Custom
# connectors the agent writes use ports 7400-7499 and indexes 40-99 (system/guides/connector.md);
# services added by URL (custom entries) use their own slots: port 7340 + slot, index 110 + slot.
MAX_SLOT = 19
BASE_PORT = 7320
BASE_INDEX = 20
CUSTOM_MAX_SLOT = 19
CUSTOM_BASE_PORT = 7340
CUSTOM_BASE_INDEX = 110

_ID = re.compile(r"^[a-z][a-z0-9]{1,30}$")
_HOST = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_TOOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class DirectoryError(ValueError):
    pass


def _https(url: Any, what: str) -> str:
    if not isinstance(url, str):
        raise DirectoryError(f"{what} must be a string")
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname or u.username or u.password or u.fragment:
        raise DirectoryError(f"{what} must be an https URL without credentials or fragment: {url!r}")
    return u.hostname.lower()


def _hosts(value: Any, what: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(h, str) and _HOST.match(h) for h in value):
        raise DirectoryError(f"{what} must be a non-empty list of host names")
    return value


def validate(entry: Any, *, custom: bool = False) -> dict[str, Any]:
    """A checked copy of one entry, or DirectoryError saying what to fix.

    ``custom``: a service the person added by URL (the config repo's services.json): nobody reviewed
    it, so ``reviewed`` is null, its tools follow their hints, and it may list no tools at all."""
    if not isinstance(entry, dict):
        raise DirectoryError("an entry must be a JSON object")
    eid = entry.get("id")
    if not isinstance(eid, str) or not _ID.match(eid):
        raise DirectoryError(f"id must be 2–31 lowercase letters and digits, starting with a letter: {eid!r}")
    where = f"{eid}: "
    try:
        slot = entry.get("slot")
        top = CUSTOM_MAX_SLOT if custom else MAX_SLOT
        if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot <= top:
            raise DirectoryError(f"slot must be a whole number from 0 to {top}")
        for key, limit in (("title", 40), ("summary", 160)):
            v = entry.get(key)
            if not isinstance(v, str) or not v.strip() or len(v) > limit or any(ord(c) < 32 for c in v):
                raise DirectoryError(f"{key} must be 1 to {limit} characters")
        if entry.get("category") not in CATEGORIES:
            raise DirectoryError(f"category must be one of {', '.join(CATEGORIES)}")
        hosts = _hosts(entry.get("hosts"), "hosts")
        if _https(entry.get("url"), "url") not in hosts:
            raise DirectoryError("hosts must include the url's host")
        auth = entry.get("auth")
        if not isinstance(auth, dict) or auth.get("kind") not in ("none", "oauth"):
            raise DirectoryError('auth.kind must be "none" or "oauth"')
        if auth["kind"] == "oauth":
            signin_hosts = _hosts(auth.get("signin_hosts"), "auth.signin_hosts")
            if _https(auth.get("signin_page"), "auth.signin_page") not in signin_hosts:
                raise DirectoryError("auth.signin_hosts must include the sign-in page's host")
        elif set(auth) - {"kind"}:
            raise DirectoryError('auth with kind "none" takes nothing else')
        if entry.get("sensitivity") not in ("private", "public"):
            raise DirectoryError('sensitivity must be "private" (it holds the person\'s data) or "public"')
        tools = entry.get("tools")
        if not isinstance(tools, dict) or (not tools and not custom):
            raise DirectoryError("tools must list the service's tools")
        shown = 0
        for name, rule in tools.items():
            if not _TOOL.match(name) or not isinstance(rule, dict):
                raise DirectoryError(f"tool {name!r}: a name and an object are needed")
            if rule.get("hidden") is True:
                if set(rule) - {"hidden"}:
                    raise DirectoryError(f"tool {name!r}: a hidden tool takes nothing else")
                continue
            if set(rule) - {"tier", "egress"} or rule.get("tier") not in TIERS:
                raise DirectoryError(f"tool {name!r}: tier must be one of {', '.join(TIERS)} (and egress true/false)")
            if not isinstance(rule.get("egress", False), bool):
                raise DirectoryError(f"tool {name!r}: egress must be true or false")
            shown += 1
        if not shown and not custom:
            raise DirectoryError("at least one tool must be shown")
        views = entry.get("views", {})
        if not isinstance(views, dict) or any(
                t not in tools or tools[t].get("hidden") or v not in VIEWS for t, v in views.items()):
            raise DirectoryError(f"views map shown tools to one of Foyelle's cards ({', '.join(VIEWS)})")
        if entry.get("new_tools", "review") not in ("review", "by_hint"):
            raise DirectoryError('new_tools must be "review" (unlisted tools stay hidden) or "by_hint"')
        reviewed = entry.get("reviewed")
        if reviewed is not None and not (isinstance(reviewed, str) and _DATE.match(reviewed)):
            raise DirectoryError("reviewed must be a date (YYYY-MM-DD), or null for a service nobody reviewed")
        if custom and (reviewed is not None or entry.get("new_tools") != "by_hint"):
            raise DirectoryError('a service added by URL has "reviewed": null and "new_tools": "by_hint"')
        if not isinstance(entry.get("notes", ""), str):
            raise DirectoryError("notes must be text")
        known = {"id", "slot", "title", "category", "summary", "url", "hosts", "auth", "sensitivity", "tools",
                 "views", "new_tools", "reviewed", "notes"}
        if set(entry) - known:
            raise DirectoryError(f"unknown fields: {', '.join(sorted(set(entry) - known))}")
    except DirectoryError as exc:
        raise DirectoryError(where + str(exc)) from None
    return {**entry, "views": views, "new_tools": entry.get("new_tools", "review"), "notes": entry.get("notes", ""),
            "reviewed": reviewed}


def load(directory: Path = DIRECTORY) -> dict[str, dict[str, Any]]:
    """Every entry, checked, by id. Ids, slots and file names must agree and be unique."""
    entries: dict[str, dict[str, Any]] = {}
    slots: dict[int, str] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            entry = validate(json.loads(path.read_text(encoding="utf-8")))
        except ValueError as exc:  # json.JSONDecodeError is a ValueError
            raise DirectoryError(f"{path.name}: {exc}") from None
        if path.stem != entry["id"]:
            raise DirectoryError(f"{path.name}: the file must be named {entry['id']}.json")
        if entry["slot"] in slots:
            raise DirectoryError(f"{path.name}: slot {entry['slot']} is taken by {slots[entry['slot']]}")
        slots[entry["slot"]] = entry["id"]
        entries[entry["id"]] = entry
    return entries


def port(entry: dict[str, Any], *, custom: bool = False) -> int:
    return (CUSTOM_BASE_PORT if custom else BASE_PORT) + entry["slot"]


def slug(title: str) -> str:
    """An id from a service's name: lowercase letters and digits ("Notion Notes" -> "notionnotes")."""
    s = re.sub(r"[^a-z0-9]", "", title.lower())
    if not s or not s[0].isalpha():
        s = "service" + s
    return s[:31]


def custom_entry(*, title: str, url: str, hosts: list[str], slot: int, auth: str = "none",
                 signin_page: str | None = None, signin_hosts: list[str] | None = None,
                 private: bool = True, summary: str | None = None, category: str = "other",
                 service_id: str | None = None) -> dict[str, Any]:
    """A checked services.json entry for a service the person adds by its MCP URL."""
    entry: dict[str, Any] = {
        "id": service_id or slug(title), "slot": slot, "title": title.strip(), "category": category,
        "summary": (summary or f"Added by you: {title.strip()}.").strip(), "url": url, "hosts": hosts,
        "auth": ({"kind": "oauth", "signin_page": signin_page, "signin_hosts": signin_hosts or []}
                 if auth == "oauth" else {"kind": "none"}),
        "sensitivity": "private" if private else "public", "tools": {}, "views": {}, "new_tools": "by_hint",
        "reviewed": None, "notes": "Added by the person by URL; tools take their tiers from their own hints.",
    }
    return validate(entry, custom=True)


def shown_tools(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {name: rule for name, rule in entry["tools"].items() if not rule.get("hidden")}


def main() -> int:
    try:
        entries = load(Path(sys.argv[1]) if len(sys.argv) > 1 else DIRECTORY)
    except DirectoryError as exc:
        print(f"services directory: {exc}", file=sys.stderr)
        return 1
    print(f"services directory: {len(entries)} entries ok ({', '.join(entries)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
