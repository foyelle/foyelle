from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import hints, mcp_session, serving

from foyelle_connectors.shell import bots
from foyelle_connectors.shell.server import ShellConfig, build_server

GIVABLE = [{"id": "calendar", "title": "Calendar", "private": True},
           {"id": "web", "title": "Web search", "private": False},
           {"id": "todoist", "title": "Todoist", "private": True}]


@pytest.fixture
def roster(tmp_path: Path) -> bots.BotStore:
    return bots.BotStore(tmp_path / "bots.json")


def test_what_a_bot_can_be_given() -> None:
    services = [{"id": "todoist", "title": "Todoist", "private": True, "connected": True},
                {"id": "kiwi", "title": "Kiwi.com", "private": False, "connected": False}]
    out = bots.givable({"calendar", "web", "system", "workshop", "shell"}, services)
    assert [g["id"] for g in out] == ["calendar", "web", "todoist"]
    assert out[0] == {"id": "calendar", "title": "Calendar", "private": True}


@pytest.mark.parametrize(("name", "expected"), [("Travel", "bot-travel"), ("Réservations & vols", "bot-reservations-vols"),
                                                ("Home helper!", "bot-home-helper")])
def test_a_bot_id_is_a_profile_name(name: str, expected: str) -> None:
    assert bots.slug(name) == expected


def test_a_name_in_another_script_still_gets_an_id() -> None:
    first, second = bots.slug("旅行"), bots.slug("مساعد")
    assert first.startswith("bot-") and second.startswith("bot-") and first != second


def test_save_keeps_only_what_it_can_be_given(roster: bots.BotStore) -> None:
    bot = roster.save(GIVABLE, name="  Travel  helper ", job="Watches flight prices.", picture="✈️",
                      services=["calendar", "web"], share_online=["calendar", "web", "todoist"])
    assert bot["id"] == "bot-travel-helper" and bot["name"] == "Travel helper"
    assert bot["services"] == ["calendar", "web"]
    assert bot["shareOnline"] == ["calendar"]  # private, and given: web is public, todoist not given
    saved = json.loads(roster.path.read_text())
    assert saved["bots"][0]["id"] == "bot-travel-helper"
    with pytest.raises(ValueError, match="not something a bot can use"):
        roster.save(GIVABLE, name="Sneaky", services=["system"])


def test_ids_stay_unique_and_edits_keep_the_id(roster: bots.BotStore) -> None:
    a = roster.save(GIVABLE, name="Home")
    b = roster.save(GIVABLE, name="Home")
    assert (a["id"], b["id"]) == ("bot-home", "bot-home-2")
    edited = roster.save(GIVABLE, name="Household", services=["todoist"], bot_id="bot-home")
    assert edited["id"] == "bot-home" and edited["name"] == "Household"
    with pytest.raises(ValueError, match="no bot"):
        roster.save(GIVABLE, name="x", bot_id="default")


def test_foyelle_is_not_a_bot_but_has_its_sharing(roster: bots.BotStore) -> None:
    roster.save(GIVABLE, name="Home", services=["todoist"])
    assert roster.share_for_foyelle(GIVABLE, ["todoist", "web"]) == {"shareOnline": ["todoist"]}
    listing = roster.listing(GIVABLE)
    assert [b["id"] for b in listing["bots"]] == ["bot-home"]
    assert listing["foyelle"] == {"shareOnline": ["todoist"]}
    assert json.loads(roster.path.read_text())["bots"][0] == {"id": "default", "shareOnline": ["todoist"]}
    with pytest.raises(ValueError):
        roster.delete("default")


def test_delete_and_limits(roster: bots.BotStore) -> None:
    roster.save(GIVABLE, name="Home")
    assert roster.delete("bot-home") == {"deleted": "bot-home"}
    assert roster.listing(GIVABLE)["bots"] == []
    with pytest.raises(ValueError):
        roster.save(GIVABLE, name="")
    with pytest.raises(ValueError):
        roster.save(GIVABLE, name="x" * 41)


def test_waiting_items_expire_and_drop(tmp_path: Path) -> None:
    store = bots.WaitingStore(tmp_path / "waiting")
    (tmp_path / "waiting").mkdir()
    for item_id, created, expires in (("a" * 16, 10, 1000), ("b" * 16, 20, 1000), ("c" * 16, 5, 50)):
        (tmp_path / "waiting" / f"{item_id}.json").write_text(json.dumps(
            {"id": item_id, "created": created, "expires": expires, "summary": item_id}))
    assert [i["id"] for i in store.listing(now=100)["items"]] == ["b" * 16, "a" * 16]
    assert not (tmp_path / "waiting" / f"{'c' * 16}.json").exists()
    assert store.drop("a" * 16) == {"dropped": "a" * 16}
    with pytest.raises(ValueError):
        store.drop("a" * 16)
    with pytest.raises(ValueError):
        store.drop("../bots")


@pytest.fixture
def config(tmp_path: Path) -> ShellConfig:
    (tmp_path / "memories").mkdir()
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({}))
    return ShellConfig(tmp_path / "memories", settings_file, tmp_path / "shell" / "settings.json",
                       tmp_path / "audit.jsonl", {"calendar": 7302, "web": 7305, "system": 7303}, "none")


async def test_bot_tools_over_mcp(config: ShellConfig) -> None:
    with serving(build_server(config)) as url:
        async with mcp_session(url) as session:
            tools = {t.name: t for t in (await session.list_tools()).tools}
            assert hints(tools["bots_list"])[0] is True
            assert hints(tools["bot_delete"])[:2] == (False, True)
            made = await session.call_tool("bot_save", {"name": "Travel", "job": "Flights", "services": ["web"]})
            assert made.structuredContent["id"] == "bot-travel"
            listed = (await session.call_tool("bots_list", {})).structuredContent
            assert [b["id"] for b in listed["bots"]] == ["bot-travel"]
            assert [g["id"] for g in listed["givable"]] == ["calendar", "web"]
            assert (await session.call_tool("waiting_list", {})).structuredContent == {"items": []}


def test_routine_results_from_foyelle_and_every_bot(tmp_path: Path) -> None:
    from foyelle_connectors.shell import routines

    def run(home: Path, job: str, stem: str, text: str, mtime: float) -> None:
        path = home / "cron" / "output" / job / f"{stem}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        import os
        os.utime(path, (mtime, mtime))

    response = "Three events today: dentist at 10."
    doc = (f"# Cron Job: Morning brief\n\n**Job ID:** ab12\n**Run Time:** 2026-10-27 07:30:00\n**Schedule:** 30 7 * * *\n\n"
           f"## Prompt\n\nWhat's on today?\n\n**Response Characters:** {len(response)}\n## Response\n\n{response}\n")
    run(tmp_path, "ab12", "2026-10-27_07-30-00", doc, 200)
    run(tmp_path / "profiles" / "bot-travel", "cd34", "2026-10-27_08-00-00",
        "# Cron Job: cd34 (FAILED)\n\nThe model was unreachable.\n", 300)
    run(tmp_path, "ab12", "2026-10-26_07-30-00", doc, 50)
    out = routines.results(tmp_path, since=100)["runs"]
    assert [(r["bot"], r["name"], r["failed"]) for r in out] == [("bot-travel", "cd34", True),
                                                                 ("default", "Morning brief", False)]
    assert out[1]["response"] == response and out[0]["response"] == "The model was unreachable."
