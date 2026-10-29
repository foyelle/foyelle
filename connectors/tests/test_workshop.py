from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from foyelle_connectors.workshop.server import SHOW_URI, build_server
from foyelle_connectors.workshop.store import Store, WorkshopError


def test_store_versions_restore_and_remove(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.save("dash", "Dash", "<p>one</p>")
    _, replaced = store.save("dash", "Dash 2", "<p>two</p>")
    assert replaced
    (version,) = store.versions("dash")
    assert store.restore("dash", version["version_id"]).body == "<p>one</p>"
    assert [v["title"] for v in store.versions("dash")] == ["Dash 2", "Dash"]
    store.remove("dash")
    with pytest.raises(WorkshopError):
        store.get("dash")
    for bad in ("", "Dash", "../x", "a" * 41):
        with pytest.raises(WorkshopError):
            store.save(bad, "t", "b")
    with pytest.raises(WorkshopError, match="too large"):
        store.save("big", "t", "x" * (200 * 1024 + 1))


async def _call(server: Any, name: str, args: dict[str, Any]) -> Any:
    _, structured = await server.call_tool(name, args)
    return structured


async def test_saved_surfaces_are_resources_and_show_opens_them(tmp_path: Path) -> None:
    server = build_server(Store(tmp_path), port=0)
    await _call(server, "save", {"name": "recent", "title": "Recent", "body": "<main id='m'>hi</main>"})
    uris = {str(r.uri) for r in await server.list_resources()}
    assert "ui://workshop/s/recent" in uris and SHOW_URI in uris
    (page,) = await server.read_resource("ui://workshop/s/recent")
    assert "<main id='m'>hi</main>" in page.content and "FoyelleView" in page.content
    await _call(server, "show", {"name": "recent"})
    (shown,) = await server.read_resource(SHOW_URI)
    assert "<main id='m'>hi</main>" in shown.content
    await _call(server, "remove", {"name": "recent"})
    assert "ui://workshop/s/recent" not in {str(r.uri) for r in await server.list_resources()}
    # Saved surfaces come back when the connector restarts.
    await _call(server, "save", {"name": "again", "title": "Again", "body": "<p/>"})
    assert "ui://workshop/s/again" in {str(r.uri) for r in await build_server(Store(tmp_path), port=0).list_resources()}
