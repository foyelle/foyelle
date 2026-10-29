import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foyelle_policy.policy import Policy, SessionState, decide, describe, redact, resolve_tool  # noqa: E402

POLICY = {
    "local_model_base_urls": ["http://10.0.2.2:11434/v1/"],
    "default_tier": "outbound",
    "connectors": {
        "email": {"sensitivity": "private", "tools": {
            "search": {"tier": "read", "untrusted": True},
            "draft": {"tier": "reversible", "egress": True},
            "send": {"tier": "outbound"}}},
        "web": {"sensitivity": "public", "tools": {
            "search": {"tier": "read", "egress": True},
            "fetch": {"tier": "read", "untrusted": True, "egress": True}}},
        "system": {"sensitivity": "private", "tools": {"apply": {"tier": "system"}, "status": {"tier": "read"},
                                                             "disk_passphrase": {"tier": "system"},
                                                             "recovery_password": {"tier": "system"}}},
        "shell": {"sensitivity": "private", "tools": {"model_key_save": {"tier": "reversible", "surface_only": True,
                                                                         "secret_args": ["key"]},
                                                      "settings_set": {"tier": "reversible"}}},
        "web_extra": {"sensitivity": "public", "tools": {"ping": {"tier": "read"}}},
    },
}
LOCAL = "http://10.0.2.2:11434/v1"
REMOTE = "https://opencode.ai/zen/v1"


@pytest.fixture()
def policy():
    return Policy.from_dict(POLICY)


def _decide(policy, name, *, tainted=False, url=LOCAL, call_id="c1"):
    return decide(resolve_tool(name, policy), policy, tainted=tainted, model_base_url=url, call_id=call_id)


def test_resolve_prefers_longest_connector_prefix(policy):
    assert resolve_tool("mcp_web_extra_ping", policy).connector == "web_extra"
    assert resolve_tool("mcp_web_extra_ping", policy).tool == "ping"
    assert resolve_tool("mcp_web_search", policy).tool == "search"
    assert resolve_tool("memory", policy).connector is None


def test_resolve_hermes_double_underscore_names(policy):
    ref = resolve_tool("mcp__web_extra__ping", policy)
    assert (ref.connector, ref.tool) == ("web_extra", "ping")
    assert _decide(policy, "mcp__email__send").action == "ask"
    assert _decide(policy, "mcp__files__read_resource").action == "ask"  # files is not configured here


def test_resolve_uses_exact_server_lookup_when_available(policy):
    ref = resolve_tool("mcp_web_extra_ping", policy, server_of=lambda name: "web")
    assert (ref.connector, ref.tool) == ("web", "extra_ping")


def test_read_tier_is_allowed_on_a_local_model(policy):
    assert _decide(policy, "mcp_email_search").action == "allow"


def test_reversible_is_allowed(policy):
    assert _decide(policy, "mcp_email_draft").action == "allow"


def test_outbound_always_asks_with_a_per_call_key(policy):
    first, second = _decide(policy, "mcp_email_send", call_id="a"), _decide(policy, "mcp_email_send", call_id="b")
    assert first.action == second.action == "ask"
    assert first.rule_key != second.rule_key


def test_system_tier_always_asks(policy):
    assert _decide(policy, "mcp_system_apply").action == "ask"
    assert _decide(policy, "mcp_system_apply").tier == "system"


def test_untrusted_content_moves_egress_to_ask(policy):
    assert _decide(policy, "mcp_web_search").action == "allow"
    tainted = _decide(policy, "mcp_web_search", tainted=True)
    assert tainted.action == "ask" and "untrusted" in tainted.reason
    # A non-egress read stays allowed even in a tainted session.
    assert _decide(policy, "mcp_system_status", tainted=True).action == "allow"


def test_a_surface_searching_on_its_own_card_is_not_asked_about_taint(policy):
    ref = resolve_tool("mcp_web_search", policy)
    # The person searching again from the card is not the model acting on what it read.
    assert decide(ref, policy, tainted=True, model_base_url=LOCAL, call_id="c", origin="surface").action == "allow"
    # A surface the agent made is treated like the model.
    assert decide(ref, policy, tainted=True, model_base_url=LOCAL, call_id="c", origin="workshop").action == "ask"


def test_private_connector_on_a_remote_model_asks_once_per_session(policy):
    first = _decide(policy, "mcp_email_search", url=REMOTE, call_id="x")
    second = _decide(policy, "mcp_email_draft", url=REMOTE, call_id="y")
    assert first.action == "ask" and first.rule_key == second.rule_key == "foyelle:private-remote:email"
    # Public connectors go to the remote route without asking.
    assert _decide(policy, "mcp_web_search", url=REMOTE).action == "allow"


