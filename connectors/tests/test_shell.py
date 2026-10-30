from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import check_surface, hints, mcp_session, serving

from foyelle_connectors.shell import audit, memory, privacy, settings, status
from foyelle_connectors.shell.server import ShellConfig, build_server


@pytest.fixture
def config(tmp_path: Path) -> ShellConfig:
    mem = tmp_path / "memories"
    mem.mkdir()
    (mem / "MEMORY.md").write_text("Prefers metric units\n§\nWorks on Foyelle\n")
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"model": {"route": "remote", "main": "zen-free"}, "ui": {"theme": "system"}}))
    log = tmp_path / "audit.jsonl"
    rows = [
        {"ts": f"2026-10-18T10:00:0{i}Z", "session_id": "s1" if i % 2 else "s2", "tool": f"mcp_files_t{i}",
         "connector": "files", "decision": "allow" if i < 3 else "ask", "origin": "model"}
        for i in range(6)
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows[:3]) + "\nnot json\n" + "\n".join(json.dumps(r) for r in rows[3:]) + "\n")
    return ShellConfig(mem, settings_file, tmp_path / "overrides.json", log, {"shell": 7300, "files": 7301}, "none")


def test_memory_read_write_backup(config: ShellConfig) -> None:
    store = memory.MemoryStore(config.memory_dir)
    files = {f["name"]: f for f in store.read()["files"]}
    assert files["MEMORY.md"]["entries"] == ["Prefers metric units", "Works on Foyelle"]
    assert files["USER.md"]["exists"] is False
    sha = files["MEMORY.md"]["sha256"]
    with pytest.raises(ValueError, match="changed since"):
        store.write("MEMORY.md", "x", expected_sha256="0" * 64)
    out = store.write("MEMORY.md", "Works on Foyelle\n", expected_sha256=sha)
    assert out["backup"] and (config.memory_dir / ".foyelle-backup" / out["backup"]).read_text().startswith("Prefers")
    assert store.read("MEMORY.md")["files"][0]["entries"] == ["Works on Foyelle"]
    with pytest.raises(ValueError):
        store.write("../etc/passwd", "x")
    export = store.export()
    assert "## MEMORY.md" in export["content"] and "## USER.md" in export["content"]


def test_settings_allowlist_and_merge(config: ShellConfig) -> None:
    prefs = settings.Settings(config.settings_file, config.overrides_file)
    with pytest.raises(ValueError, match="cannot be changed"):
        prefs.set("model.base_url", "http://evil")
    with pytest.raises(ValueError, match="one of"):
        prefs.set("ui.theme", "purple")
    with pytest.raises(ValueError, match="between"):
        prefs.set("ui.text_scale", 9)
    prefs.set("ui.theme", "dark")
    prefs.set("ui.text_scale", 1.25)
    assert json.loads(config.overrides_file.read_text()) == {"ui": {"text_scale": 1.25, "theme": "dark"}}
    eff = prefs.effective()
    assert eff["ui"]["theme"] == "dark" and eff["model"]["main"] == "zen-free"
    prefs.set("ui.theme", None)
    assert prefs.effective()["ui"]["theme"] == "system"
    # Hand-edited overrides outside the allowlist are ignored.
    config.overrides_file.write_text(json.dumps({"model": {"base_url": "http://evil"}}))
    assert "base_url" not in prefs.effective()["model"]


def test_audit_newest_first_with_filters(config: ShellConfig) -> None:
    res = audit.query(config.audit_log)
    assert [e["tool"] for e in res["entries"]] == [f"mcp_files_t{i}" for i in range(5, -1, -1)]
    assert res["skipped_malformed"] == 1
    assert [e["tool"] for e in audit.query(config.audit_log, session_id="s1")["entries"]] == [
        "mcp_files_t5", "mcp_files_t3", "mcp_files_t1"]
    assert len(audit.query(config.audit_log, decision="ask")["entries"]) == 3
    page = audit.query(config.audit_log, limit=2, offset=2)
    assert [e["tool"] for e in page["entries"]] == ["mcp_files_t3", "mcp_files_t2"] and page["has_more"]
    assert audit.query(config.audit_log, tool="t4")["entries"][0]["tool"] == "mcp_files_t4"
    assert audit.query(config.audit_log.with_name("missing.jsonl"))["exists"] is False


