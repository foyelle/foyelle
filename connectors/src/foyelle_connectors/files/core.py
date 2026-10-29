"""Scoped file access with strict containment and reversible writes.

Paths are virtual: ``<root>/<relative path>``, e.g. ``documents/notes/a.md``.
Nothing is ever hard-deleted: trash and overwrite move the old content to
``<root>/.foyelle-trash/<operation id>/`` and record the operation in
``<root>/.foyelle-trash/journal.jsonl`` so ``undo`` can restore it.
"""

from __future__ import annotations

import base64
import codecs
import fnmatch
import json
import os
import re
import secrets
import shutil
import stat
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from ..sdk.fsutil import atomic_write

TRASH = ".foyelle-trash"
# Images the surface shows as pictures (read_image); SVG is shown through <img>, so its scripts
# never run.
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp", ".svg": "image/svg+xml"}
JOURNAL = "journal.jsonl"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class FileError(ValueError):
    """A refused or failed file operation; the message is shown to the model."""


@dataclass(frozen=True)
class Root:
    name: str
    path: Path  # resolved, absolute
    read_only: bool = False


def parse_roots(spec: str | None) -> dict[str, Root]:
    """Parse ``FOYELLE_FILES_ROOTS``.

    Comma-separated entries ``name=/absolute/path`` with an optional ``:ro``
    (read-only) or ``:rw`` suffix, e.g.
    ``documents=/home/u/Documents,photos=/home/u/Pictures:ro``.
    Names are lower-case ``[a-z0-9_-]``. Roots must exist.
    """
    roots: dict[str, Root] = {}
    for raw in filter(None, (item.strip() for item in (spec or "").split(","))):
        name, sep, rest = raw.partition("=")
        if not sep or not _NAME_RE.match(name):
            raise FileError(f"bad root entry {raw!r}: expected name=/path[:ro]")
        read_only = False
        for suffix, ro in ((":ro", True), (":rw", False)):
            if rest.endswith(suffix):
                rest, read_only = rest[: -len(suffix)], ro
        path = Path(rest)
        if not path.is_absolute():
            raise FileError(f"root {name!r} must be an absolute path")
        if not path.is_dir():
            raise FileError(f"root {name!r}: {path} is not a directory")
        if name in roots:
            raise FileError(f"duplicate root name {name!r}")
        roots[name] = Root(name, path.resolve(strict=True), read_only)
    return roots


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds")


def _is_binary(sample: bytes) -> bool:
    return b"\x00" in sample


