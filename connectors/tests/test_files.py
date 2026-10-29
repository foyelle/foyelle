from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import check_surface, hints, mcp_session, serving

from foyelle_connectors.files.core import TRASH, FileError, FileService, parse_roots
from foyelle_connectors.files.server import build_server


@pytest.fixture
def tree(tmp_path: Path) -> dict[str, Path]:
    docs = tmp_path / "docs"
    (docs / "notes").mkdir(parents=True)
    (docs / "notes" / "todo.md").write_text("buy milk\ncall Ada about the budget\n")
    (docs / "report.txt").write_text("quarterly report")
    (docs / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00binary")
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "cat.txt").write_text("a cat")
    outside = tmp_path / "secret"
    outside.mkdir()
    (outside / "key.txt").write_text("TOP SECRET")
    os.symlink(outside, docs / "escape")
    os.symlink(outside / "key.txt", docs / "key-link.txt")
    os.symlink(docs / "report.txt", docs / "inside-link.txt")
    return {"docs": docs, "photos": photos, "outside": outside}


@pytest.fixture
def svc(tree: dict[str, Path]) -> FileService:
    return FileService(parse_roots(f"docs={tree['docs']},photos={tree['photos']}:ro"), max_read=64)


def test_parse_roots(tree: dict[str, Path]) -> None:
    roots = parse_roots(f"docs={tree['docs']}:rw, photos={tree['photos']}:ro")
    assert roots["docs"].read_only is False and roots["photos"].read_only is True
    for bad in ("docs", "Docs=/x", "docs=relative", f"docs={tree['docs']}/missing", f"a={tree['docs']},a={tree['docs']}"):
        with pytest.raises(FileError):
            parse_roots(bad)


def test_containment(svc: FileService) -> None:
    for path in ("docs/../secret/key.txt", "docs/escape/key.txt", "docs/key-link.txt", "nope/x", "/etc/passwd",
                 f"docs/{TRASH}/journal.jsonl", "docs/./report.txt"):
        with pytest.raises(FileError):
            svc.read(path)
    with pytest.raises(FileError):
        svc.write("docs/escape/new.txt", "x")
    with pytest.raises(FileError):
        svc.write("docs/escape/sub/new.txt", "x", make_dirs=True)
    names = [e["name"] for e in svc.list("docs")["entries"]]
    assert "escape" not in names and "key-link.txt" not in names and "inside-link.txt" in names
    assert svc.read("docs/inside-link.txt")["text"] == "quarterly report"
    assert [e["name"] for e in svc.list("")["entries"]] == ["docs", "photos"]


def test_read_limits(svc: FileService) -> None:
    with pytest.raises(FileError, match="binary"):
        svc.read("docs/image.png")
    first = svc.read("docs/notes/todo.md", limit=10)
    assert first["text"] == "buy milk\nc" and first["truncated"] and first["next_offset"] == 10
    rest = svc.read("docs/notes/todo.md", offset=first["next_offset"])
    assert first["text"] + rest["text"] == "buy milk\ncall Ada about the budget\n" and not rest["truncated"]


def test_utf8_chunk_boundary(svc: FileService, tree: dict[str, Path]) -> None:
    (tree["docs"] / "u.txt").write_text("ééé", encoding="utf-8")  # 6 bytes
    part = svc.read("docs/u.txt", limit=3)
    assert part["text"] == "é" and part["next_offset"] == 2
    assert svc.read("docs/u.txt", offset=2)["text"] == "éé"


def test_search(svc: FileService) -> None:
    assert [r["path"] for r in svc.search("todo")["results"]] == ["docs/notes/todo.md"]
    assert [r["path"] for r in svc.search("*.txt", path="photos")["results"]] == ["photos/cat.txt"]
    hits = svc.search(content="ADA")["results"]
    assert hits[0]["path"] == "docs/notes/todo.md" and hits[0]["matches"][0]["line"] == 2
    assert all("secret" not in r["path"] for r in svc.search("key")["results"])