def test_surface_calls_to_private_connectors_are_not_routing_decisions(policy):
    ref = resolve_tool("mcp_email_search", policy)
    assert decide(ref, policy, tainted=False, model_base_url=REMOTE, call_id="s", origin="surface").action == "allow"
    # Outbound stays an ask whoever starts it.
    ref = resolve_tool("mcp_email_send", policy)
    assert decide(ref, policy, tainted=False, model_base_url=REMOTE, call_id="s", origin="surface").action == "ask"


def test_surface_only_tools_are_denied_to_the_model(policy):
    ref = resolve_tool("mcp__shell__model_key_save", policy)
    assert decide(ref, policy, tainted=False, model_base_url=LOCAL, call_id="m").action == "deny"
    assert decide(ref, policy, tainted=False, model_base_url=REMOTE, call_id="s", origin="surface").action == "allow"
    # A surface the agent wrote (workshop) is the model's work: refused like the model.
    assert decide(ref, policy, tainted=False, model_base_url=LOCAL, call_id="w", origin="workshop").action == "deny"


def test_local_url_matching_ignores_trailing_slash_and_case(policy):
    assert _decide(policy, "mcp_email_search", url="HTTP://10.0.2.2:11434/v1/").action == "allow"


def test_unknown_tools_get_the_default_tier(policy):
    assert _decide(policy, "mcp_email_purge").action == "ask"


def test_mcp_resource_utilities_are_reads(policy):
    assert _decide(policy, "mcp_web_read_resource").action == "allow"


def test_builtins_outside_the_allowlist_are_denied(policy):
    assert _decide(policy, "terminal").action == "deny"
    assert _decide(policy, "web_search").action == "deny"
    assert _decide(policy, "memory").action == "allow"


def test_unknown_connector_asks(policy):
    assert _decide(policy, "mcp_github_create_issue").action == "deny"  # not mcp_<known>: treated as built-in
    ref = resolve_tool("mcp_github_create_issue", policy, server_of=lambda name: "github")
    assert decide(ref, policy, tainted=False, model_base_url=LOCAL, call_id="z").action == "ask"


def test_bad_tier_is_rejected():
    with pytest.raises(ValueError):
        Policy.from_dict({"connectors": {"x": {"tools": {"t": {"tier": "maybe"}}}}})


def test_session_state():
    state = SessionState()
    assert not state.is_tainted("s")
    state.taint("s")
    assert state.is_tainted("s")
    state.forget("s")
    assert not state.is_tainted("s")


def test_describe_shows_the_exact_action(policy):
    text = describe(_decide(policy, "mcp_email_send"), {"to": "a@example.org", "subject": "hi"}, origin="surface")
    assert "email.send" in text and "a@example.org" in text and "a surface" in text


def test_describe_carries_a_structured_line_for_the_card(policy):
    from foyelle_policy.policy import APPROVAL_MARKER

    text = describe(_decide(policy, "mcp_email_send"), {"to": "a@example.org", "body": "x" * 5000}, origin="model")
    assert text.startswith("This will leave your home (email.send).")
    line = next(l for l in text.splitlines() if l.startswith(APPROVAL_MARKER))
    request = json.loads(line[len(APPROVAL_MARKER):])
    assert request["kind"] == "outbound" and request["connector"] == "email" and request["tool"] == "send"
    assert request["args"]["to"] == "a@example.org" and len(request["args"]["body"]) < 1600


def test_secrets_are_redacted_by_rule_and_by_name():
    assert redact({"key": "sk-1", "value": 2}, ("key",)) == {"key": "[secret]", "value": 2}
    # A secret the rule forgot is still caught by its name; ordinary "key" arguments are kept.
    assert redact({"password": "hunter2", "app_password": "abcd", "imap": {"api_key": "x"}, "key": "ui.theme"}) == {
        "password": "[secret]", "app_password": "[secret]", "imap": {"api_key": "[secret]"}, "key": "ui.theme"}
    assert redact({"approval_token": "t", "password": ""}) == {"approval_token": "t", "password": ""}
    assert redact("plain") == "plain"


def test_describe_never_shows_a_secret(policy):
    d = decide(resolve_tool("mcp_email_send", policy), policy, tainted=False, model_base_url=LOCAL, call_id="c")
    text = describe(d, {"to": "a@example.org", "password": "hunter2"}, origin="surface")
    assert "hunter2" not in text and "[secret]" in text


