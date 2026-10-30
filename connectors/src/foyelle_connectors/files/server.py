"""files connector (port 7301): scoped folders with reversible writes."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .. import sdk
from .core import FileService, parse_roots

INSTRUCTIONS = (
    "Access the user's scoped folders. Paths are virtual: '<root>/<path inside it>', for example "
    "'documents/notes.txt' (not a host path like /var/... or /home/...); call list with an empty "
    "path to see the roots. Writes are reversible: trash and overwrite keep the old content, "
    "versions lists a file's earlier versions and restore brings one back; undo reverts the latest change. Nothing is ever deleted permanently."
)

BROWSER = "ui://files/browser"


def build_server(service: FileService, *, port: int | None = None) -> FastMCP:
    server = sdk.create_server("files", INSTRUCTIONS, port=port)
    sdk.register_page(server, "browser", BROWSER, "files_browser.html", title="Files",
                      description="Your documents and pictures: look inside, move things to the trash, bring back earlier versions.")
    read = sdk.annotations(read_only=True)
    # destructiveHint follows the MCP definition (not purely additive); the
    # Foyelle tier ("reversible") comes from policy.json, not from here.
    modify = sdk.annotations(read_only=False, destructive=True)

    @server.tool(title="Browse files", annotations=read, meta=sdk.ui_meta(BROWSER))
    def browse(path: str = "") -> dict[str, Any]:
        """Show the user a folder in the file browser surface (empty path: the roots)."""
        return service.list(path) | {"history": service.history(10)}

    @server.tool(name="list", title="List folder", annotations=read)
    def list_(path: str = "") -> dict[str, Any]:
        """List a folder's entries. ``path`` is '<root>/<folder>', e.g. 'documents'; empty: the roots."""
        return service.list(path)

    @server.tool(title="Search files", annotations=read)
    def search(query: str = "", path: str = "", content: str | None = None, limit: int = 50) -> dict[str, Any]:
        """Find files by name (substring, or glob with * and ?) and/or by text they contain.

        ``path`` limits the search to one folder; ``content`` greps text files up to 1 MiB.
        """
        return service.search(query, path, content, limit)

    @server.tool(name="read", title="Read file", annotations=read)
    def read_(path: str, offset: int = 0, limit: int | None = None) -> dict[str, Any]:
        """Read a UTF-8 text file, e.g. path='documents/notes.txt' (binary files are refused). Large files come in chunks:
        pass next_offset back as offset to continue."""
        return service.read(path, offset, limit)

    @server.tool(title="Write file", annotations=modify)
    def write(path: str, content: str, overwrite: bool = False, make_dirs: bool = False) -> dict[str, Any]:
        """Create a text file, e.g. path='documents/notes.txt', or replace one with overwrite=true (the old
        version is kept; undo restores it). Paths start with a root name from list, never /var or /home."""
        return service.write(path, content, overwrite, make_dirs)

    @server.tool(title="List versions", annotations=read)
    def versions(path: str) -> dict[str, Any]:
        """Earlier versions of a file, e.g. path='documents/notes.txt', newest first: every
        overwrite (and restore) keeps the content it replaced."""
        return service.versions(path)

    @server.tool(title="Read a version", annotations=read)
    def read_version(path: str, version_id: str) -> dict[str, Any]:
        """The content of an earlier version (version_id from versions)."""
        return service.read_version(path, version_id)

    @server.tool(title="Restore a version", annotations=modify)
    def restore(path: str, version_id: str) -> dict[str, Any]:
        """Make an earlier version current. What it replaces becomes a version too."""
        return service.restore(path, version_id)

    @server.tool(title="Show an image", annotations=read)
    def read_image(path: str) -> dict[str, Any]:
        """An image file (png, jpg, gif, webp, svg) as base64 for the file browser to display."""
        return service.read_image(path)

    @server.tool(title="Move or rename", annotations=modify)
    def move(source: str, destination: str) -> dict[str, Any]:
        """Move or rename a file or folder. The destination must not exist."""
        return service.move(source, destination)

    @server.tool(title="Move to trash", annotations=modify)
    def trash(path: str) -> dict[str, Any]:
        """Move a file or folder to its root's trash. It can be restored with undo."""
        return service.trash(path)

    @server.tool(title="Undo", annotations=modify)
    def undo(operation_id: str | None = None) -> dict[str, Any]:
        """Undo the most recent write, move or trash (or the one with ``operation_id``)."""
        return service.undo(operation_id)

    return server


def main() -> None:
    roots = parse_roots(sdk.env("FOYELLE_FILES_ROOTS"))
    service = FileService(
        roots,
        max_read=sdk.env_int("FOYELLE_FILES_MAX_READ", 1024 * 1024),
        max_write=sdk.env_int("FOYELLE_FILES_MAX_WRITE", 5 * 1024 * 1024),
    )
    sdk.run(build_server(service))
