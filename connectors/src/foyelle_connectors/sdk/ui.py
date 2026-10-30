"""MCP Apps (SEP-1865) view resources."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.resources import TextResource

from pathlib import Path

from ..ui import render_body, render_page

RESOURCE_MIME_TYPE = "text/html;profile=mcp-app"

# Views are self-contained: no network at all.
_RESOURCE_META = {"ui": {"csp": {"connectDomains": [], "resourceDomains": []}, "prefersBorder": False}}


def register_view(
    server: FastMCP,
    name: str,
    uri: str,
    html: str,
    *,
    title: str | None = None,
    description: str | None = None,
    service: str | None = None,
) -> None:
    """Expose ``html`` as a ``ui://`` resource with the MCP App MIME type.

    ``title`` and ``description`` are what the canvas's card gallery shows: the card's name and,
    in one line, what it shows. ``service`` names the online service the card belongs to, for a
    bridge helper's cards (``_meta.foyelle.service``)."""
    if not uri.startswith("ui://"):
        raise ValueError(f"view URI must use the ui:// scheme: {uri}")
    meta = {**_RESOURCE_META, "foyelle": {"service": service}} if service else _RESOURCE_META
    server.add_resource(
        TextResource(
            uri=uri,
            name=name,
            title=title,
            description=description,
            mime_type=RESOURCE_MIME_TYPE,
            text=html,
            meta=meta,
        )
    )


def register_page(
    server: FastMCP,
    name: str,
    uri: str,
    view_file: str,
    *,
    title: str,
    description: str | None = None,
    service: str | None = None,
) -> None:
    """Render ``ui/views/<view_file>`` with the shared runtime and register it."""
    register_view(server, name, uri, render_page(title, view_file), title=title, description=description,
                  service=service)


def register_html_page(
    server: FastMCP,
    name: str,
    uri: str,
    path: str | Path,
    *,
    title: str,
    description: str | None = None,
) -> None:
    """Like ``register_page``, for a view file next to a connector outside this package (one
    written for this machine): body markup plus a ``<script>`` using ``FoyelleView``."""
    body = Path(path).read_text(encoding="utf-8")
    register_view(server, name, uri, render_body(title, body), title=title, description=description)