def test_write_trash_move_undo(svc: FileService, tree: dict[str, Path]) -> None:
    docs = tree["docs"]
    with pytest.raises(FileError, match="read-only"):
        svc.write("photos/new.txt", "x")
    created = svc.write("docs/new.txt", "v1")
    assert created["operation"] == "create"
    with pytest.raises(FileError, match="exists"):
        svc.write("docs/new.txt", "v2")
    svc.write("docs/new.txt", "v2", overwrite=True)
    assert (docs / "new.txt").read_text() == "v2"
    assert svc.undo()["undone"]["op"] == "overwrite"
    assert (docs / "new.txt").read_text() == "v1"
    assert svc.undo()["undone"]["op"] == "create"
    assert not (docs / "new.txt").exists()  # moved to the trash, not deleted
    assert any(p.name == "new.txt" for p in (docs / TRASH).rglob("*"))

    svc.trash("docs/report.txt")
    assert not (docs / "report.txt").exists()
    assert "report.txt" not in [e["name"] for e in svc.list("docs")["entries"]]
    svc.undo()
    assert (docs / "report.txt").read_text() == "quarterly report"

    svc.move("docs/notes", "docs/archive")
    assert (docs / "archive" / "todo.md").exists()
    with pytest.raises(FileError, match="exists"):
        svc.move("docs/report.txt", "docs/archive")
    svc.undo()
    assert (docs / "notes" / "todo.md").exists() and not (docs / "archive").exists()
    with pytest.raises(FileError, match="nothing to undo"):
        svc.undo()
    with pytest.raises(FileError):
        svc.trash("docs")
    svc.write("docs/deep/er/file.txt", "x", make_dirs=True)
    assert (docs / "deep" / "er" / "file.txt").read_text() == "x"


async def test_end_to_end(svc: FileService, tree: dict[str, Path]) -> None:
    with serving(build_server(svc)) as url:
        async with mcp_session(url) as session:
            tools = await check_surface(session, "browse", "ui://files/browser")
            assert set(tools) == {"browse", "list", "search", "read", "write", "move", "trash", "undo",
                                  "versions", "read_version", "restore", "read_image"}
            assert hints(tools["read"]) == (True, False, False)
            assert hints(tools["trash"]) == (False, True, False)
            result = await session.call_tool("browse", {"path": "docs"})
            assert {e["name"] for e in result.structuredContent["entries"]} >= {"notes", "report.txt"}
            result = await session.call_tool("write", {"path": "docs/hello.md", "content": "# hi"})
            assert result.structuredContent["operation"] == "create"
            result = await session.call_tool("read", {"path": "docs/escape/key.txt"})
            assert result.isError and "escapes" in result.content[0].text
            result = await session.call_tool("undo", {})
            assert not (tree["docs"] / "hello.md").exists()


def test_host_paths_inside_a_root_are_accepted(svc: FileService, tree: dict[str, Path]) -> None:
    host = f"{svc.roots['docs'].path}/fresh.txt"
    svc.write(host, "hi")
    assert svc.read("docs/fresh.txt")["text"] == "hi"
    assert svc.read("/docs/fresh.txt")["text"] == "hi"


def test_unknown_root_error_shows_the_path_form(svc: FileService) -> None:
    with pytest.raises(FileError, match=r"unknown root 'var'.*'docs/notes.txt'.*roots: docs, photos"):
        svc.write("/var/lib/foyelle/harness/workspace/test.txt", "x")
    with pytest.raises(FileError, match="empty path"):
        svc.read("")


def test_versions_and_restore(svc: FileService) -> None:
    svc.write("docs/v.txt", "one")
    svc.write("docs/v.txt", "two", overwrite=True)
    svc.write("docs/v.txt", "three", overwrite=True)
    listed = svc.versions("docs/v.txt")["versions"]
    assert [svc.read_version("docs/v.txt", v["version_id"])["text"] for v in listed] == ["two", "one"]
    oldest = listed[-1]["version_id"]
    out = svc.restore("docs/v.txt", oldest)
    assert out["restored"] == oldest and svc.read("docs/v.txt")["text"] == "one"
    # The restore kept what it replaced, and the restored version is still listed.
    texts = [svc.read_version("docs/v.txt", v["version_id"])["text"]
             for v in svc.versions("docs/v.txt")["versions"]]
    assert texts == ["three", "two", "one"]
    # Undo of the restore brings back the content before it.
    svc.undo()
    assert svc.read("docs/v.txt")["text"] == "three"
    with pytest.raises(FileError, match="no version"):
        svc.read_version("docs/v.txt", "nope")


def test_restore_refused_in_a_read_only_root(svc: FileService, tree: dict[str, Path]) -> None:
    with pytest.raises(FileError):
        svc.restore("photos/x.txt", "anything")


def test_read_image(svc: FileService) -> None:
    svc.write("docs/pic.svg", "<svg xmlns='http://www.w3.org/2000/svg'/>")
    out = svc.read_image("docs/pic.svg")
    assert out["mime"] == "image/svg+xml"
    import base64
    assert base64.b64decode(out["data"]).startswith(b"<svg")
    svc.write("docs/n.txt", "x")
    with pytest.raises(FileError, match="not an image"):
        svc.read_image("docs/n.txt")
