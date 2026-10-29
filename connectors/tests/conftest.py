"""Shared test helpers: run a connector on an ephemeral port and talk MCP to it."""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import FastMCP

from foyelle_connectors.sdk import RESOURCE_MIME_TYPE


@contextlib.contextmanager
def serving(server: FastMCP) -> Iterator[str]:
    """Serve ``server`` over streamable HTTP on 127.0.0.1:<ephemeral>; yield the /mcp URL."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    uv = uvicorn.Server(uvicorn.Config(server.streamable_http_app(), log_level="warning", lifespan="on"))
    thread = threading.Thread(target=uv.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not uv.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("server did not start")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        uv.should_exit = True
        thread.join(10)
        sock.close()


@contextlib.asynccontextmanager
async def mcp_session(url: str) -> AsyncIterator[ClientSession]:
    async with httpx.AsyncClient(trust_env=False, timeout=30) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


async def check_surface(session: ClientSession, tool: str, uri: str) -> Any:
    """Assert ``tool`` links to ``uri`` and the resource is a self-contained MCP App."""
    tools = {t.name: t for t in (await session.list_tools()).tools}
    assert tool in tools, sorted(tools)
    assert tools[tool].meta == {"ui": {"resourceUri": uri}}
    listed = {str(r.uri): r for r in (await session.list_resources()).resources}
    assert listed[uri].mimeType == RESOURCE_MIME_TYPE
    result = await session.read_resource(uri)  # type: ignore[arg-type]
    content = result.contents[0]
    assert content.mimeType == RESOURCE_MIME_TYPE
    assert content.text.startswith("<!doctype html>")  # type: ignore[union-attr]
    assert "FoyelleView" in content.text  # type: ignore[union-attr]
    assert content.meta == {"ui": {"csp": {"connectDomains": [], "resourceDomains": []}, "prefersBorder": False}}
    return tools


def hints(tool: Any) -> tuple[bool | None, bool | None, bool | None]:
    a = tool.annotations
    return a.readOnlyHint, a.destructiveHint, a.openWorldHint
