import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foyelle_policy.bots import Bots  # noqa: E402
from foyelle_policy.policy import Policy, decide, describe, resolve_tool  # noqa: E402
from foyelle_policy.waiting import Waiting, job_of  # noqa: E402

POLICY = {
    "local_model_base_urls": ["http://10.0.2.2:11434/v1/"],
    "connectors": {
        "calendar": {"sensitivity": "private", "tools": {"list_events": {"tier": "read"},
                                                         "add_event": {"tier": "reversible"}}},
        "email": {"sensitivity": "private", "tools": {"search": {"tier": "read", "untrusted": True},
                                                      "send": {"tier": "outbound", "egress": True}}},
        "web": {"sensitivity": "public", "tools": {"fetch": {"tier": "read", "untrusted": True, "egress": True}}},
        "kiwi": {"sensitivity": "public", "service": True, "title": "Kiwi.com", "by_hint": True,
                 "tools": {"search_flight": {"tier": "read", "egress": True}}},
        "todoist": {"sensitivity": "private", "service": True, "title": "Todoist",
                    "tools": {"find_tasks": {"tier": "read"}, "add_tasks": {"tier": "reversible"}}},
        "system": {"sensitivity": "private", "tools": {"apply": {"tier": "system"}}},
        "shell": {"sensitivity": "private", "tools": {"use_service": {"tier": "read", "public": True},
                                                      "theme_list": {"tier": "read", "public": True}}},
    },
}
LOCAL = "http://10.0.2.2:11434/v1"
REMOTE = "https://opencode.ai/zen/v1"
BOTS = {"bots": [
    {"id": "travel", "name": "Travel", "services": ["kiwi", "calendar", "system"], "shareOnline": ["calendar"]},
    {"id": "home", "name": "Home", "services": ["todoist", "email"]},
    {"id": "default", "shareOnline": ["todoist"]},
]}


@pytest.fixture()
def policy():
    return Policy.from_dict(POLICY)


def bg(policy, name, *, url=LOCAL, tainted=False, share=False, args=None, hints=None):
    return decide(resolve_tool(name, policy), policy, tainted=tainted, model_base_url=url, call_id="c",
                  origin="background", args=args, share_online=share, hints=hints)


# ── routines (Hermes's cron tool) ────────────────────────────────────────────────────────────


def test_making_a_routine_asks_and_says_what_it_will_do(policy):
    args = {"action": "create", "name": "Morning brief", "schedule": "every day at 7:30",
            "prompt": "Tell me what's on today."}
    d = decide(resolve_tool("cronjob_manage", policy), policy, tainted=False, model_base_url=LOCAL, call_id="r1",
               args=args)
    assert d.action == "ask" and d.tier == "system" and d.rule_key == "foyelle:routine:cronjob_manage:r1"
    card = describe(d, args, origin="model")
    assert card.startswith("Set up the routine “Morning brief”, every day at 7:30. Each time, it will: Tell me")
    assert '"kind": "routine"' in card


def test_listing_routines_is_reading(policy):
    d = decide(resolve_tool("cronjob_manage", policy), policy, tainted=False, model_base_url=LOCAL, call_id="r",
               args={"action": "list"})
    assert d.action == "allow"


def test_defaults_the_model_fills_in_are_not_settings(policy):
    args = {"action": "create", "name": "x", "schedule": "every 1h", "prompt": "p", "no_agent": False, "workdir": "",
            "monitor": {"script": None, "url": ""}, "enabled_toolsets": [], "deliver": "origin"}
    d = decide(resolve_tool("cronjob_manage", policy), policy, tainted=False, model_base_url=LOCAL, call_id="r", args=args)
    assert d.action == "ask"


@pytest.mark.parametrize("args", [{"action": "create", "name": "x", "prompt": "p"},
                                  {"action": "create", "name": "x", "schedule": "every 1h"},
                                  {"action": "update", "prompt": "p"}, {"action": "remove"}])
def test_an_incomplete_routine_call_is_refused_before_any_card(policy, args):
    d = decide(resolve_tool("cronjob_manage", policy), policy, tainted=False, model_base_url=LOCAL, call_id="r", args=args)
    assert d.action == "deny"


@pytest.mark.parametrize("extra", [{"script": "rm -rf /"}, {"no_agent": True}, {"workdir": "/etc"},
                                   {"enabled_toolsets": ["terminal"]}, {"monitor": {"url": "http://x"}},
                                   {"deliver": "telegram:123"}, {"failure_deliver": "slack"}])
