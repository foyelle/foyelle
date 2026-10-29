"""Workshop connector: surfaces the agent makes for the user, available at once.

Each saved surface is a ``ui://workshop/s/<name>`` resource (the canvas lists it with the other
surfaces) rendered with the shared view runtime. A workshop surface may call other connectors'
tools as ``FoyelleView.run("<server>.<tool>", args)``: the gateway routes those calls with the
origin "workshop", so the policy applies approvals as for the model and refuses surface-only
tools. The connector itself has no network and no access to other connectors' data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.resources.types import FunctionResource

from .. import sdk
from ..sdk.ui import RESOURCE_MIME_TYPE, _RESOURCE_META
from ..ui import render_body
from .store import Store, Surface, WorkshopError, uri_of

INSTRUCTIONS = (
    "Make surfaces for the user: HTML views on the canvas that can call other connectors' tools. "
    "Call the System connector's howto with topic 'surface' first; it explains the page format. "
    "Saving is instant and reversible (earlier versions are kept)."
)
SHOW_URI = "ui://workshop/show"


def build_server(store: Store, *, port: int | None = None) -> FastMCP:
    server = sdk.create_server("workshop", INSTRUCTIONS, port=port)
    shown: dict[str, str] = {"name": ""}

    def page(name: str) -> str:
        try:
            s = store.get(name)
        except WorkshopError as exc:
            return render_body("Workshop", f"<main><p class='muted'>{exc}</p></main>")
        return render_body(s.title, s.body)

    def register(s: Surface) -> None:
        unregister(s.name)
        server.add_resource(FunctionResource(
            uri=uri_of(s.name), name=s.name, title=s.title, description=s.description or None,
            mime_type=RESOURCE_MIME_TYPE, meta=_RESOURCE_META, fn=lambda n=s.name: page(n)))

    def unregister(name: str) -> None:
        server._resource_manager._resources.pop(uri_of(name), None)

    # The surface the show tool opens: whichever one show was last asked for.
    server.add_resource(FunctionResource(
        uri=SHOW_URI, name="show", title="Workshop surface", mime_type=RESOURCE_MIME_TYPE,
        meta=_RESOURCE_META, fn=lambda: page(shown["name"])))
    for s in store.all():
        register(s)

    @server.tool(title="Save a surface", annotations=sdk.annotations(read_only=False, destructive=False))
    def save(name: str, title: str, body: str, description: str = "") -> dict[str, Any]:
        """Create or replace a surface. ``body`` is HTML body markup plus a <script> using
        FoyelleView (see howto 'surface'). Replacing keeps the old version."""
        s, replaced = store.save(name, title, body, description)
        register(s)
        return s.summary() | {"replaced": replaced}

    @server.tool(name="list", title="List surfaces", annotations=sdk.annotations(read_only=True))
    def list_() -> dict[str, Any]:
        """The surfaces made so far."""
        return {"surfaces": [s.summary() for s in store.all()]}

    @server.tool(title="Read a surface", annotations=sdk.annotations(read_only=True))
    def read(name: str) -> dict[str, Any]:
        """A surface's title, description and body, to change it."""
        s = store.get(name)
        return s.summary() | {"body": s.body}

    @server.tool(title="Show a surface", annotations=sdk.annotations(read_only=True), meta=sdk.ui_meta(SHOW_URI))
    def show(name: str) -> dict[str, Any]:
        """Open a saved surface on the canvas."""
        s = store.get(name)
        shown["name"] = s.name
        return s.summary()

    @server.tool(title="List versions", annotations=sdk.annotations(read_only=True))
    def versions(name: str) -> dict[str, Any]:
        """Earlier versions of a surface, newest first."""
        return {"name": name, "versions": store.versions(name)}

    @server.tool(title="Restore a version", annotations=sdk.annotations(read_only=False, destructive=False))
    def restore(name: str, version_id: str) -> dict[str, Any]:
        """Make an earlier version current (the current one is kept as a version)."""
        s = store.restore(name, version_id)
        register(s)
        return s.summary()

    @server.tool(title="Remove a surface", annotations=sdk.annotations(read_only=False, destructive=True))
    def remove(name: str) -> dict[str, Any]:
        """Move a surface to the workshop's trash (saving the same name again brings it back)."""
        store.remove(name)
        unregister(name)
        return {"removed": name}

    return server


def main() -> None:
    store = Store(sdk.env_path("FOYELLE_WORKSHOP_DIR", str(Path.home() / "workshop")))
    sdk.run(build_server(store))


if __name__ == "__main__":
    main()
