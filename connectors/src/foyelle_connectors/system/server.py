"""system connector (port 7305): NixOS changes as reviewed git commits."""

from __future__ import annotations

import json
from functools import partial
from importlib.resources import files
from pathlib import Path
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP

from .. import sdk
from .service import DEFAULT_BUILD, DEFAULT_FLAKE, DEFAULT_NVD, SystemConfig, SystemService

INSTRUCTIONS = (
    "Change the operating system only through this loop: propose (a commit on a proposal branch), "
    "build (no switch), show the diff for review, then apply once the user approves. "
    "Proposals contain whole file contents of the NixOS flake repository. Every applied change "
    "can be rolled back."
)

REVIEW = "ui://system/review"


def build_server(service: SystemService, *, port: int | None = None) -> FastMCP:
    server = sdk.create_server("system", INSTRUCTIONS, port=port)
    sdk.register_page(server, "review", REVIEW, "system_review.html", title="System changes",
                      description="Review proposed system changes, approve or reject them, roll back.")

    async def run(fn: Any, /, **kwargs: Any) -> Any:
        return await anyio.to_thread.run_sync(partial(fn, **kwargs))

    @server.tool(title="System status", annotations=sdk.annotations(read_only=True), meta=sdk.ui_meta(REVIEW))
    async def status() -> dict[str, Any]:
        """Current generation, recent generations, proposal branches and pending proposals."""
        return await run(service.status)

    @server.tool(title="Propose a change", annotations=sdk.annotations(read_only=False, destructive=False))
    async def propose(title: str, summary: str, files: dict[str, str | None]) -> dict[str, Any]:
        """Commit a change to a new branch proposal/<id> of the config repo (nothing is built or applied).

        ``files`` maps repository-relative paths to their complete new content; null deletes a file.
        ``summary`` explains the change in plain language for the user's review.
        """
        return await run(service.propose, title=title, summary=summary, files=files)

    @server.tool(title="Propose adding a service", annotations=sdk.annotations(read_only=False, destructive=False))
    async def propose_service(title: str, url: str, hosts: list[str], auth: str = "none", signin_page: str | None = None,
                              signin_hosts: list[str] | None = None, private: bool = True, summary: str | None = None,
                              category: str = "other") -> dict[str, Any]:
        """Propose adding a service that isn't in the Services directory, by its remote MCP server's URL
        (when the person asks for one). It opens new outside hosts, so it is a system change the person
        reviews and approves; then build it, and after it is applied they connect it on the Accounts card.

        hosts: every host its helper must reach (the URL's host first; add the token host for OAuth).
        auth: "none", or "oauth" with signin_page (the authorization page) and signin_hosts (the hosts
        that page needs). private: false only when the service holds nothing about the person. Find
        these in the service's own documentation; never guess hosts."""
        return await run(service.propose_service, title=title, url=url, hosts=hosts, auth=auth, signin_page=signin_page,
                         signin_hosts=signin_hosts, private=private, summary=summary, category=category)

    @server.tool(title="Build a proposal", annotations=sdk.annotations(read_only=False, destructive=False))
    async def build(proposal_id: str, wait_seconds: float | None = None, rebuild: bool = False) -> dict[str, Any]:
        """Build the proposed system without switching to it. Long builds continue in the
        background: if state is still "building", call status or build again later."""
        return await service.build(proposal_id, wait_seconds, rebuild)

    @server.tool(title="Review a proposal", annotations=sdk.annotations(read_only=True), meta=sdk.ui_meta(REVIEW))
    async def diff(proposal_id: str) -> dict[str, Any]:
        """Show the config diff, the package diff (nvd, after a build) and whether the change is
        boot-level (kernel, initrd or boot loader; it then applies at the next boot)."""
        return await run(service.diff, proposal_id=proposal_id)

    @server.tool(title="Apply a proposal", annotations=sdk.annotations(read_only=False, destructive=True))
    async def apply(proposal_id: str, approval_token: str = "") -> dict[str, Any]:
        """Switch the system to a built proposal. Requires the user's approval: leave approval_token
        empty, the Foyelle harness fills it in when the user approves."""
        return await run(service.apply, proposal_id=proposal_id, approval_token=approval_token)

    @server.tool(title="Reject a proposal", annotations=sdk.annotations(read_only=False, destructive=False))
    async def reject(proposal_id: str, reason: str | None = None) -> dict[str, Any]:
        """Mark a proposal as rejected (its branch is kept for reference)."""
        return await run(service.reject, proposal_id=proposal_id, reason=reason)

    @server.tool(title="Roll back", annotations=sdk.annotations(read_only=False, destructive=True))
    async def rollback(generation: int | None = None, approval_token: str = "") -> dict[str, Any]:
        """Switch back to an earlier generation (default: the previous one). Requires approval;
        the harness fills in approval_token."""
        return await run(service.rollback, approval_token=approval_token, generation=generation)

    @server.tool(title="Disk encryption status", annotations=sdk.annotations(read_only=True))
    async def disk_status() -> dict[str, Any]:
        """Whether the disk is encrypted and whether it still opens with the public initial
        passphrase (then it should be changed with disk_passphrase)."""
        return await run(service.disk_status)

    @server.tool(title="Change the disk passphrase", annotations=sdk.annotations(read_only=False, destructive=False))
    async def disk_passphrase(approval_token: str = "") -> dict[str, Any]:
        """Open the disk passphrase prompt on its own console, where the user types the current and
        the new passphrase. Never ask the user for a passphrase in chat: it is typed only into that
        prompt and never passes through Foyelle. Requires approval; the harness fills in
        approval_token."""
        return await run(service.disk_passphrase, approval_token=approval_token)

    @server.tool(title="Set the recovery console password", annotations=sdk.annotations(read_only=False, destructive=False))
    async def recovery_password(approval_token: str = "") -> dict[str, Any]:
        """Open a prompt on its own console where the user types a new password for the recovery
        console (Ctrl+Alt+F2, user "recovery"). Never ask for the password in chat. Requires
        approval; the harness fills in approval_token."""
        return await run(service.recovery_password, approval_token=approval_token)

    @server.tool(title="How to extend this machine", annotations=sdk.annotations(read_only=True))
    def howto(topic: str = "connector") -> dict[str, Any]:
        """Step-by-step guide with a full example. topic: "connector" (new tools and their surface,
        a reviewed system change) or "surface" (an instant surface over existing tools)."""
        return guide(topic)

    return server


