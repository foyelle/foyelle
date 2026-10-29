from __future__ import annotations

import json
from pathlib import Path

import pytest

from foyelle_connectors.shell.services import ServicesState


@pytest.fixture
def state(tmp_path: Path) -> ServicesState:
    system = tmp_path / "services-system.json"
    system.write_text(json.dumps({"available": {"kiwi": {"port": 7320}, "todoist": {"port": 7321}}, "always": ["kiwi"]}))
    return ServicesState(tmp_path / "shell" / "services.json", system)


def test_listing_shows_the_directory_and_what_is_connected(state: ServicesState) -> None:
    rows = {s["id"]: s for s in state.listing()["services"]}
    assert set(rows) == {"kiwi", "todoist"}
    assert rows["kiwi"]["connected"] is True and rows["kiwi"]["always"] is True  # on for development
    assert rows["todoist"] == {**rows["todoist"], "connected": False, "account": True, "private": True,
                               "state": "inactive"}


def test_connect_and_disconnect_only_record_the_choice(state: ServicesState) -> None:
    assert state.set("todoist", True) == {"id": "todoist", "connected": True}
    assert json.loads(state.connected_file.read_text()) == {"connected": ["todoist"]}
    rows = {s["id"]: s for s in state.listing(lambda sid: "activating")["services"]}
    assert rows["todoist"]["connected"] is True and rows["todoist"]["state"] == "activating"
    state.set("todoist", False)
    assert state.connected() == []


def test_only_directory_services_and_not_the_always_on(state: ServicesState) -> None:
    with pytest.raises(ValueError, match="not a service this computer can connect"):
        state.set("evil", True)
    with pytest.raises(ValueError, match="always on"):
        state.set("kiwi", False)


def test_a_hand_edited_file_is_read_safely(state: ServicesState) -> None:
    state.connected_file.parent.mkdir(parents=True, exist_ok=True)
    state.connected_file.write_text('{"connected": ["todoist", 3, null]}')
    assert state.connected() == ["todoist"]
    state.connected_file.write_text("not json")
    assert state.connected() == []


def test_without_a_system_file_every_entry_is_listed(tmp_path: Path) -> None:
    dev = ServicesState(tmp_path / "services.json", tmp_path / "missing.json")
    assert {s["id"] for s in dev.listing()["services"]} >= {"kiwi", "todoist"}


def test_conversation_services_use_the_plugin_format(tmp_path: Path) -> None:
    from foyelle_connectors.shell.services import ConversationServices

    conv = ConversationServices(tmp_path / "conversations")
    assert conv.get("20261026_1") == {"on": [], "off": []}
    conv.set("20261026_1", "todoist", True)
    conv.set("20261026_1", "kiwi", False)
    assert json.loads((tmp_path / "conversations" / "20261026_1.json").read_text()) == {"off": ["kiwi"], "on": ["todoist"]}
    assert conv.set("20261026_1", "todoist", False) == {"on": [], "off": ["kiwi", "todoist"]}
    with pytest.raises(ValueError, match="stored id"):
        conv.get("../../etc/passwd")


def test_services_added_by_url_are_listed_from_the_machine(tmp_path: Path) -> None:
    system = tmp_path / "system.json"
    system.write_text(json.dumps({"available": {
        "kiwi": {"port": 7320, "title": "Kiwi.com flights"},
        "notionnotes": {"port": 7340, "title": "Notion Notes", "category": "notes", "summary": "Added by you.",
                        "account": True, "private": True, "reviewed": False}}, "always": []}))
    state = ServicesState(tmp_path / "services.json", system)
    rows = {s["id"]: s for s in state.listing()["services"]}
    assert set(rows) == {"kiwi", "notionnotes"}  # todoist isn't built on this machine
    assert rows["notionnotes"]["reviewed"] is None and rows["notionnotes"]["account"] is True
    assert state.set("notionnotes", True)["connected"] is True