def test_reverse_reader_spans_blocks(tmp_path: Path) -> None:
    path = tmp_path / "big.jsonl"
    path.write_text("".join(json.dumps({"n": i, "pad": "x" * 500}) + "\n" for i in range(400)))
    assert [json.loads(line)["n"] for line in audit.iter_lines_reversed(path)] == list(range(399, -1, -1))


def test_status_helpers(tmp_path: Path) -> None:
    assert status.parse_connectors("shell,files=9999") == {"shell": 7300, "files": 9999}
    net = tmp_path / "net"
    (net / "eth0").mkdir(parents=True)
    (net / "eth0" / "operstate").write_text("up\n")
    (net / "eth0" / "carrier").write_text("1\n")
    (net / "lo").mkdir()
    route = tmp_path / "route"
    route.write_text("Iface\tDestination\tGateway\neth0\t00000000\t0102A8C0\n")
    assert status.network_status(net, route) == {
        "online": True, "default_route": True, "interfaces": [{"name": "eth0", "state": "up", "carrier": True}]}
    health = status.connector_health({"files": 1}, "tcp", "", timeout=0.2)
    assert health == [{"name": "files", "port": 1, "state": "unreachable"}]


def test_user_name_goes_to_settings_and_memory(config: ShellConfig) -> None:
    store = memory.MemoryStore(config.memory_dir)
    (config.memory_dir / "USER.md").write_text("The user's name is Sam\n§\nLikes tea.\n")
    store.set_name("Sam")
    assert (config.memory_dir / "USER.md").read_text() == "User's name is Sam.\n§\nLikes tea.\n"
    store.set_name("Mike")
    assert memory.split_entries((config.memory_dir / "USER.md").read_text())[:2] == ["User's name is Mike.", "Likes tea."]
    prefs = settings.Settings(config.settings_file, config.overrides_file)
    with pytest.raises(ValueError):
        prefs.set("user.name", "x" * 61)
    assert prefs.set("ui.theme", "time")["value"] == "time"
    assert prefs.set("ui.welcomed", True)["value"] is True