def test_a_routine_never_runs_anything_beside_the_agent(policy, extra):
    args = {"action": "create", "name": "x", "schedule": "every 1h", "prompt": "p", **extra}
    d = decide(resolve_tool("cronjob_manage", policy), policy, tainted=False, model_base_url=LOCAL, call_id="r",
               args=args)
    assert d.action == "deny"


def test_a_routine_sent_elsewhere_is_told_where_results_go(policy):
    args = {"action": "create", "name": "x", "schedule": "every 1h", "prompt": "p", "deliver": "telegram"}
    d = decide(resolve_tool("cronjob_manage", policy), policy, tainted=False, model_base_url=LOCAL, call_id="r",
               args=args)
    assert d.action == "deny" and "leave deliver out" in d.reason and "note in this conversation" in d.reason


def test_a_routine_cannot_change_routines(policy):
    assert bg(policy, "cronjob_manage", args={"action": "create", "prompt": "p"}).action == "deny"
    assert bg(policy, "cronjob_manage", args={"action": "list"}).action == "deny"


# ── the background origin ────────────────────────────────────────────────────────────────────


def test_a_routine_reads_with_a_local_model(policy):
    assert bg(policy, "mcp__calendar__list_events").action == "allow"


def test_a_routine_never_changes_sends_or_applies(policy):
    assert bg(policy, "mcp__calendar__add_event").action == "wait"
    assert bg(policy, "mcp__email__send").action == "wait"
    assert bg(policy, "mcp__system__apply").action == "wait"


def test_private_content_reaches_the_online_model_only_when_shared(policy):
    assert bg(policy, "mcp__calendar__list_events", url=REMOTE).action == "deny"
    assert bg(policy, "mcp__calendar__list_events", url=REMOTE, share=True).action == "allow"
    assert bg(policy, "mcp__shell__theme_list", url=REMOTE).action == "allow"  # public tool
    assert bg(policy, "mcp__web__fetch", url=REMOTE).action == "allow"  # public connector


def test_a_tainted_routine_never_sends_out(policy):
    assert bg(policy, "mcp__web__fetch", tainted=True).action == "wait"


def test_a_routine_cannot_use_unreviewed_or_unknown_tools(policy):
    assert bg(policy, "mcp__kiwi__book", hints={"readOnlyHint": True}).action == "deny"
    assert bg(policy, "mcp__nowhere__thing").action == "deny"
    assert bg(policy, "clarify").action == "deny"
    assert bg(policy, "memory").action == "allow"


# ── bots ─────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def bots(tmp_path):
    path = tmp_path / "bots.json"
    path.write_text(json.dumps(BOTS))
    return Bots(path)


def test_foyelle_uses_everything(bots):
    assert bots.allows("default", "system") and bots.allows("default", "todoist")


def test_a_bot_uses_only_what_it_was_given(bots):
    assert bots.allows("travel", "kiwi") and bots.allows("travel", "calendar")
    assert not bots.allows("travel", "todoist") and not bots.allows("travel", "email")
    assert not bots.allows("travel", "system")  # never a bot's, even if listed
    assert bots.allows("travel", "shell") and bots.allows("travel", None)


def test_an_unknown_profile_gets_nothing(bots, tmp_path):
    assert not bots.allows("stranger", "calendar")
    assert bots.allows("stranger", "shell")
    assert not Bots(tmp_path / "missing.json").allows("travel", "kiwi")


def test_sharing_online_is_per_bot_and_per_service(bots):
    assert bots.shares_online("travel", "calendar")
    assert not bots.shares_online("travel", "kiwi")
    assert not bots.shares_online("home", "todoist")
    assert bots.shares_online("default", "todoist")


def test_bots_json_is_reread_when_it_changes(bots, tmp_path):
    assert not bots.allows("home", "kiwi")
    raw = json.loads((tmp_path / "bots.json").read_text())
    raw["bots"][1]["services"].append("kiwi")
    (tmp_path / "bots.json").write_text(json.dumps(raw))
    import os
    os.utime(tmp_path / "bots.json", (1, 1))
    assert bots.allows("home", "kiwi")


# ── waiting items ────────────────────────────────────────────────────────────────────────────


