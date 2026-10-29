"""Connectors per conversation: only the services a conversation uses reach the model.

Every tool's description goes to the model on every request, so a person with many connected
services pays for all of them in every turn. Foyelle's own helpers are always offered; a service
(a Services directory entry, policy ``"service": true``) is offered when this conversation turned
it on (the chips above the message box, or the model calling ``shell.use_service``), or when the
person names it ("what's on my Todoist?"), unless they turned it off. The others are named in one
line added to the system prompt, so the model knows it can load one.

State is one small JSON file per conversation, keyed by the stored session id, next to the shell
helper's settings (which runs as the same user and writes it for the chips).

A bot (bots.py) never sees a connector it wasn't given: its tools are left out of every request,
and only its own services are named in the note.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .bots import Bots, current_profile
from .policy import Policy

CONVERSATIONS_DIR = Path(os.environ.get("FOYELLE_CONVERSATIONS_DIR",
                                        "/var/lib/foyelle/connectors/shell/conversations"))
USE_SERVICE = ("shell", "use_service")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class Conversations:
    """Which services each conversation turned on or off, on disk."""

    def __init__(self, directory: Path = CONVERSATIONS_DIR) -> None:
        self.directory = directory
        self._lock = threading.Lock()

    def _path(self, session_id: str) -> Optional[Path]:
        return self.directory / f"{session_id}.json" if _SAFE_ID.match(session_id or "") else None

    def get(self, session_id: str) -> dict[str, set[str]]:
        path = self._path(session_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path else {}
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        return {k: {s for s in data.get(k, []) if isinstance(s, str)} for k in ("on", "off")}

    def set(self, session_id: str, service: str, on: bool) -> None:
        path = self._path(session_id)
        if path is None:
            return
        with self._lock:
            state = self.get(session_id)
            (state["on"] if on else state["off"]).add(service)
            (state["off"] if on else state["on"]).discard(service)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({k: sorted(v) for k, v in state.items()}), encoding="utf-8")
            tmp.replace(path)


def services_of(policy: Policy) -> dict[str, str]:
    """Service connector name -> the name people use for it (its title)."""
    return {c.name: (c.title or c.name) for c in policy.connectors.values() if c.service}


def _texts(content: Any) -> Iterable[str]:
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                yield part["text"]


def user_text(request: dict) -> str:
    """Everything the person wrote in this request's conversation (chat or Responses format)."""
    items = request.get("messages") if isinstance(request.get("messages"), list) else request.get("input")
    out = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and item.get("role") == "user":
            out.extend(_texts(item.get("content")))
    return "\n".join(out)


def mentioned(text: str, services: dict[str, str]) -> set[str]:
    """Services the person named, by id or by the first word of their title ("Kiwi", "Todoist")."""
    found = set()
    for name, title in services.items():
        words = {name, title, title.split()[0].split(".")[0]}
        if any(w and re.search(rf"(?<![\w]){re.escape(w)}(?![\w])", text, re.IGNORECASE) for w in words):
            found.add(name)
    return found


def tool_name(tool: Any) -> Optional[str]:
    if not isinstance(tool, dict):
        return None
    fn = tool.get("function")
    name = fn.get("name") if isinstance(fn, dict) else tool.get("name")
    return name if isinstance(name, str) else None