def test_privacy_today_counts_what_left(tmp_path: Path) -> None:
    from datetime import datetime, timedelta, timezone

    # 15:00 local time today, whatever the zone: the entries 1-5 hours back are today, 20 back is not.
    now = datetime.now().astimezone().replace(hour=15, minute=0, second=0, microsecond=0)

    def at(hours_ago: float) -> str:
        return (now - timedelta(hours=hours_ago)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    rows = [
        # Yesterday: not counted.
        {"ts": at(20), "connector": "email", "tool": "mcp__email__send", "decision": "ask", "approval": "approved"},
        {"ts": at(5), "connector": "files", "tool": "mcp__files__list", "decision": "allow", "reason": "read tier"},
        {"ts": at(4), "connector": "web", "tool": "mcp__web__search", "decision": "allow", "status": "ok"},
        {"ts": at(3), "connector": "web", "tool": "mcp__web__search", "decision": "allow", "status": "ok"},
        {"ts": at(3), "connector": "web", "tool": "mcp__web__fetch", "decision": "allow", "status": "error"},
        {"ts": at(2), "connector": "email", "tool": "mcp__email__send", "decision": "ask", "approval": "approved",
         "status": "ok"},
        {"ts": at(2), "connector": "email", "tool": "mcp__email__send", "decision": "ask", "approval": "denied"},
        {"ts": at(1), "connector": "files", "tool": "mcp__files__read", "decision": "ask", "approval": "approved",
         "reason": "send files content to a remote model for this session", "status": "ok"},
        {"ts": at(1), "connector": "shell", "tool": "mcp__shell__memory_read", "decision": "ask", "approval": "denied",
         "reason": "send shell content to a remote model for this session"},
    ]
    log = tmp_path / "audit.jsonl"
    log.write_text("".join(json.dumps(r) + "\n" for r in rows) + "not json\n")
    d = privacy.today(log, model={"route": "remote", "main": "m", "provider_label": "OpenRouter"}, now=now)
    assert {o["key"]: o["count"] for o in d["went_out"]} == {"web.search": 2, "email.send": 1}
    assert "web.fetch" not in {o["key"] for o in d["went_out"]}  # it failed: nothing went out
    assert [s["label"] for s in d["shared"]] == ["files"]
    labels = [e["label"] for e in d["held_back"]]
    assert "An email (you said not now)" in labels
    assert "Your settings and memories, kept from the online model" in labels
    assert d["model"] == {"online": True, "provider": "OpenRouter", "name": "m"}
    assert privacy.today(log, model={"route": "local", "local": "q"}, now=now)["model"]["online"] is False
    assert privacy.today(tmp_path / "none.jsonl")["went_out"] == []
    assert privacy.parse_outbound("a.b, c.d") == {"a.b", "c.d"}
    assert privacy.parse_outbound("") == privacy.DEFAULT_OUTBOUND


async def test_end_to_end(config: ShellConfig) -> None:
    with serving(build_server(config)) as url:
        async with mcp_session(url) as session:
            tools = await check_surface(session, "status", "ui://shell/status")
            await check_surface(session, "memory_read", "ui://shell/memory")
            await check_surface(session, "settings_get", "ui://shell/settings")
            await check_surface(session, "audit_query", "ui://shell/audit")
            await check_surface(session, "privacy_today", "ui://shell/privacy")
            assert hints(tools["privacy_today"]) == (True, False, False)
            result = await session.call_tool("user_name_set", {"name": "  Ada "})
            assert result.structuredContent == {"name": "Ada"}
            assert hints(tools["audit_query"]) == (True, False, False)
            assert hints(tools["memory_write"]) == (False, True, False)
            assert hints(tools["settings_set"]) == (False, True, False)
            result = await session.call_tool("status", {})
            assert not result.isError
            assert result.structuredContent["model"] == {"route": "remote", "main": "zen-free"}
            result = await session.call_tool("settings_set", {"key": "model.route", "value": "local"})
            assert result.structuredContent["value"] == "local"
            result = await session.call_tool("settings_set", {"key": "nope", "value": 1})
            assert result.isError
            result = await session.call_tool("audit_query", {"decision": "ask", "limit": 1})
            assert result.structuredContent["entries"][0]["tool"] == "mcp_files_t5"
            # The canvas opens the card gallery (or Accounts) when these finish; they only look.
            assert hints(tools["open_cards"]) == (True, False, False) and hints(tools["open_accounts"]) == (True, False, False)
            assert (await session.call_tool("open_cards", {})).structuredContent == {"opened": True}


def test_model_key_is_saved_privately_and_never_returned(tmp_path):
    import stat

    from foyelle_connectors.shell import model_key

    env = tmp_path / "harness" / ".env"
    env.parent.mkdir()
    env.write_text("OTHER=1\nOPENCODE_ZEN_API_KEY=old\n")
    assert model_key.configured(env)
    model_key.save(env, "  sk-test_KEY.123  ")
    text = env.read_text()
    assert "OTHER=1" in text and "old" not in text
    assert "OPENCODE_ZEN_API_KEY=sk-test_KEY.123" in text and "OPENCODE_GO_API_KEY=sk-test_KEY.123" in text
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    model_key.clear(env)
    assert env.read_text() == "OTHER=1\n" and not model_key.configured(env)


def test_model_key_rejects_junk(tmp_path):
    import pytest

    from foyelle_connectors.shell import model_key

    for bad in ("", "short", "has space in it", "line\nbreak-injection=1"):
        with pytest.raises(ValueError):
            model_key.save(tmp_path / ".env", bad)


def test_model_key_follows_the_configured_provider(tmp_path: Path) -> None:
    from foyelle_connectors.shell import model_key

    env = tmp_path / ".env"
    env.write_text("OPENCODE_ZEN_API_KEY=old\nOTHER=1\n")
    names = model_key.key_names("OPENROUTER_API_KEY")
    assert names == ("OPENROUTER_API_KEY",)
    assert not model_key.configured(env, names)
    model_key.save(env, "sk-or-v1-abcdef123456", names)
    assert model_key.configured(env, names)
    assert "OPENROUTER_API_KEY=sk-or-v1-abcdef123456" in env.read_text()
    model_key.clear(env, names)
    assert not model_key.configured(env, names) and "OPENCODE_ZEN_API_KEY=old" in env.read_text()
    assert model_key.key_names("") == model_key.DEFAULT_KEY_NAMES
    assert model_key.key_names("not-valid lower") == model_key.DEFAULT_KEY_NAMES