def test_a_waiting_item_keeps_what_to_run(tmp_path):
    item = Waiting(tmp_path).add(tool_name="mcp__todoist__add_tasks", connector="todoist", tool="add_tasks",
                                 tier="reversible", args={"content": "Pay"}, summary="todoist.add_tasks",
                                 profile="home", session_id="cron_ab12_20261027_0730", job_id="ab12", now=100)
    saved = json.loads((tmp_path / f"{item}.json").read_text())
    assert saved["toolName"] == "mcp__todoist__add_tasks" and saved["args"] == {"content": "Pay"}
    assert saved["bot"] == "home" and saved["jobId"] == "ab12" and saved["expires"] == 100 + 24 * 3600


def test_job_of_a_routine_session(monkeypatch):
    monkeypatch.delitem(sys.modules, "cron", raising=False)
    assert job_of("cron_ab12cd34ef56_20261027_073000") == "ab12cd34ef56"
    assert job_of("20261027_073000_abc") is None


# ── the hooks, with Hermes stubbed out ───────────────────────────────────────────────────────


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    (tmp_path / "policy.json").write_text(json.dumps(POLICY))
    (tmp_path / "bots.json").write_text(json.dumps(BOTS))
    monkeypatch.setenv("FOYELLE_POLICY_FILE", str(tmp_path / "policy.json"))
    monkeypatch.setenv("FOYELLE_AUDIT_LOG", str(tmp_path / "audit" / "audit.jsonl"))
    monkeypatch.setenv("FOYELLE_APPROVALS_DIR", str(tmp_path / "approvals"))
    monkeypatch.setenv("FOYELLE_BOTS_FILE", str(tmp_path / "bots.json"))
    monkeypatch.setenv("FOYELLE_WAITING_DIR", str(tmp_path / "waiting"))
    monkeypatch.setenv("FOYELLE_CONVERSATIONS_DIR", str(tmp_path / "conversations"))

    state = types.SimpleNamespace(profile="default", cron="", answers=[], asked=[])
    approval = types.ModuleType("tools.approval")

    def request_tool_approval(tool_name, reason, *, rule_key="", approval_callback=None):
        state.asked.append((tool_name, reason, rule_key))
        return {"approved": state.answers.pop(0) if state.answers else False}

    approval.request_tool_approval = request_tool_approval
    config = types.ModuleType("hermes_cli.config")
    config.load_config_readonly = lambda: {"model": {"base_url": LOCAL}}
    profiles = types.ModuleType("hermes_cli.profiles")
    profiles.current_profile_name = lambda default=None: state.profile
    session_context = types.ModuleType("gateway.session_context")
    session_context.get_session_env = lambda name, default="": state.cron if name == "HERMES_CRON_SESSION" else default
    for name, mod in {"tools": types.ModuleType("tools"), "tools.approval": approval,
                      "hermes_cli": types.ModuleType("hermes_cli"), "hermes_cli.config": config,
                      "hermes_cli.profiles": profiles, "gateway": types.ModuleType("gateway"),
                      "gateway.session_context": session_context}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    for name in [m for m in sys.modules if m == "foyelle_policy" or m.startswith("foyelle_policy.")]:
        monkeypatch.delitem(sys.modules, name)
    import foyelle_policy

    hooks = {}
    foyelle_policy.register(types.SimpleNamespace(register_hook=lambda name, fn: hooks.setdefault(name, fn),
                                                  register_middleware=lambda kind, fn: hooks.setdefault(kind, fn)))
    state.hooks, state.tmp = hooks, tmp_path
    return state


def _waiting(engine):
    return [json.loads(p.read_text()) for p in sorted((engine.tmp / "waiting").glob("*.json"))]


def test_a_bot_is_refused_a_connector_it_was_not_given(engine):
    engine.profile = "travel"
    out = engine.hooks["pre_tool_call"](tool_name="mcp__todoist__find_tasks", args={}, session_id="s", tool_call_id="1")
    assert out["action"] == "block" and "not given to this bot" in out["message"]
    out = engine.hooks["pre_tool_call"](tool_name="mcp__shell__use_service", args={"service": "Todoist"},
                                        session_id="s", tool_call_id="2")
    assert out["action"] == "block"
    assert engine.hooks["pre_tool_call"](tool_name="mcp__kiwi__search_flight", args={}, session_id="s",
                                         tool_call_id="3") is None
    engine.hooks["post_tool_call"](tool_name="mcp__todoist__find_tasks", args={}, session_id="s", tool_call_id="1",
                                   status="blocked")
    row = json.loads((engine.tmp / "audit" / "audit.jsonl").read_text().splitlines()[0])
    assert row["decision"] == "deny" and row["bot"] == "travel" and row["reason"] == "todoist was not given to this bot"