class PerConversation:
    def __init__(self, policy: Callable[[], Policy], server_of: Callable[[str], Optional[str]],
                 conversations: Optional[Conversations] = None, bots: Optional[Bots] = None,
                 profile: Callable[[], str] = current_profile) -> None:
        self.policy = policy
        self.server_of = server_of
        self.conversations = conversations or Conversations()
        self.bots = bots or Bots()
        self.profile = profile
        # session id -> the services offered on its latest request (for pre_tool_call).
        self.offered: dict[str, set[str]] = {}

    def active(self, session_id: str, request: dict) -> set[str]:
        services = services_of(self.policy())
        state = self.conversations.get(session_id)
        return ((state["on"] | mentioned(user_text(request), services)) - state["off"]) & set(services)

    def filter(self, request: dict, session_id: str = "", **_: Any) -> Optional[dict]:
        """``llm_request`` middleware: the request without the services this conversation doesn't
        use, plus one line naming them. None when nothing changes, or on any error: a request is
        never lost to this filter."""
        try:
            return self._filter(request, session_id)
        except Exception:  # noqa: BLE001
            return None

    def _filter(self, request: dict, session_id: str) -> Optional[dict]:
        tools = request.get("tools")
        policy = self.policy()
        profile = self.profile()
        connectors = set(policy.connectors)
        # A bot's request never carries a connector it wasn't given.
        withheld = {c for c in connectors if not self.bots.allows(profile, c)}
        services = {s: t for s, t in services_of(policy).items() if s not in withheld}
        if not isinstance(tools, list) or not (services or withheld):
            return None
        active = self.active(session_id, request) & set(services)
        kept, left_out, dropped = [], set(), False
        for tool in tools:
            name = tool_name(tool)
            server = self.server_of(name) if name else None
            if server is None and name:
                server = _prefixed(name, {c: c for c in connectors})
            if server in withheld:
                dropped = True
                continue
            if server in services and server not in active:
                left_out.add(server)
                continue
            kept.append(tool)
        self.offered[session_id] = active | (set(services) - left_out - active)
        if not left_out:
            if not dropped:
                return None
            return {"request": {**request, "tools": kept}, "source": "foyelle-policy",
                    "reason": "connectors not given to this bot"}
        names = ", ".join(sorted(services[s] for s in left_out))
        note = (f"Also connected, not loaded in this conversation: {names}. To use one, call shell.use_service "
                "with its name first; its tools are then available.")
        return {"request": _with_note({**request, "tools": kept}, note), "source": "foyelle-policy",
                "reason": "services not used in this conversation"}

    def refuse(self, session_id: str, server: Optional[str], origin: str) -> Optional[str]:
        """A reason to refuse a model's call to a service this conversation hasn't turned on."""
        if origin != "model" or server is None or server not in services_of(self.policy()):
            return None
        offered = self.offered.get(session_id)
        if offered is None or server in offered:
            return None  # nothing filtered for this session yet (a restart): the policy decides
        return f"{server} is not loaded in this conversation; call shell.use_service with its name first"

    def use(self, session_id: str, service: Any) -> None:
        """``shell.use_service`` was called: the service stays on for this conversation."""
        services = services_of(self.policy())
        if not isinstance(service, str):
            return
        match = next((s for s, title in services.items() if service.lower() in (s, title.lower())), None)
        if match:
            self.conversations.set(session_id, match, True)
            self.offered.setdefault(session_id, set()).add(match)


def _prefixed(name: str, services: dict[str, str]) -> Optional[str]:
    """The service of a tool Hermes adds per server (mcp__todoist__list_resources, read_resource,
    list_prompts, get_prompt), which the surfaces registry doesn't record."""
    for service in services:
        if re.match(rf"^mcp_{{1,2}}{re.escape(service)}_{{1,2}}", name):
            return service
    return None


def _with_note(request: dict, note: str) -> dict:
    """The note at the end of the system prompt (chat format), or of ``instructions`` (Responses)."""
    if isinstance(request.get("instructions"), str):
        return {**request, "instructions": request["instructions"] + "\n\n" + note}
    messages = request.get("messages")
    if isinstance(messages, list) and messages and isinstance(messages[0], dict) and messages[0].get("role") == "system":
        first = dict(messages[0])
        content = first.get("content")
        if isinstance(content, str):
            first["content"] = content + "\n\n" + note
        elif isinstance(content, list):
            first["content"] = [*content, {"type": "text", "text": note}]
        return {**request, "messages": [first, *messages[1:]]}
    if isinstance(request.get("system"), str):
        return {**request, "system": request["system"] + "\n\n" + note}
    return request
