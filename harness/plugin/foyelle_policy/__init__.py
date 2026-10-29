"""Foyelle policy engine and audit log, as a Hermes plugin.

``pre_tool_call`` decides allow / ask / deny for every tool call (model or surface) from
``/etc/foyelle/policy.json``. An "ask" goes to Hermes's own human-approval gate, so the canvas
shows its approval surface; the plugin waits for the answer itself, which lets it log the outcome
and mint the one-time token the system-change apply helper requires. ``post_tool_call`` writes
one audit record per call and marks sessions that read untrusted content.

The plugin loads in every Hermes profile (each of the person's bots is one, bots.py). A routine
run or a heartbeat turn has the origin "background": nothing waits for an answer there, and a
call that would ask becomes a waiting item (waiting.py).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .audit import AuditLog, args_digest
from .bots import Bots, current_profile
from .conversation import USE_SERVICE, PerConversation
from .policy import (BACKGROUND, Decision, Policy, SessionState, decide, describe, redact, resolve_tool,
                     summarize)
from .waiting import Waiting, job_of

logger = logging.getLogger(__name__)

POLICY_PATH = Path(os.environ.get("FOYELLE_POLICY_FILE", "/etc/foyelle/policy.json"))
AUDIT_PATH = Path(os.environ.get("FOYELLE_AUDIT_LOG", "/var/lib/foyelle/audit/audit.jsonl"))
APPROVALS_DIR = Path(os.environ.get("FOYELLE_APPROVALS_DIR", "/run/foyelle/approvals"))
APPROVAL_TTL_SECONDS = 600
# Tools whose approval mints a one-time token for the apply helper.
TOKEN_TOOLS = frozenset({("system", "apply"), ("system", "rollback"), ("system", "disk_passphrase"),
                         ("system", "recovery_password")})
# Hermes's heartbeat turn (hermes_cli/heartbeat.py HEARTBEAT_PROMPT_TEMPLATE).
HEARTBEAT_PREFIX = "[Heartbeat — recurring instruction"


class _Engine:
    def __init__(self) -> None:
        self.sessions = SessionState()
        self.audit = AuditLog(AUDIT_PATH)
        self._policy: Optional[Policy] = None
        self._policy_mtime = -1.0
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.bots = Bots()
        self.waiting = Waiting()
        # session id -> the turn id of the heartbeat turn it is in (pre_llm_call sees the prompt).
        self._heartbeat_turns: dict[str, str] = {}
        self.per_conversation = PerConversation(self.policy, self.server_of, bots=self.bots)

    # ── inputs ────────────────────────────────────────────────────────────────────────────

    def policy(self) -> Policy:
        try:
            mtime = POLICY_PATH.stat().st_mtime
        except OSError:
            # No policy file: fail closed (every connector tool asks, built-ins outside the
            # allowlist are denied) rather than open.
            return Policy(connectors={}, default_tier="outbound")
        if mtime != self._policy_mtime:
            self._policy = Policy.load(POLICY_PATH)
            self._policy_mtime = mtime
        return self._policy

    @staticmethod
    def model_base_url() -> str:
        try:
            from hermes_cli.config import load_config_readonly

            model = (load_config_readonly() or {}).get("model") or {}
            return str(model.get("base_url") or "") if isinstance(model, dict) else ""
        except Exception:
            return ""

    def origin(self, session_id: str = "", turn_id: str = "") -> str:
        """Who is acting: the model, a surface, or nobody (a routine run or a
        heartbeat turn: "background")."""
        try:
            from gateway.session_context import get_session_env

            if get_session_env("HERMES_CRON_SESSION") == "1":
                return BACKGROUND
        except Exception:
            pass
        if session_id and turn_id and self._heartbeat_turns.get(session_id) == turn_id:
            return BACKGROUND
        try:
            from tools.mcp_surfaces import current_origin

            return current_origin()
        except Exception:
            return "model"

    @staticmethod
    def hints_of(name: str) -> dict:
        try:
            from tools.mcp_surfaces import tool_annotations

            return tool_annotations(name)
        except Exception:
            return {}

    @staticmethod
    def server_of(name: str) -> Optional[str]:
        try:
            from tools.mcp_surfaces import server_of

            return server_of(name)
        except Exception:
            return None

    # ── hooks ─────────────────────────────────────────────────────────────────────────────

    def pre_llm_call(self, session_id: str = "", turn_id: str = "", user_message: Any = None, **_: Any) -> None:
        """Remember which turn is a heartbeat turn: its tool calls have nobody to answer them."""
        if not session_id:
            return None
        text = user_message if isinstance(user_message, str) else ""
        with self._lock:
            if text.lstrip().startswith(HEARTBEAT_PREFIX) and turn_id:
                self._heartbeat_turns[session_id] = turn_id
            elif text:
                self._heartbeat_turns.pop(session_id, None)
        return None

    def pre_tool_call(self, tool_name: str = "", args: Any = None, session_id: str = "",
                      tool_call_id: str = "", turn_id: str = "", **_: Any) -> Optional[dict]:
        args = args if isinstance(args, dict) else {}
        origin = self.origin(session_id, turn_id)
        profile = current_profile()
        policy = self.policy()
        ref = resolve_tool(tool_name, policy, self.server_of)
        call_id = tool_call_id or secrets.token_hex(6)
        # A bot uses only the connectors it was given (Foyelle itself: all of them).
        given = self.bots.allows(profile, ref.connector)
        if not given or ((ref.connector, ref.tool) == USE_SERVICE and not self.bots.allows(profile, self._service_id(args))):
            what = ref.connector if not given else "that service"
            self._remember(call_id, {"session_id": session_id, "tool": tool_name, "connector": ref.connector,
                                     "decision": "deny", "reason": f"{what} was not given to this bot", "origin": origin,
                                     "bot": profile, "args_digest": args_digest(redact(args)), "approval": None,
                                     "tainted": self.sessions.is_tainted(session_id)})
            return {"action": "block", "message": f"Blocked by Foyelle policy: {what} was not given to this bot. "
                                                  "The person can add it on the bot's details."}
        # Services per conversation: loading one is the model's way in; a service not loaded here
        # is refused (its tools weren't offered, so a call to one is a stale or invented name).
        refusal = self.per_conversation.refuse(session_id, ref.connector, origin)
        if refusal:
            return {"action": "block", "message": f"Blocked by Foyelle policy: {refusal}."}
        decision = decide(ref, policy, tainted=self.sessions.is_tainted(session_id),
                          model_base_url=self.model_base_url(), call_id=call_id, origin=origin,
                          hints=self.hints_of(tool_name), args=args,
                          share_online=self.bots.shares_online(profile, ref.connector))
        record = {"session_id": session_id, "tool": tool_name, "connector": decision.connector,
                  "tier": decision.tier, "args_digest": args_digest(redact(args, decision.secret_args)),
                  "decision": decision.action,
                  "reason": decision.reason, "origin": origin, "bot": profile,
                  "tainted": self.sessions.is_tainted(session_id), "approval": None}
        job = job_of(session_id)
        if job:
            record["job"] = job
        if decision.action == "wait":
            return self._wait(decision, tool_name, args, profile, session_id, job, call_id, record)
        if decision.action == "deny":
            self._remember(call_id, record)
            return {"action": "block", "message": f"Blocked by Foyelle policy: {decision.reason}."}
        if decision.action == "ask":
            approved = self._ask(decision, args, origin)
            record["approval"] = "approved" if approved else "denied"
            if not approved:
                self._remember(call_id, record)
                return {"action": "block", "message": "The user did not approve this action. Do not retry it; "
                                                      "ask the user what they would like instead."}
            if (decision.connector, decision.tool) in TOKEN_TOOLS:
                token = self._mint_token(decision, args, session_id)
                self._remember(call_id, record)
                return {"action": "modify", "args": {**args, "approval_token": token}}
        self._remember(call_id, record)
        return None

    def post_tool_call(self, tool_name: str = "", args: Any = None, session_id: str = "",
                       tool_call_id: str = "", status: str = "", duration_ms: Any = None,
                       error_type: str = "", **_: Any) -> None:
        with self._lock:
            record = self._pending.pop(tool_call_id, None) if tool_call_id else None
        if record is None:
            record = {"session_id": session_id, "tool": tool_name, "connector": None, "decision": "unknown",
                      "args_digest": args_digest(redact(args if isinstance(args, dict) else {})),
                      "origin": self.origin(session_id), "bot": current_profile(),
                      "approval": None, "tainted": self.sessions.is_tainted(session_id)}
        record.update(status=status or "ok", duration_ms=duration_ms, error_type=error_type or None)
        # A service the model loaded (and the policy let it): on for the rest of this conversation.
        if (status or "ok") == "ok" and record.get("decision") in ("allow", "ask") and record.get("approval") != "denied":
            ref = resolve_tool(tool_name, self.policy(), self.server_of)
            if (ref.connector, ref.tool) == USE_SERVICE:
                self.per_conversation.use(session_id, (args if isinstance(args, dict) else {}).get("service"))
        try:
            self.audit.append(record)
        except OSError as exc:
            logger.error("foyelle-policy: cannot write the audit log %s: %s", AUDIT_PATH, exc)
        if (status or "ok") == "ok" and record.get("decision") in ("allow", "ask"):
            policy = self.policy()
            ref = resolve_tool(tool_name, policy, self.server_of)
            connector = policy.connectors.get(ref.connector or "")
            rule = connector.tools.get(ref.tool) if connector else None
            # A tool no review listed, on a service whose tools follow their hints, is untrusted too.
            if (rule is not None and rule.untrusted) or (rule is None and connector is not None and connector.by_hint):
                self.sessions.taint(session_id)

    # ── helpers ───────────────────────────────────────────────────────────────────────────

    def _service_id(self, args: dict) -> Optional[str]:
        """The connector a ``shell.use_service`` call names (by id or by title)."""
        service = args.get("service")
        if not isinstance(service, str):
            return None
        for connector in self.policy().connectors.values():
            if connector.service and service.lower() in (connector.name, (connector.title or "").lower()):
                return connector.name
        return service

    def _wait(self, decision: Decision, tool_name: str, args: dict, profile: str, session_id: str,
              job: Optional[str], call_id: str, record: dict) -> dict:
        """Save what a background run wanted to do for the person, instead of doing it."""
        if redact(args, decision.secret_args) != args:
            record["decision"] = "deny"
            self._remember(call_id, record)
            return {"action": "block", "message": "Blocked by Foyelle policy: a routine can't act with a secret."}
        try:
            item = self.waiting.add(tool_name=tool_name, connector=decision.connector, tool=decision.tool,
                                    tier=decision.tier, args=args, summary=summarize(decision, args),
                                    profile=profile, session_id=session_id, job_id=job)
        except OSError as exc:
            logger.error("foyelle-policy: cannot save a waiting item: %s", exc)
            record["decision"] = "deny"
            self._remember(call_id, record)
            return {"action": "block", "message": "Blocked by Foyelle policy: this needs the person, and it could "
                                                  "not be saved for them."}
        record["waiting"] = item
        self._remember(call_id, record)
        return {"action": "block", "message": (
            "Waiting for the person: nobody is here to approve this, so it was saved for them and did not run. "
            "Do not retry it. In your result, say in one line what is waiting for their approval.")}

    def _remember(self, call_id: str, record: dict) -> None:
        with self._lock:
            self._pending[call_id] = record

    def _ask(self, decision: Decision, args: dict, origin: str) -> bool:
        from tools.approval import request_tool_approval

        name = f"{decision.connector}.{decision.tool}" if decision.connector else decision.tool
        result = request_tool_approval(name, describe(decision, args, origin=origin),
                                       rule_key=decision.rule_key)
        return bool(isinstance(result, dict) and result.get("approved"))

    def _mint_token(self, decision: Decision, args: dict, session_id: str) -> str:
        token = secrets.token_urlsafe(24)
        APPROVALS_DIR.mkdir(parents=True, exist_ok=True)
        body = {"tool": f"{decision.connector}.{decision.tool}", "args_digest": args_digest(args),
                "proposal_id": args.get("proposal_id"), "generation": args.get("generation"),
                "session_id": session_id, "expires": time.time() + APPROVAL_TTL_SECONDS}
        path = APPROVALS_DIR / token
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(body, fh)
        return token


_engine: Optional[_Engine] = None


def register(ctx) -> None:
    global _engine
    _engine = _Engine()
    ctx.register_hook("pre_llm_call", _engine.pre_llm_call)
    ctx.register_hook("pre_tool_call", _engine.pre_tool_call)
    ctx.register_hook("post_tool_call", _engine.post_tool_call)
    ctx.register_middleware("llm_request", _engine.per_conversation.filter)