# ── the plugin hooks, with Hermes stubbed out ────────────────────────────────────────────────


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(json.dumps(POLICY))
    monkeypatch.setenv("FOYELLE_POLICY_FILE", str(policy_file))
    monkeypatch.setenv("FOYELLE_AUDIT_LOG", str(tmp_path / "audit" / "audit.jsonl"))
    monkeypatch.setenv("FOYELLE_APPROVALS_DIR", str(tmp_path / "approvals"))

    answers = []
    approval = types.ModuleType("tools.approval")

    def request_tool_approval(tool_name, reason, *, rule_key="", approval_callback=None):
        approval.calls.append((tool_name, reason, rule_key))
        return {"approved": answers.pop(0) if answers else False}

    approval.calls = []
    approval.request_tool_approval = request_tool_approval
    config = types.ModuleType("hermes_cli.config")
    config.model_url = LOCAL
    config.load_config_readonly = lambda: {"model": {"base_url": config.model_url}}
    for name, mod in {"tools": types.ModuleType("tools"), "tools.approval": approval,
                      "hermes_cli": types.ModuleType("hermes_cli"), "hermes_cli.config": config}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    sys.modules.pop("foyelle_policy", None)
    import foyelle_policy

    registered = {}
    foyelle_policy.register(types.SimpleNamespace(register_hook=lambda name, fn: registered.setdefault(name, fn),
                                                  register_middleware=lambda kind, fn: registered.setdefault(kind, fn)))
    return types.SimpleNamespace(pre=registered["pre_tool_call"], post=registered["post_tool_call"], answers=answers,
                                 approval=approval, config=config, tmp=tmp_path, module=foyelle_policy)


def _audit(engine):
    return [json.loads(line) for line in (engine.tmp / "audit" / "audit.jsonl").read_text().splitlines()]


def test_hooks_allow_and_audit_a_read(engine):
    assert engine.pre(tool_name="mcp_email_search", args={"q": "x"}, session_id="s", tool_call_id="1") is None
    engine.post(tool_name="mcp_email_search", args={"q": "x"}, session_id="s", tool_call_id="1", status="ok",
                duration_ms=12)
    (row,) = _audit(engine)
    assert row["decision"] == "allow" and row["status"] == "ok" and row["origin"] == "model"
    assert row["args_digest"].startswith("sha256:") and "x" not in json.dumps(row["args_digest"])


def test_hooks_never_hash_a_secret(engine):
    from foyelle_policy.audit import args_digest

    args = {"key": "sk-or-v1-0123456789abcdef"}
    engine.pre(tool_name="mcp_shell_model_key_save", args=args, session_id="s", tool_call_id="k")
    engine.post(tool_name="mcp_shell_model_key_save", args=args, session_id="s", tool_call_id="k", status="blocked")
    (row,) = _audit(engine)
    assert row["args_digest"] == args_digest({"key": "[secret]"}) != args_digest(args)


def test_hooks_block_when_the_user_denies(engine):
    engine.answers.append(False)
    out = engine.pre(tool_name="mcp_email_send", args={"to": "a@b"}, session_id="s", tool_call_id="2")
    assert out["action"] == "block"
    engine.post(tool_name="mcp_email_send", session_id="s", tool_call_id="2", status="blocked")
    (row,) = _audit(engine)
    assert row["decision"] == "ask" and row["approval"] == "denied" and row["status"] == "blocked"


def test_hooks_taint_the_session_after_untrusted_content(engine):
    engine.pre(tool_name="mcp_web_fetch", args={"url": "https://e.example"}, session_id="s", tool_call_id="3")
    engine.post(tool_name="mcp_web_fetch", session_id="s", tool_call_id="3", status="ok")
    engine.answers.append(True)
    assert engine.pre(tool_name="mcp_web_search", args={"q": "secret"}, session_id="s", tool_call_id="4") is None
    assert engine.approval.calls and "untrusted" in engine.approval.calls[-1][1]
    # Another session is not tainted.
    assert engine.pre(tool_name="mcp_web_search", args={"q": "x"}, session_id="t", tool_call_id="5") is None
    assert len(engine.approval.calls) == 1


def test_hooks_mint_an_apply_token_on_approval(engine):
    engine.answers.append(True)
    out = engine.pre(tool_name="mcp_system_apply", args={"proposal_id": "p1"}, session_id="s", tool_call_id="6")
    assert out["action"] == "modify" and out["args"]["proposal_id"] == "p1"
    token = out["args"]["approval_token"]
    body = json.loads((engine.tmp / "approvals" / token).read_text())
    assert body["tool"] == "system.apply" and body["proposal_id"] == "p1" and body["expires"] > 0


