"""FastMCP server factory shared by every connector."""

from __future__ import annotations

import warnings
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .config import env, env_int
from .log import setup_logging

DEFAULT_HOST = "127.0.0.1"
MCP_PATH = "/mcp"

# Contract table (docs/architecture.md section 2).
PORTS = {"shell": 7300, "files": 7301, "email": 7302, "calendar": 7303, "web": 7304, "system": 7305, "workshop": 7306}


def create_server(name: str, instructions: str, *, port: int | None = None, host: str | None = None) -> FastMCP:
    """Build a FastMCP server for connector ``name``.

    The bind address and port come from ``FOYELLE_CONNECTOR_HOST`` and
    ``FOYELLE_CONNECTOR_PORT`` unless given explicitly; defaults are
    127.0.0.1 and the contract port. FastMCP turns on DNS-rebinding
    protection (Host/Origin checks) for loopback binds.
    """
    bind_host = host or env("FOYELLE_CONNECTOR_HOST", DEFAULT_HOST) or DEFAULT_HOST
    bind_port = port if port is not None else env_int("FOYELLE_CONNECTOR_PORT", PORTS.get(name, 0))
    with warnings.catch_warnings():
        # pydantic-settings warns about FastMCP's own Settings model; harmless.
        warnings.filterwarnings("ignore", message="Field 'lifespan' has an incomplete definition")
        return FastMCP(
            name=f"foyelle-{name}",
            instructions=instructions,
            host=bind_host,
            port=bind_port,
            streamable_http_path=MCP_PATH,
            log_level="INFO",
        )


def run(server: FastMCP) -> None:
    """Serve over streamable HTTP at http://<host>:<port>/mcp until interrupted.

    Equivalent to ``server.run("streamable-http")`` but keeps uvicorn from
    installing its own log format, so every line on stderr is JSON.
    """
    level = env("FOYELLE_LOG_LEVEL", "INFO") or "INFO"
    setup_logging(level)
    uvicorn.run(
        server.streamable_http_app(),
        host=server.settings.host,
        port=server.settings.port,
        log_config=None,
        log_level=level.lower(),
        server_header=False,
        proxy_headers=False,
        # Stop promptly: a system switch waits for connectors to stop, and the System connector
        # can be waiting on that very switch (its apply request), so no long graceful drain.
        timeout_graceful_shutdown=3,
    )


def annotations(
    *,
    read_only: bool,
    destructive: bool = False,
    open_world: bool = False,
    idempotent: bool | None = None,
    title: str | None = None,
) -> ToolAnnotations:
    """Standard MCP tool annotations.

    Foyelle convention: ``openWorldHint`` is true when the tool talks to a
    host outside the machine (IMAP, CalDAV, the web), false for local data.
    """
    return ToolAnnotations(
        title=title,
        readOnlyHint=read_only,
        destructiveHint=False if read_only else destructive,
        idempotentHint=True if read_only else idempotent,
        openWorldHint=open_world,
    )


def ui_meta(resource_uri: str) -> dict[str, Any]:
    """Tool ``_meta`` linking it to an MCP Apps view (SEP-1865)."""
    return {"ui": {"resourceUri": resource_uri}}