GUIDES = ("connector", "surface")


def guide(topic: str) -> dict[str, Any]:
    if topic not in GUIDES:
        raise ValueError(f"unknown topic {topic!r}; topics: {', '.join(GUIDES)}")
    text = (files(__package__) / "guides" / f"{topic}.md").read_text(encoding="utf-8")
    slots = sdk.env("FOYELLE_CONNECTOR_SLOTS", "") or ""
    taken = [s.strip() for s in slots.split(";") if s.strip()]
    if topic == "connector":
        text += "\n## Taken\n\n" + ("\n".join(f"- {s}" for s in taken) or "- (none)") + "\n"
    return {"topic": topic, "guide": text}


def _argv(name: str, default: list[str]) -> list[str]:
    raw = sdk.env(name)
    if raw is None:
        return list(default)
    value = json.loads(raw)
    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
        raise sdk.ConfigError(f"{name} must be a JSON array of strings")
    return value


def config_from_env() -> SystemConfig:
    return SystemConfig(
        repo=sdk.env_path("FOYELLE_CONFIG_REPO", "/var/lib/foyelle/config"),
        host=sdk.env_required("FOYELLE_HOST"),
        apply_socket=sdk.env_path("FOYELLE_APPLY_SOCKET", "/run/foyelle/apply.sock"),
        base_branch=sdk.env("FOYELLE_CONFIG_BRANCH", "main") or "main",
        current_system=sdk.env_path("FOYELLE_CURRENT_SYSTEM", "/run/current-system"),
        profiles_dir=sdk.env_path("FOYELLE_PROFILES_DIR", "/nix/var/nix/profiles"),
        build_command=_argv("FOYELLE_NIX_BUILD_COMMAND", DEFAULT_BUILD),
        nvd_command=_argv("FOYELLE_NVD_COMMAND", DEFAULT_NVD),
        flake_template=sdk.env("FOYELLE_FLAKE_TEMPLATE", DEFAULT_FLAKE) or DEFAULT_FLAKE,
        git=sdk.env("FOYELLE_GIT", "git") or "git",
        build_timeout=float(sdk.env_int("FOYELLE_BUILD_TIMEOUT", 3600)),
        build_wait=float(sdk.env_int("FOYELLE_BUILD_WAIT", 100)),
        apply_timeout=float(sdk.env_int("FOYELLE_APPLY_TIMEOUT", 600)),
    )


def main() -> None:
    config = config_from_env()
    if not Path(config.repo).is_dir():
        raise sdk.ConfigError(f"config repo {config.repo} does not exist")
    service = SystemService(config)
    try:
        service.reconcile()  # a switch may have restarted us mid-apply
    except Exception as exc:  # never keep the connector from starting
        sdk.get_logger("system").warning("reconcile failed: %s", exc)
    sdk.run(build_server(service))