def test_a_routine_run_saves_what_needs_the_person(engine):
    engine.profile, engine.cron = "home", "1"
    out = engine.hooks["pre_tool_call"](tool_name="mcp__todoist__add_tasks", args={"content": "Pay the invoice"},
                                        session_id="cron_ab12_20261027_073000", tool_call_id="w")
    assert out["action"] == "block" and out["message"].startswith("Waiting for the person")
    assert engine.asked == []  # nobody was asked
    (item,) = _waiting(engine)
    assert item["toolName"] == "mcp__todoist__add_tasks" and item["bot"] == "home" and item["jobId"] == "ab12"
    assert "Pay the invoice" in item["summary"]


def test_a_routine_never_saves_a_secret(engine):
    engine.cron = "1"
    out = engine.hooks["pre_tool_call"](tool_name="mcp__calendar__add_event", args={"password": "hunter2"},
                                        session_id="cron_x_1", tool_call_id="s")
    assert out["action"] == "block" and "secret" in out["message"]
    assert not (engine.tmp / "waiting").exists() or _waiting(engine) == []


def test_a_heartbeat_turn_is_background(engine):
    engine.hooks["pre_llm_call"](session_id="s", turn_id="t1",
                                 user_message="[Heartbeat — recurring instruction, fires every 10m]\nCheck the parcel")
    out = engine.hooks["pre_tool_call"](tool_name="mcp__email__send", args={"to": "a@b"}, session_id="s",
                                        turn_id="t1", tool_call_id="h")
    assert out["action"] == "block" and engine.asked == [] and len(_waiting(engine)) == 1
    # The person's next message is an ordinary turn again: sending asks.
    engine.hooks["pre_llm_call"](session_id="s", turn_id="t2", user_message="send it now")
    engine.hooks["pre_tool_call"](tool_name="mcp__email__send", args={"to": "a@b"}, session_id="s", turn_id="t2",
                                  tool_call_id="h2")
    assert len(engine.asked) == 1


def test_a_routine_card_is_named_after_the_tool(engine):
    engine.answers.append(True)
    out = engine.hooks["pre_tool_call"](tool_name="cronjob_manage", args={"action": "create", "name": "Brief",
                                                                          "schedule": "0 7 * * *", "prompt": "p"},
                                        session_id="s", tool_call_id="r")
    assert out is None
    (name, reason, key) = engine.asked[0]
    assert name == "cronjob_manage" and reason.startswith("Set up the routine “Brief”")


def test_the_audit_names_the_bot_and_the_routine(engine, monkeypatch):
    cron = types.ModuleType("cron")
    identity = types.ModuleType("cron.execution_identity")
    identity.current_cron_execution = lambda: types.SimpleNamespace(job_id="job1")
    monkeypatch.setitem(sys.modules, "cron", cron)
    monkeypatch.setitem(sys.modules, "cron.execution_identity", identity)
    engine.profile, engine.cron = "travel", "1"
    engine.hooks["pre_tool_call"](tool_name="mcp__kiwi__search_flight", args={}, session_id="local-0f3a",
                                  tool_call_id="a")
    engine.hooks["post_tool_call"](tool_name="mcp__kiwi__search_flight", args={}, session_id="local-0f3a",
                                   tool_call_id="a", status="ok")
    (row,) = [json.loads(line) for line in (engine.tmp / "audit" / "audit.jsonl").read_text().splitlines()]
    assert row["origin"] == "background" and row["bot"] == "travel" and row["job"] == "job1"


def test_a_bots_request_never_carries_a_connector_it_was_not_given(engine):
    engine.profile = "travel"
    request = {"tools": [{"type": "function", "function": {"name": n}} for n in
                         ("mcp__kiwi__search_flight", "mcp__todoist__find_tasks", "mcp__email__search",
                          "mcp__shell__use_service", "memory")],
               "messages": [{"role": "system", "content": "You are Travel."},
                            {"role": "user", "content": "flights to Istanbul?"}]}
    out = engine.hooks["llm_request"](request, session_id="s")["request"]
    names = [t["function"]["name"] for t in out["tools"]]
    assert "mcp__todoist__find_tasks" not in names and "mcp__email__search" not in names
    assert "memory" in names and "mcp__shell__use_service" in names
