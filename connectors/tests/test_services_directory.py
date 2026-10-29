from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from foyelle_connectors.services import directory as d


def test_the_shipped_directory_is_valid() -> None:
    entries = d.load()
    assert {"kiwi", "todoist"} <= set(entries)
    assert d.port(entries["kiwi"]) == 7320 and d.port(entries["todoist"]) == 7321
    assert "feedback-to-devs" not in d.shown_tools(entries["kiwi"])


GOOD = {
    "id": "notes", "slot": 5, "title": "Notes", "category": "notes", "summary": "Your notes.",
    "url": "https://mcp.notes.example/mcp", "hosts": ["mcp.notes.example"],
    "auth": {"kind": "oauth", "signin_page": "https://notes.example/authorize", "signin_hosts": ["notes.example"]},
    "sensitivity": "private", "tools": {"search": {"tier": "read"}, "wipe": {"hidden": True}},
    "reviewed": "2026-10-26",
}


def broken(**changes):
    e = copy.deepcopy(GOOD)
    for key, value in changes.items():
        if value is None:
            e.pop(key, None)
        else:
            e[key] = value
    return e


@pytest.mark.parametrize("entry, message", [
    (broken(id="Notes!"), "id must be"),
    (broken(slot=99), "slot must be"),
    (broken(url="http://mcp.notes.example"), "https URL"),
    (broken(url="https://user:pw@mcp.notes.example"), "without credentials"),
    (broken(hosts=["other.example"]), "include the url's host"),
    (broken(hosts=["*"]), "list of host names"),
    (broken(auth={"kind": "oauth", "signin_page": "https://evil.example/a", "signin_hosts": ["notes.example"]}),
     "sign-in page's host"),
    (broken(auth={"kind": "magic"}), "auth.kind"),
    (broken(sensitivity="secret"), "sensitivity"),
    (broken(tools={"search": {"tier": "god"}}), "tier must be"),
    (broken(tools={"wipe": {"hidden": True}}), "at least one tool"),
    (broken(tools={"wipe": {"hidden": True, "tier": "read"}}), "hidden tool takes nothing else"),
    (broken(views={"wipe": "tasks"}), "views map shown tools"),
    (broken(views={"search": "dashboard"}), "views map shown tools"),
    (broken(new_tools="always"), "new_tools"),
    (broken(reviewed="yesterday"), "reviewed must be a date"),
    (broken(extra=1), "unknown fields"),
    (broken(category=None), "category"),
])
def test_bad_entries_are_refused_with_a_reason(entry, message) -> None:
    with pytest.raises(d.DirectoryError, match=message):
        d.validate(entry)


def test_unreviewed_entries_are_allowed() -> None:
    assert d.validate(broken(reviewed=None, new_tools="by_hint"))["reviewed"] is None


def test_files_ids_and_slots_must_agree(tmp_path: Path) -> None:
    (tmp_path / "notes.json").write_text(json.dumps(GOOD))
    assert set(d.load(tmp_path)) == {"notes"}
    (tmp_path / "other.json").write_text(json.dumps({**GOOD, "id": "other"}))
    with pytest.raises(d.DirectoryError, match="slot 5 is taken by notes"):
        d.load(tmp_path)
    (tmp_path / "other.json").write_text(json.dumps({**GOOD, "id": "else", "slot": 6}))
    with pytest.raises(d.DirectoryError, match="must be named else.json"):
        d.load(tmp_path)
