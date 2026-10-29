"""Pure policy decisions: no Hermes imports, so this module is testable on its own.

See docs/architecture.md §3 for the policy file format and the rules.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

TIERS = ("read", "reversible", "outbound", "system")

# Hermes built-in tools that stay available: they touch only the agent's own memory, skills
# (skill writes are staged for review by skills.write_approval) and conversation state.
BUILTIN_ALLOWED = frozenset({
    "memory", "skill_view", "skills_list", "skill_manage", "todo", "todo_write", "todo_read", "clarify",
    "session_search", "tool_search",
    # The tool-search bridge: tool_call re-enters dispatch, so the inner call is decided on its own.
    "tool_describe", "tool_call",
})


# Routines: Hermes's cron tool. Making, changing or removing one asks; listing them is reading.
ROUTINE_TOOL = "cronjob_manage"
ROUTINE_READ_ACTIONS = frozenset({"list", "status"})
# Arguments that would run something beside the agent (a script, a monitor, another working
# directory or toolset) or deliver outside Foyelle: never set by the model.
ROUTINE_FORBIDDEN_ARGS = ("script", "no_agent", "monitor", "monitor_script", "monitor_url", "workdir",
                          "enabled_toolsets", "interpreter")
ROUTINE_DELIVERY = frozenset({"", "origin", "local"})
# Actions on one routine name it (cronjob_manage list shows the ids).
ROUTINE_ONE = frozenset({"update", "remove", "pause", "resume", "run", "trigger"})
# Origins with nobody in front of the screen: a routine run (cron) or a heartbeat turn.
BACKGROUND = "background"


@dataclass(frozen=True)
class ToolRule:
    tier: str
    untrusted: bool = False
    egress: bool = False
    surface_only: bool = False  # only a surface (the user's own UI) may call it, never the model
    # Arguments that hold a secret (a password, a key): never hashed into the audit log, never shown.
    secret_args: tuple[str, ...] = ()
    # What it returns holds nothing about the person (on a private connector: a service's name, the
    # themes), so it isn't a question of sharing private content with a remote model.
    public: bool = False


@dataclass(frozen=True)
class Connector:
    name: str
    sensitivity: str = "private"
    tools: dict[str, ToolRule] = field(default_factory=dict)
    # A service whose unlisted tools take their tier from their own hints (a Services directory
    # entry with "new_tools": "by_hint", or one added by URL). Otherwise unlisted tools get the
    # default tier.
    by_hint: bool = False
    # A Services directory entry: offered to the model per conversation (conversation.py).
    service: bool = False
    title: str = ""


@dataclass(frozen=True)
class Policy:
    connectors: dict[str, Connector]
    default_tier: str = "outbound"
    local_model_base_urls: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict) -> "Policy":
        connectors = {}
        for name, spec in (raw.get("connectors") or {}).items():
            tools = {}
            for tool, rule in (spec.get("tools") or {}).items():
                tier = rule.get("tier", raw.get("default_tier", "outbound"))
                if tier not in TIERS:
                    raise ValueError(f"{name}.{tool}: unknown tier {tier!r}")
                tools[tool] = ToolRule(tier=tier, untrusted=bool(rule.get("untrusted")), egress=bool(rule.get("egress")),
                                       surface_only=bool(rule.get("surface_only")),
                                       secret_args=tuple(str(a) for a in rule.get("secret_args") or ()),
                                       public=bool(rule.get("public")))
            sensitivity = spec.get("sensitivity", "private")
            if sensitivity not in ("private", "public"):
                raise ValueError(f"{name}: sensitivity must be private or public")
            connectors[name] = Connector(name=name, sensitivity=sensitivity, tools=tools, by_hint=bool(spec.get("by_hint")),
                                         service=bool(spec.get("service")), title=str(spec.get("title") or ""))
        default_tier = raw.get("default_tier", "outbound")
        if default_tier not in TIERS:
            raise ValueError(f"unknown default_tier {default_tier!r}")
        urls = tuple(_norm_url(u) for u in raw.get("local_model_base_urls") or ())
        return cls(connectors=connectors, default_tier=default_tier, local_model_base_urls=urls)

    @classmethod
    def load(cls, path: Path) -> "Policy":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _norm_url(url: str) -> str:
    return str(url or "").strip().rstrip("/").lower()


@dataclass(frozen=True)
class ToolRef:
    """A registry tool name resolved to its connector, or a built-in."""

    name: str
    connector: Optional[str]
    tool: str


def resolve_tool(name: str, policy: Policy, server_of=None) -> ToolRef:
    """``mcp__<server>__<tool>`` (Hermes's registry names; the older ``mcp_<server>_<tool>`` too) ->
    connector + tool. ``server_of`` is the gateway's exact lookup (``tools.mcp_surfaces.server_of``);
    without it the longest configured connector prefix wins."""
    if name.startswith("mcp__") and "__" in name[5:]:
        server, _, tool = name[5:].partition("__")
        return ToolRef(name=name, connector=server, tool=tool)
    server = server_of(name) if server_of else None
    if server is None and name.startswith("mcp_"):
        rest = name[4:]
        for candidate in sorted(policy.connectors, key=len, reverse=True):
            if rest.startswith(candidate + "_"):
                server = candidate
                break
    if server is not None and name.startswith(f"mcp_{server}_"):
        return ToolRef(name=name, connector=server, tool=name[len(f"mcp_{server}_"):])
    if server is not None:
        return ToolRef(name=name, connector=server, tool=name)
    return ToolRef(name=name, connector=None, tool=name)


@dataclass(frozen=True)
class Decision:
    action: str  # "allow" | "ask" | "deny" | "wait" (background: saved for the person, not run)
    reason: str
    tier: str
    rule_key: str = ""
    connector: Optional[str] = None
    tool: str = ""
    untrusted: bool = False
    secret_args: tuple[str, ...] = ()


class SessionState:
    """Per-session taint: set once a tool marked ``untrusted`` returned content in the session."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tainted: set[str] = set()

    def taint(self, session_id: str) -> None:
        if session_id:
            with self._lock:
                self._tainted.add(session_id)

    def is_tainted(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._tainted

    def forget(self, session_id: str) -> None:
        with self._lock:
            self._tainted.discard(session_id)


def _from_hints(hints: dict) -> ToolRule:
    """The tier for a tool no review listed, from the server's own hints. Such a tool always comes
    from a remote service: what it is asked goes there, and what it returns is untrusted."""
    if hints.get("destructiveHint") is True:
        tier = "outbound"
    elif hints.get("readOnlyHint") is True:
        tier = "read"
    else:
        tier = "reversible"
    return ToolRule(tier=tier, untrusted=True, egress=True)


def _routine(ref: ToolRef, args: dict, origin: str, call_id: str) -> Decision:
    """Hermes's cron tool: the person confirms every routine that is made, changed or removed."""
    if origin == BACKGROUND:
        return Decision("deny", "routines can't be changed from a routine", "system", tool=ref.name)
    action = str(args.get("action") or "")
    if action in ROUTINE_READ_ACTIONS:
        return Decision("allow", "listing routines", "read", tool=ref.name)
    forbidden = [a for a in ROUTINE_FORBIDDEN_ARGS if not _empty(args.get(a))]
    if forbidden:
        return Decision("deny", f"a routine can't set {', '.join(forbidden)}; leave {'them' if len(forbidden) > 1 else 'it'} "
                                "out and try again (a routine uses this bot's own tools)", "system", tool=ref.name)
    for key in ("deliver", "failure_deliver"):
        if str(args.get(key) or "") not in ROUTINE_DELIVERY:
            return Decision("deny", "a routine's results stay in Foyelle: leave deliver out. Each run's answer appears "
                                    "as a note in this conversation; there is no Telegram, email or other channel",
                            "system", tool=ref.name)
    # Asking the person about a call the tool would refuse only shows them cards for nothing.
    if action == "create" and (_empty(args.get("schedule")) or (_empty(args.get("prompt"))
                                                                 and _empty(args.get("skills")) and _empty(args.get("skill")))):
        return Decision("deny", "a routine needs a schedule (like 'every day at 07:30' or '30 7 * * *') and a prompt "
                                "that says what to do each time", "system", tool=ref.name)
    if action in ROUTINE_ONE and _empty(args.get("job_id")):
        return Decision("deny", "say which routine with job_id (cronjob_manage with action 'list' shows them)", "system",
                        tool=ref.name)
    return Decision("ask", "a routine runs on its own later", "system",
                    rule_key=f"foyelle:routine:{ref.name}:{call_id}", tool=ref.name)


def _empty(value: Any) -> bool:
    """Unset, or only defaults (a model often fills every argument with false, "" or {})."""
    if isinstance(value, dict):
        return all(_empty(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(_empty(v) for v in value)
    return value in (None, "", False, 0)


def decide(ref: ToolRef, policy: Policy, *, tainted: bool, model_base_url: str, call_id: str,
           origin: str = "model", hints: dict | None = None, args: dict | None = None,
           share_online: bool = False) -> Decision:
    """The one decision function. ``call_id`` makes per-call approval keys unique, so an
    "always" or "session" answer can never pre-approve a later outbound action. In the
    background (``origin`` "background") nothing waits for an answer: what would ask is a
    "wait" (saved for the person) or a "deny"; ``share_online`` is the bot's standing answer to
    sending this connector's content to the online model."""
    if ref.connector is None:
        if ref.name == ROUTINE_TOOL:
            return _routine(ref, args if isinstance(args, dict) else {}, origin, call_id)
        if ref.name in BUILTIN_ALLOWED and not (origin == BACKGROUND and ref.name == "clarify"):
            return Decision("allow", "built-in agent tool", "read", tool=ref.name)
        return Decision("deny", f"{ref.name} is not available: Foyelle reaches data only through connectors",
                        "outbound", tool=ref.name)

    connector = policy.connectors.get(ref.connector)
    if connector is None:
        if origin == BACKGROUND:
            return Decision("deny", f"{ref.connector} is not a configured connector", policy.default_tier,
                            connector=ref.connector, tool=ref.tool)
        return Decision("ask", f"{ref.connector} is not a configured connector", policy.default_tier,
                        rule_key=f"foyelle:unknown:{ref.name}:{call_id}", connector=ref.connector, tool=ref.tool)

    hints = hints or {}
    rule = connector.tools.get(ref.tool)
    unreviewed = False
    if rule is None and ref.tool in ("list_resources", "read_resource"):
        rule = ToolRule(tier="read")
    if rule is None and connector.by_hint:
        rule, unreviewed = _from_hints(hints), True
    if rule is None:
        rule = ToolRule(tier=policy.default_tier)
    # A hint can make a reviewed tier stricter, never looser: a tool that now says it destroys
    # something asks every time until it is reviewed again.
    if not unreviewed and hints.get("destructiveHint") is True and rule.tier in ("read", "reversible"):
        rule = ToolRule(tier="outbound", untrusted=rule.untrusted, egress=rule.egress,
                        surface_only=rule.surface_only, secret_args=rule.secret_args)

    common = dict(tier=rule.tier, connector=connector.name, tool=ref.tool, untrusted=rule.untrusted,
                  secret_args=rule.secret_args)

    if rule.surface_only and origin != "surface":
        return Decision("deny", f"{connector.name}.{ref.tool} can only be used from its surface", **common)
    if origin == BACKGROUND:
        return _background(rule, connector, ref, common, tainted=tainted, unreviewed=unreviewed,
                           remote=_norm_url(model_base_url) not in policy.local_model_base_urls,
                           share_online=share_online)

    if rule.tier == "system":
        return Decision("ask", "system change: review the diff before it is applied",
                        rule_key=f"foyelle:system:{ref.name}:{call_id}", **common)
    if rule.tier == "outbound":
        return Decision("ask", "irreversible or outbound action", rule_key=f"foyelle:outbound:{ref.name}:{call_id}",
                        **common)
    # Taint guards against the model acting on what it read. A surface's own call is the person
    # acting on Foyelle's card (a workshop surface the agent made has the origin "workshop").
    if tainted and rule.egress and origin != "surface":
        return Decision("ask", "this session has read untrusted content and this action sends data out",
                        rule_key=f"foyelle:tainted:{ref.name}:{call_id}", **common)
    # A tool nobody reviewed: the person decides once per conversation. A service's look-up tools
    # share one answer; any other tool gets its own.
    if unreviewed and origin != "surface":
        scope = "read" if rule.tier == "read" else ref.tool
        return Decision("ask", f"{connector.name}.{ref.tool} has not been reviewed by Foyelle",
                        rule_key=f"foyelle:unreviewed:{connector.name}:{scope}", **common)
    # Private routing is about what reaches a remote model. A surface's own call returns its result
    # to the surface on screen, not to the model, so it is not asked about.
    if (origin == "model" and connector.sensitivity == "private" and not rule.public
            and _norm_url(model_base_url) not in policy.local_model_base_urls):
        # Session grain: one "session" answer covers this connector for the rest of the session.
        return Decision("ask", f"send {connector.name} content to a remote model for this session",
                        rule_key=f"foyelle:private-remote:{connector.name}", **common)
    return Decision("allow", f"{rule.tier} tier", **common)


def _background(rule: ToolRule, connector: Connector, ref: ToolRef, common: dict, *, tainted: bool,
                unreviewed: bool, remote: bool, share_online: bool) -> Decision:
    """A routine or heartbeat run: reading runs; anything that changes, sends or needs a fresh
    answer waits for the person; private content reaches the online model only if the bot was
    allowed to share it."""
    if unreviewed:
        return Decision("deny", f"{connector.name}.{ref.tool} has not been reviewed by Foyelle; routines can't use it",
                        **common)
    if rule.tier != "read":
        return Decision("wait", f"{rule.tier} action in a routine: waiting for the person", **common)
    if tainted and rule.egress:
        return Decision("wait", "this routine read untrusted content and this action sends data out", **common)
    if remote and connector.sensitivity == "private" and not rule.public and not share_online:
        return Decision("deny", f"this bot's routines may not send {connector.name} content to the online model",
                        **common)
    return Decision("allow", "read tier (routine)", **common)


APPROVAL_MARKER = "Foyelle-Request: "


def _clip(value: Any, limit: int = 1500) -> Any:
    """Long strings shortened, so the structured line stays readable JSON."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, dict):
        return {k: _clip(v, limit) for k, v in list(value.items())[:40]}
    if isinstance(value, list):
        return [_clip(v, limit) for v in value[:40]]
    return value


# Argument names that are secrets whatever the rule says: a forgotten secret_args must not leak.
SECRET_NAME = re.compile(r"(^|_)(password|passphrase|passwd|secret|token|api_?key|app_password|credential)s?$", re.I)
REDACTED = "[secret]"


def redact(args: Any, secret_args: tuple[str, ...] = ()) -> Any:
    """The arguments with every secret value replaced, for the audit digest and the approval card.
    Approval tokens are kept: they are minted per call and bind the approval to the arguments."""
    if not isinstance(args, dict):
        return args
    out = {}
    for k, v in args.items():
        secret = (k in secret_args or bool(SECRET_NAME.search(str(k)))) and k != "approval_token"
        out[k] = REDACTED if secret and v not in (None, "") else redact(v, secret_args) if isinstance(v, dict) else v
    return out


def describe(decision: Decision, args: Any, *, origin: str) -> str:
    """The approval card text: one plain sentence, then one structured line the canvas reads to
    draw the card (what kind of approval, which connector and tool, the arguments), then the
    exact technical description."""
    kind = decision.rule_key.split(":")[1] if decision.rule_key.startswith("foyelle:") else "ask"
    what = f"{decision.connector}.{decision.tool}" if decision.connector else decision.tool
    plain = {
        "routine": routine_sentence(args if isinstance(args, dict) else {}),
        "outbound": f"This will leave your home ({what}).",
        "system": f"This changes your computer ({what}).",
        "tainted": f"I read something from outside earlier; check this before it goes out ({what}).",
        "private-remote": (f"To answer, I would send what is in your {decision.connector} to the online "
                           f"model, for this conversation."),
        "unknown": f"{decision.connector} is not one of your helpers. Allow {decision.tool}?",
        "unreviewed": (f"Use {decision.connector} to look things up, for this conversation? Foyelle hasn't reviewed "
                       f"this; the service says it only reads." if decision.tier == "read" else
                       f"Let Foyelle use {decision.tool} on {decision.connector}, for this conversation? Foyelle hasn't "
                       f"reviewed this tool."),
    }.get(kind, f"May I {what}?")
    args = redact(args, decision.secret_args)
    try:
        shown = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        shown = str(args)
    if len(shown) > 2000:
        shown = shown[:2000] + "…"
    who = {"surface": "a surface", BACKGROUND: "a routine"}.get(origin, "the agent")
    technical = (f"{decision.reason}. {who} wants to run {what} ({decision.tier} tier) with {shown}")
    request = {"kind": kind, "connector": decision.connector, "tool": decision.tool, "tier": decision.tier,
               "origin": origin, "args": _clip(args if isinstance(args, dict) else {"value": args})}
    return f"{plain}\n\n{APPROVAL_MARKER}{json.dumps(request, ensure_ascii=False, default=str)}\n\n{technical}"


def routine_sentence(args: dict) -> str:
    """A routine change in words, for its approval card."""
    action = str(args.get("action") or "create")
    name = str(args.get("name") or args.get("job_id") or "a routine")
    schedule = str(args.get("schedule") or "").strip()
    prompt = " ".join(str(args.get("prompt") or "").split())
    if len(prompt) > 300:
        prompt = prompt[:300] + "…"
    if action == "create":
        when = f", {schedule}" if schedule else ""
        return f"Set up the routine “{name}”{when}. Each time, it will: {prompt}" if prompt else \
            f"Set up the routine “{name}”{when}."
    verbs = {"update": "Change", "remove": "Remove", "pause": "Pause", "resume": "Resume", "run": "Run now",
             "trigger": "Run now"}
    sentence = f"{verbs.get(action, action.capitalize())} the routine “{name}”."
    if action == "update" and (schedule or prompt):
        sentence += f" New schedule: {schedule}." if schedule else ""
        sentence += f" It will: {prompt}" if prompt else ""
    return sentence


def summarize(decision: Decision, args: Any) -> str:
    """One line for a waiting item: the tool and its (redacted, shortened) arguments."""
    shown = redact(args, decision.secret_args) if isinstance(args, dict) else {}
    parts = [f"{k}: {_clip(v, 120)}" for k, v in list(shown.items())[:6] if v not in (None, "", [], {})]
    return f"{decision.connector}.{decision.tool}" + (f" ({'; '.join(map(str, parts))})" if parts else "")