class FileService:
    def __init__(
        self,
        roots: dict[str, Root],
        *,
        max_read: int = 1024 * 1024,
        max_write: int = 5 * 1024 * 1024,
        max_list: int = 1000,
        max_scan: int = 20000,
        grep_max_file: int = 1024 * 1024,
        search_seconds: float = 10.0,
    ) -> None:
        self.roots = roots
        self.max_read = max_read
        self.max_write = max_write
        self.max_list = max_list
        self.max_scan = max_scan
        self.grep_max_file = grep_max_file
        self.search_seconds = search_seconds

    # ---- path handling -------------------------------------------------

    def _path_help(self) -> str:
        first = next(iter(self.roots), "documents")
        return (f"paths are '<root>/<path inside it>', for example '{first}/notes.txt' "
                f"(roots: {', '.join(self.roots) or 'none'})")

    def _from_host_path(self, vpath: str) -> str:
        """Accept a host path that lies inside a root (models often use them) as its virtual path."""
        if not vpath.startswith("/"):
            return vpath
        host = PurePosixPath(vpath)
        for root in self.roots.values():
            base = PurePosixPath(root.path)
            if host == base or base in host.parents:
                return "/".join((root.name, *host.relative_to(base).parts))
        return vpath

    def _split(self, vpath: str) -> tuple[Root, list[str]]:
        if "\x00" in vpath:
            raise FileError("path contains a NUL byte")
        vpath = self._from_host_path(vpath)
        parts = [p for p in vpath.replace("\\", "/").split("/") if p]
        if not parts:
            raise FileError(f"empty path: {self._path_help()}")
        root = self.roots.get(parts[0])
        if root is None:
            raise FileError(f"unknown root {parts[0]!r}: {self._path_help()}")
        rel = parts[1:]
        for p in rel:
            if p in (".", ".."):
                raise FileError("'.' and '..' are not allowed in paths")
        if rel and rel[0] == TRASH:
            raise FileError("the trash folder is managed by the connector; use undo to restore")
        return root, rel

    def _contain(self, root: Root, real: Path) -> Path:
        if not real.is_relative_to(root.path):
            raise FileError("path escapes its root (symlink or traversal)")
        rel = real.relative_to(root.path)
        if rel.parts and rel.parts[0] == TRASH:
            raise FileError("the trash folder is managed by the connector; use undo to restore")
        return real

    def resolve(self, vpath: str, *, must_exist: bool = True) -> tuple[Root, Path]:
        """Map a virtual path to a real path inside its root (symlinks resolved)."""
        root, rel = self._split(vpath)
        candidate = root.path.joinpath(*rel)
        if must_exist:
            try:
                real = candidate.resolve(strict=True)
            except FileNotFoundError:
                raise FileError(f"{vpath} does not exist") from None
            except OSError as exc:
                raise FileError(f"cannot resolve {vpath}: {exc.strerror}") from None
            return root, self._contain(root, real)
        try:
            parent = candidate.parent.resolve(strict=True)
        except FileNotFoundError:
            raise FileError(f"parent folder of {vpath} does not exist") from None
        self._contain(root, parent)
        real = parent / candidate.name
        if real.is_symlink() or real.exists():
            real = real.resolve()
        return root, self._contain(root, real)

    def vpath(self, root: Root, real: Path) -> str:
        rel = real.relative_to(root.path).as_posix()
        return root.name if rel == "." else f"{root.name}/{rel}"

    def _writable(self, root: Root) -> None:
        if root.read_only:
            raise FileError(f"root {root.name!r} is read-only")

    # ---- read operations -----------------------------------------------

    def roots_info(self) -> list[dict[str, Any]]:
        return [{"name": r.name, "path": r.name, "type": "root", "read_only": r.read_only} for r in self.roots.values()]

    def _entry(self, root: Root, path: Path, st: os.stat_result) -> dict[str, Any]:
        kind = "dir" if stat.S_ISDIR(st.st_mode) else "file" if stat.S_ISREG(st.st_mode) else "other"
        return {
            "name": path.name,
            "path": self.vpath(root, path),
            "type": kind,
            "size": st.st_size if kind == "file" else None,
            "modified": _iso(st.st_mtime),
        }

    def list(self, path: str = "") -> dict[str, Any]:
        if not path.strip("/"):
            return {"path": "", "entries": self.roots_info(), "truncated": False}
        root, real = self.resolve(path)
        if not real.is_dir():
            raise FileError(f"{path} is not a folder")
        entries, truncated = [], False
        with os.scandir(real) as it:
            for de in it:
                if de.name == TRASH and real == root.path:
                    continue
                child = Path(de.path)
                try:
                    if de.is_symlink():
                        target = child.resolve(strict=True)
                        if not target.is_relative_to(root.path):
                            continue  # never expose what a symlink points to outside the root
                    st = child.stat()
                except OSError:
                    continue
                if len(entries) >= self.max_list:
                    truncated = True
                    break
                entries.append(self._entry(root, child, st))
        entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
        return {
            "path": self.vpath(root, real),
            "read_only": root.read_only,
            "entries": entries,
            "truncated": truncated,
        }

    def search(self, query: str = "", path: str = "", content: str | None = None, limit: int = 50) -> dict[str, Any]:
        if not query and not content:
            raise FileError("give a name query, a content string, or both")
        limit = max(1, min(limit, 200))
        targets = [self.resolve(path)] if path.strip("/") else [(r, r.path) for r in self.roots.values()]
        pattern = query.lower()
        glob = any(c in pattern for c in "*?[")
        needle = content.lower() if content else None
        results: list[dict[str, Any]] = []
        scanned, deadline, truncated = 0, time.monotonic() + self.search_seconds, False
        for root, base in targets:
            for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
                if Path(dirpath) == root.path and TRASH in dirnames:
                    dirnames.remove(TRASH)
                dirnames.sort()
                for name in sorted(filenames) + ([] if needle else dirnames):
                    scanned += 1
                    if scanned > self.max_scan or time.monotonic() > deadline or len(results) >= limit:
                        truncated = True
                        break
                    lname = name.lower()
                    if pattern and not (fnmatch.fnmatch(lname, pattern) if glob else pattern in lname):
                        continue
                    full = Path(dirpath) / name
                    try:
                        if full.is_symlink() and not full.resolve().is_relative_to(root.path):
                            continue
                        st = full.stat()
                    except OSError:
                        continue
                    hit = self._entry(root, full, st)
                    if needle:
                        if not stat.S_ISREG(st.st_mode) or st.st_size > self.grep_max_file:
                            continue
                        lines = self._grep(full, needle)
                        if not lines:
                            continue
                        hit["matches"] = lines
                    results.append(hit)
                if truncated:
                    break
            if truncated:
                break
        return {"query": query, "content": content, "results": results, "truncated": truncated}

    @staticmethod
    def _grep(path: Path, needle: str, max_lines: int = 3) -> list[dict[str, Any]]:
        try:
            data = path.read_bytes()
        except OSError:
            return []
        if _is_binary(data[:8192]):
            return []
        text = data.decode("utf-8", errors="replace")
        out = []
        for i, line in enumerate(text.splitlines(), 1):
            if needle in line.lower():
                out.append({"line": i, "text": line.strip()[:200]})
                if len(out) >= max_lines:
                    break
        return out

    def read(self, path: str, offset: int = 0, limit: int | None = None) -> dict[str, Any]:
        root, real = self.resolve(path)
        if not real.is_file():
            raise FileError(f"{path} is not a regular file")
        limit = self.max_read if limit is None else max(1, min(limit, self.max_read))
        size = real.stat().st_size
        with real.open("rb") as fh:
            if _is_binary(fh.read(8192)):
                raise FileError(f"{path} looks like a binary file; only text can be read")
            fh.seek(max(0, offset))
            chunk = fh.read(limit)
        decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
        end = offset + len(chunk)
        try:
            text = decoder.decode(chunk, final=end >= size)
        except UnicodeDecodeError:
            raise FileError(f"{path} is not UTF-8 text") from None
        pending = len(decoder.getstate()[0])  # bytes of a split character at the end
        end -= pending
        return {
            "path": self.vpath(root, real),
            "size": size,
            "modified": _iso(real.stat().st_mtime),
            "offset": offset,
            "text": text,
            "truncated": end < size,
            "next_offset": end if end < size else None,
        }

    def read_image(self, path: str) -> dict[str, Any]:
        """An image file as base64, for the surface to display (not for the model)."""
        root, real = self.resolve(path)
        mime = IMAGE_TYPES.get(real.suffix.lower())
        if mime is None or not real.is_file():
            raise FileError(f"{path} is not an image ({', '.join(sorted(IMAGE_TYPES))})")
        size = real.stat().st_size
        if size > self.max_write:
            raise FileError(f"{path} is too large to preview ({size} bytes)")
        return {"path": self.vpath(root, real), "mime": mime, "size": size,
                "data": base64.b64encode(real.read_bytes()).decode("ascii")}

    # ---- versions -------------------------------------------------------

    def _versions(self, root: Root, vpath: str) -> list[dict[str, Any]]:
        out = []
        for op in self._journal(root):
            if op.get("op") != "overwrite" or op.get("path") != vpath:
                continue
            stored = root.path / TRASH / op["stored"]
            if stored.is_file():  # undo of that overwrite moves the copy back
                out.append({"version_id": op["id"], "saved": _iso(op["ts"]), "replaced_at": op["ts"],
                            "size": stored.stat().st_size, "stored": stored})
        out.sort(key=lambda v: v["replaced_at"], reverse=True)
        return out

    def versions(self, path: str) -> dict[str, Any]:
        """Earlier versions of a file, newest first: the content it had before each overwrite."""
        root, real = self.resolve(path)
        vpath = self.vpath(root, real)
        listed = [{k: v[k] for k in ("version_id", "saved", "size")} for v in self._versions(root, vpath)]
        return {"path": vpath, "current": {"size": real.stat().st_size, "modified": _iso(real.stat().st_mtime)},
                "versions": listed}

    def _version_file(self, path: str, version_id: str) -> tuple[Root, Path, Path]:
        root, real = self.resolve(path)
        for v in self._versions(root, self.vpath(root, real)):
            if v["version_id"] == version_id:
                return root, real, v["stored"]
        raise FileError(f"{path} has no version {version_id!r}; call versions to list them")

    def read_version(self, path: str, version_id: str) -> dict[str, Any]:
        """The text of an earlier version (images: their base64 data, as read_image)."""
        root, real, stored = self._version_file(path, version_id)
        data = stored.read_bytes()
        out: dict[str, Any] = {"path": self.vpath(root, real), "version_id": version_id, "size": len(data)}
        mime = IMAGE_TYPES.get(real.suffix.lower())
        if mime and mime != "image/svg+xml":
            return out | {"mime": mime, "data": base64.b64encode(data).decode("ascii")}
        if _is_binary(data[:8192]):
            raise FileError("that version is binary; only text and images can be shown")
        try:
            text = data[: self.max_read].decode("utf-8")
        except UnicodeDecodeError:
            raise FileError("that version is not UTF-8 text") from None
        return out | {"text": text, "truncated": len(data) > self.max_read}

    def restore(self, path: str, version_id: str) -> dict[str, Any]:
        """Make an earlier version the current one. The content it replaces becomes a version
        itself, so a restore can be undone or restored back."""
        root, real, stored = self._version_file(path, version_id)
        self._writable(root)
        result = self._overwrite(root, real, stored.read_bytes())
        return result | {"restored": version_id}

    # ---- reversible writes ---------------------------------------------

    def _overwrite(self, root: Root, real: Path, data: bytes) -> dict[str, Any]:
        """Replace an existing file, keeping the old content as a version (and for undo)."""
        if not real.is_file():
            raise FileError(f"{self.vpath(root, real)} is not a regular file")
        op_id = f"{time.time_ns():x}-{secrets.token_hex(3)}"
        dest_dir = self._trash_dir(root) / op_id
        dest_dir.mkdir(mode=0o700)
        shutil.copy2(real, dest_dir / real.name)
        atomic_write(real, data)
        op = self._record(root, {"id": op_id, "op": "overwrite", "path": self.vpath(root, real),
                                 "stored": f"{op_id}/{real.name}"})
        return {"path": op["path"], "size": len(data), "operation": "overwrite", "operation_id": op["id"]}

    def _trash_dir(self, root: Root) -> Path:
        d = root.path / TRASH
        d.mkdir(mode=0o700, exist_ok=True)
        return d

    def _record(self, root: Root, op: dict[str, Any]) -> dict[str, Any]:
        op = {"id": f"{time.time_ns():x}-{secrets.token_hex(3)}", "ts": time.time(), "root": root.name, **op}
        with (self._trash_dir(root) / JOURNAL).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(op) + "\n")
        return op

    def _stash(self, root: Root, real: Path, op_id: str, suffix: str = "") -> str:
        """Move ``real`` into the trash under ``op_id``; return its trash-relative name."""
        dest_dir = self._trash_dir(root) / op_id
        dest_dir.mkdir(mode=0o700, exist_ok=True)
        dest = dest_dir / (real.name + suffix)
        shutil.move(real, dest)
        return dest.relative_to(root.path / TRASH).as_posix()

    def write(self, path: str, content: str, overwrite: bool = False, make_dirs: bool = False) -> dict[str, Any]:
        data = content.encode("utf-8")
        if len(data) > self.max_write:
            raise FileError(f"content too large ({len(data)} bytes, max {self.max_write})")
        root, rel = self._split(path)
        self._writable(root)
        if make_dirs:
            # Create level by level so a symlinked component cannot lead outside.
            cur = root.path
            for part in rel[:-1]:
                cur = cur / part
                if not cur.exists():
                    cur.mkdir()
                cur = self._contain(root, cur.resolve(strict=True))
        root, real = self.resolve(path, must_exist=False)
        if real == root.path:
            raise FileError("cannot write to a root")
        if real.exists():
            if not overwrite:
                raise FileError(f"{path} exists; pass overwrite=true to replace it (the old version is kept)")
            return self._overwrite(root, real, data)
        atomic_write(real, data)
        op = self._record(root, {"op": "create", "path": self.vpath(root, real)})
        return {"path": op["path"], "size": len(data), "operation": op["op"], "operation_id": op["id"]}

    def move(self, source: str, destination: str) -> dict[str, Any]:
        sroot, sreal = self.resolve(source)
        droot, dreal = self.resolve(destination, must_exist=False)
        self._writable(sroot)
        self._writable(droot)
        if sreal == sroot.path:
            raise FileError("cannot move a root")
        if dreal.exists() or dreal.is_symlink():
            raise FileError(f"{destination} already exists")
        if dreal.is_relative_to(sreal):
            raise FileError("cannot move a folder into itself")
        shutil.move(sreal, dreal)
        op = self._record(
            sroot, {"op": "move", "path": self.vpath(sroot, sreal), "destination": self.vpath(droot, dreal)}
        )
        return {"path": op["path"], "destination": op["destination"], "operation_id": op["id"]}

    def trash(self, path: str) -> dict[str, Any]:
        root, real = self.resolve(path)
        self._writable(root)
        if real == root.path:
            raise FileError("cannot trash a root")
        op_id = f"{time.time_ns():x}-{secrets.token_hex(3)}"
        vpath = self.vpath(root, real)
        stored = self._stash(root, real, op_id)
        op = self._record(root, {"id": op_id, "op": "trash", "path": vpath, "stored": stored})
        return {"path": vpath, "operation_id": op["id"], "restorable": True}

    def _journal(self, root: Root) -> list[dict[str, Any]]:
        path = root.path / TRASH / JOURNAL
        if not path.exists():
            return []
        ops: dict[str, dict[str, Any]] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if "undo" in rec:
                if rec["undo"] in ops:
                    ops[rec["undo"]]["undone"] = True
            elif "id" in rec:
                ops[rec["id"]] = rec
        return list(ops.values())

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        ops = [op for r in self.roots.values() if not r.read_only for op in self._journal(r)]
        ops.sort(key=lambda o: o["ts"], reverse=True)
        return ops[:limit]

    def undo(self, operation_id: str | None = None) -> dict[str, Any]:
        """Revert one operation (default: the most recent one not yet undone)."""
        candidates = [op for op in self.history(limit=10_000) if not op.get("undone")]
        if operation_id:
            candidates = [op for op in candidates if op["id"] == operation_id]
        if not candidates:
            raise FileError("nothing to undo" if not operation_id else f"no undoable operation {operation_id}")
        op = candidates[0]
        root = self.roots[op["root"]]
        trash_dir = root.path / TRASH
        match op["op"]:
            case "trash":
                _, target = self.resolve(op["path"], must_exist=False)
                if target.exists():
                    raise FileError(f"{op['path']} exists again; move it away first")
                shutil.move(trash_dir / op["stored"], target)
            case "overwrite":
                _, target = self.resolve(op["path"], must_exist=False)
                if target.exists():
                    self._stash(root, target, op["id"], suffix=f".replaced-{int(time.time())}")
                shutil.move(trash_dir / op["stored"], target)
            case "create":
                _, target = self.resolve(op["path"])
                self._stash(root, target, op["id"])
            case "move":
                _, src = self.resolve(op["destination"])
                _, back = self.resolve(op["path"], must_exist=False)
                if back.exists():
                    raise FileError(f"{op['path']} exists again; move it away first")
                shutil.move(src, back)
            case other:
                raise FileError(f"cannot undo operation type {other!r}")
        with (trash_dir / JOURNAL).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"undo": op["id"], "ts": time.time()}) + "\n")
        return {"undone": {k: op[k] for k in ("id", "op", "path", "destination") if k in op}}