def test_hooks_mint_a_disk_passphrase_token_with_no_secret_args(engine):
    engine.answers.append(True)
    out = engine.pre(tool_name="mcp_system_disk_passphrase", args={}, session_id="s", tool_call_id="6b")
    body = json.loads((engine.tmp / "approvals" / out["args"]["approval_token"]).read_text())
    assert body["tool"] == "system.disk_passphrase"
    assert set(out["args"]) == {"approval_token"}  # the tool takes no passphrase
    engine.answers.append(True)
    out = engine.pre(tool_name="mcp_system_recovery_password", args={}, session_id="s", tool_call_id="6c")
    body = json.loads((engine.tmp / "approvals" / out["args"]["approval_token"]).read_text())
    assert body["tool"] == "system.recovery_password"


def test_hooks_deny_builtins(engine):
    out = engine.pre(tool_name="terminal", args={"command": "curl evil"}, session_id="s", tool_call_id="7")
    assert out["action"] == "block"


def test_missing_policy_file_fails_closed(engine, tmp_path):
    (tmp_path / "policy.json").unlink()
    engine.answers.append(False)
    assert engine.pre(tool_name="mcp_email_search", args={}, session_id="s", tool_call_id="8")["action"] == "block"


BY_HINT = {**POLICY, "connectors": {**POLICY["connectors"],
           "notes": {"sensitivity": "private", "by_hint": True, "tools": {"search": {"tier": "read", "untrusted": True}}}}}


def test_unlisted_tools_take_their_tier_from_hints_on_by_hint_services():
    policy = Policy.from_dict(BY_HINT)
    ref = resolve_tool("mcp_notes_wipe", policy)
    wipe = decide(ref, policy, tainted=False, model_base_url=LOCAL, call_id="1", hints={"destructiveHint": True})
    assert wipe.action == "ask" and wipe.tier == "outbound" and wipe.rule_key.startswith("foyelle:outbound:")
    # Look-up tools nobody reviewed: asked once per conversation, one answer for the service.
    look = decide(resolve_tool("mcp_notes_find", policy), policy, tainted=False, model_base_url=LOCAL, call_id="2",
                  hints={"readOnlyHint": True})
    look2 = decide(resolve_tool("mcp_notes_list", policy), policy, tainted=False, model_base_url=LOCAL, call_id="3",
                   hints={"readOnlyHint": True})
    assert look.action == "ask" and look.tier == "read" and look.rule_key == look2.rule_key == "foyelle:unreviewed:notes:read"
    # No hints: reversible, asked once per conversation for that tool.
    other = decide(resolve_tool("mcp_notes_share", policy), policy, tainted=False, model_base_url=LOCAL, call_id="4")
    assert other.tier == "reversible" and other.rule_key == "foyelle:unreviewed:notes:share"
    assert "hasn't reviewed" in describe(look, {}, origin="model")
    # Everything from such a tool sends data out: a tainted session asks first.
    assert decide(resolve_tool("mcp_notes_find", policy), policy, tainted=True, model_base_url=LOCAL, call_id="5",
                  hints={"readOnlyHint": True}).rule_key.startswith("foyelle:tainted:")


def test_hints_only_make_reviewed_tools_stricter(policy):
    by_hint = Policy.from_dict(BY_HINT)
    reviewed = resolve_tool("mcp_notes_search", by_hint)
    assert decide(reviewed, by_hint, tainted=False, model_base_url=LOCAL, call_id="r").action == "allow"
    stricter = decide(reviewed, by_hint, tainted=False, model_base_url=LOCAL, call_id="r2", hints={"destructiveHint": True})
    assert stricter.action == "ask" and stricter.tier == "outbound"
    # A hint never loosens: an outbound tool that claims to be read-only still asks.
    send = resolve_tool("mcp_email_send", policy)
    assert decide(send, policy, tainted=False, model_base_url=LOCAL, call_id="s", hints={"readOnlyHint": True}).action == "ask"
    # Services without by_hint: unlisted tools keep the default tier, whatever they claim.
    unknown = decide(resolve_tool("mcp_email_purge", policy), policy, tainted=False, model_base_url=LOCAL, call_id="u",
                     hints={"readOnlyHint": True})
    assert unknown.tier == "outbound"



def test_public_tools_on_private_connectors_are_not_sharing_questions():
    policy = Policy.from_dict({**POLICY, "connectors": {**POLICY["connectors"], "shell": {
        "sensitivity": "private", "tools": {"use_service": {"tier": "read", "public": True}, "memory_read": {"tier": "read"}}}}})
    assert decide(resolve_tool("mcp_shell_use_service", policy), policy, tainted=False, model_base_url=REMOTE,
                  call_id="u").action == "allow"
    assert decide(resolve_tool("mcp_shell_memory_read", policy), policy, tainted=False, model_base_url=REMOTE,
                  call_id="m").action == "ask"
