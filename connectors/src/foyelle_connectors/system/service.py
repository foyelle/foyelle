"""System change loop: propose (git commit on a branch), build, review, apply via the helper.

Every external command runs with an argument list, a timeout and no shell.
Proposal metadata lives in ``<git dir>/foyelle/proposals/<id>.json`` so it
never pollutes the config history.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

DEFAULT_FLAKE = "git+file://{repo}?ref={ref}&rev={rev}#nixosConfigurations.{host}.config.system.build.toplevel"
DEFAULT_BUILD = ["nix", "build", "--no-link", "--print-out-paths", "{flake}"]
DEFAULT_NVD = ["nvd", "diff", "{current}", "{out}"]
ZERO_SHA = "0" * 40
_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_FILES = 50
MAX_FILE_BYTES = 1024 * 1024
MAX_DIFF = 200_000
AGENT = {"GIT_AUTHOR_NAME": "Foyelle agent", "GIT_AUTHOR_EMAIL": "agent@foyelle.invalid",
         "GIT_COMMITTER_NAME": "Foyelle agent", "GIT_COMMITTER_EMAIL": "agent@foyelle.invalid"}


class ChangeError(RuntimeError):
    """A refused or failed system operation."""


@dataclass
class SystemConfig:
    repo: Path
    host: str
    apply_socket: Path = Path("/run/foyelle/apply.sock")
    base_branch: str = "main"
    current_system: Path = Path("/run/current-system")
    profiles_dir: Path = Path("/nix/var/nix/profiles")
    build_command: list[str] = field(default_factory=lambda: list(DEFAULT_BUILD))
    nvd_command: list[str] = field(default_factory=lambda: list(DEFAULT_NVD))
    flake_template: str = DEFAULT_FLAKE
    git: str = "git"
    build_timeout: float = 3600
    build_wait: float = 100
    command_timeout: float = 120
    apply_timeout: float = 600


BRIEF_KEYS = ("id", "title", "state", "created", "stale", "boot_level", "apply_error")


def _brief(meta: dict[str, Any]) -> dict[str, Any]:
    return {k: meta[k] for k in BRIEF_KEYS if k in meta}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _tail(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else "…" + text[-limit:]


def check_repo_path(path: str) -> str:
    """A safe, normalised repository-relative path."""
    if not path or "\x00" in path or "\\" in path or path.startswith("/"):
        raise ChangeError(f"invalid path {path!r}: use a relative path with forward slashes")
    parts = PurePosixPath(path).parts
    if any(p in ("..", ".", "") for p in parts) or parts[0] == ".git" or ".git" in parts:
        raise ChangeError(f"invalid path {path!r}")
    return "/".join(parts)


def boot_changes(current: Path, new: Path) -> list[str]:
    """Which boot-relevant parts differ between two system closures.

    Compares the kernel and initrd store paths, kernel-params, and the boot
    loader installer referenced by switch-to-configuration. Anything that
    cannot be compared counts as a change (fail safe: install for next boot).
    """
    changes = []
    for name in ("kernel", "initrd"):
        a, b = current / name, new / name
        if not (a.exists() and b.exists()) or os.path.realpath(a) != os.path.realpath(b):
            changes.append(name)

    def read(p: Path) -> str | None:
        try:
            return p.read_text(errors="replace")[:65536]
        except OSError:
            return None

    a, b = read(current / "kernel-params"), read(new / "kernel-params")
    if a is None or b is None or a.strip() != b.strip():
        changes.append("kernel-params")

    def loader(p: Path) -> str | None:
        text = read(p / "bin" / "switch-to-configuration")
        if text is None:
            return None
        return "\n".join(sorted(line.strip() for line in text.splitlines() if "INSTALL_BOOTLOADER" in line or ("install-" in line and "boot" in line)))

    a, b = loader(current), loader(new)
    if a is None or b is None or a != b:
        changes.append("bootloader")
    return changes


class SystemService:
    def __init__(self, config: SystemConfig) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._builds: dict[str, asyncio.Task[None]] = {}

    # ---- git ---------------------------------------------------------------

    def git(self, *args: str, input: bytes | None = None, env: dict[str, str] | None = None) -> str:
        repo = str(self.config.repo)
        cmd = [self.config.git, "-C", repo, "-c", f"safe.directory={repo}", "-c", "commit.gpgsign=false", *args]
        full_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **AGENT, **(env or {})}
        try:
            proc = subprocess.run(
                cmd, input=input, capture_output=True, timeout=self.config.command_timeout, env=full_env, check=False
            )
        except subprocess.TimeoutExpired:
            raise ChangeError(f"git {args[0]} timed out") from None
        except FileNotFoundError:
            raise ChangeError("git is not installed") from None
        if proc.returncode != 0:
            raise ChangeError(f"git {args[0]} failed: {proc.stderr.decode(errors='replace').strip()[:500]}")
        return proc.stdout.decode(errors="replace")

    def _rev(self, ref: str) -> str | None:
        try:
            return self.git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip() or None
        except ChangeError:
            return None

    def _git_dir(self) -> Path:
        return Path(self.git("rev-parse", "--absolute-git-dir").strip())

    def _meta_dir(self) -> Path:
        d = self._git_dir() / "foyelle" / "proposals"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ---- proposal metadata -------------------------------------------------

    def _check_id(self, proposal_id: str) -> str:
        if not _ID_RE.match(proposal_id):
            raise ChangeError(f"invalid proposal id {proposal_id!r}")
        return proposal_id

    def load(self, proposal_id: str, base_head: str | None = None) -> dict[str, Any]:
        path = self._meta_dir() / f"{self._check_id(proposal_id)}.json"
        try:
            meta = json.loads(path.read_text())
        except FileNotFoundError:
            raise ChangeError(f"no proposal {proposal_id}") from None
        head = base_head or self._rev(f"refs/heads/{self.config.base_branch}")
        meta["stale"] = meta.get("base") != head and meta["state"] not in ("applied", "rolled_back", "rejected")
        return meta

    def _save(self, meta: dict[str, Any]) -> dict[str, Any]:
        meta = {k: v for k, v in meta.items() if k != "stale"}
        meta["updated"] = _now()
        path = self._meta_dir() / f"{meta['id']}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, indent=2))
        os.replace(tmp, path)
        return meta

    def _update(self, proposal_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            meta = self.load(proposal_id)
            meta.update(changes)
            self._save(meta)
        return self.load(proposal_id)

    def proposals(self, limit: int = 20) -> list[dict[str, Any]]:
        metas = []
        head = self._rev(f"refs/heads/{self.config.base_branch}")
        for path in sorted(self._meta_dir().glob("*.json"), reverse=True)[:limit]:
            try:
                metas.append(self.load(path.stem, head))
            except (ChangeError, ValueError):
                continue
        return metas

    # ---- tools -------------------------------------------------------------

    def propose_service(self, *, title: str, url: str, hosts: list[str], auth: str = "none",
                        signin_page: str | None = None, signin_hosts: list[str] | None = None,
                        private: bool = True, summary: str | None = None, category: str = "other") -> dict[str, Any]:
        """Propose adding a service outside the Services directory by its MCP URL: an entry in the
        config repo's services.json (a reviewed system change, since it opens new outside hosts)."""
        from ..services import directory

        branch = self.config.base_branch
        try:
            current = json.loads(self.git("show", f"refs/heads/{branch}:services.json"))
        except (ChangeError, ValueError):
            current = {}
        services = current.get("services") if isinstance(current, dict) else None
        services = [s for s in services if isinstance(s, dict)] if isinstance(services, list) else []
        taken_ids = {s.get("id") for s in services} | set(directory.load())
        taken_slots = {s.get("slot") for s in services}
        slot = next((n for n in range(directory.CUSTOM_MAX_SLOT + 1) if n not in taken_slots), None)
        if slot is None:
            raise ChangeError("there is no room for another service added by URL; remove one first")
        try:
            entry = directory.custom_entry(title=title, url=url, hosts=hosts, slot=slot, auth=auth,
                                           signin_page=signin_page, signin_hosts=signin_hosts,
                                           private=private, summary=summary, category=category)
        except directory.DirectoryError as exc:
            raise ChangeError(str(exc)) from None
        if entry["id"] in taken_ids:
            raise ChangeError(f"there is already a service called {entry['id']}; give this one another name")
        content = json.dumps({"services": [*services, entry]}, indent=2) + "\n"
        reach = ", ".join(sorted(set(entry["hosts"]) | set(entry["auth"].get("signin_hosts", []))))
        plain = (f"Add {entry['title']}, a service Foyelle hasn't reviewed, by its address {entry['url']}.\n\n"
                 f"Foyelle will be able to reach: {reach}. What you ask this service goes there; what it returns "
                 "is treated as untrusted, and Foyelle asks you once per conversation before using one of its "
                 "tools (destructive ones every time). After this change, connect it on the Accounts card.")
        return self.propose(title=f"Add the service {entry['title']}", summary=plain, files={"services.json": content})

    def propose(self, title: str, summary: str, files: dict[str, str | None]) -> dict[str, Any]:
        title = title.strip()
        if not title or "\n" in title or len(title) > 120:
            raise ChangeError("title must be one line of at most 120 characters")
        if len(summary) > 10_000:
            raise ChangeError("summary is too long (max 10000 characters)")
        if not files or len(files) > MAX_FILES:
            raise ChangeError(f"give between 1 and {MAX_FILES} files")
        clean: dict[str, bytes | None] = {}
        for path, content in files.items():
            data = None if content is None else content.encode("utf-8")
            if data is not None and len(data) > MAX_FILE_BYTES:
                raise ChangeError(f"{path} is too large")
            clean[check_repo_path(path)] = data
        branch = self.config.base_branch
        with self._lock:
            base = self._rev(f"refs/heads/{branch}")
            if base is None:
                raise ChangeError(f"the config repo has no {branch} branch")
            base_tree = self.git("rev-parse", f"{base}^{{tree}}").strip()
            fd, index = tempfile.mkstemp(prefix="foyelle-index-")
            os.close(fd)
            os.unlink(index)  # git creates it; an empty file is not a valid index
            env = {"GIT_INDEX_FILE": index}
            try:
                self.git("read-tree", base, env=env)
                for path, data in clean.items():
                    if data is None:
                        self.git("update-index", "--force-remove", "--", path, env=env)
                        continue
                    sha = self.git("hash-object", "-w", "--stdin", input=data).strip()
                    existing = self.git("ls-tree", base, "--", path).split()
                    mode = existing[0] if existing and existing[1] == "blob" else "100644"
                    self.git("update-index", "--add", "--cacheinfo", f"{mode},{sha},{path}", env=env)
                tree = self.git("write-tree", env=env).strip()
            finally:
                Path(index).unlink(missing_ok=True)
            if tree == base_tree:
                raise ChangeError("the proposed files are identical to the current configuration")
            pid = f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
            message = f"{title}\n\n{summary.strip()}\n\nFoyelle-Proposal: {pid}\n".encode()
            commit = self.git("commit-tree", tree, "-p", base, "-F", "-", input=message).strip()
            self.git("update-ref", f"refs/heads/proposal/{pid}", commit, ZERO_SHA)
            meta = self._save({
                "id": pid, "title": title, "summary": summary.strip(), "branch": f"proposal/{pid}",
                "commit": commit, "base": base, "files": sorted(clean), "state": "proposed",
                "created": _now(), "out_path": None, "boot_level": None, "boot_changes": [],
                "build_log": None,
            })
        stat = self.git("diff", "--stat", base, commit)
        return self.load(meta["id"]) | {"diffstat": stat}

    def _build_argv(self, meta: dict[str, Any]) -> list[str]:
        values = {"repo": str(self.config.repo), "ref": meta["branch"], "rev": meta["commit"], "host": self.config.host}
        values["flake"] = self.config.flake_template.format(**values)
        return [arg.format(**values) for arg in self.config.build_command]

    async def _run_build(self, proposal_id: str, argv: list[str]) -> None:
        result = await self._build_once(argv)
        await asyncio.to_thread(self._update, proposal_id, **result)

    async def _build_once(self, argv: list[str]) -> dict[str, Any]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL
            )
        except OSError as exc:
            return {"state": "build_failed", "build_log": f"cannot start build: {exc}"}
        try:
            out, err = await asyncio.wait_for(proc.communicate(), self.config.build_timeout)
        except (TimeoutError, asyncio.CancelledError):
            proc.kill()
            await proc.wait()
            return {"state": "build_failed", "build_log": "build timed out or was cancelled"}
        log = _tail(err.decode(errors="replace"))
        paths = [ln.strip() for ln in out.decode(errors="replace").splitlines() if ln.strip().startswith("/")]
        if proc.returncode != 0 or not paths:
            return {"state": "build_failed", "build_log": log or f"exit status {proc.returncode}"}
        changes = await asyncio.to_thread(boot_changes, self.config.current_system, Path(paths[-1]))
        return {"state": "built", "out_path": paths[-1], "build_log": log, "boot_changes": changes,
                "boot_level": bool(changes), "built": _now()}

    async def build(self, proposal_id: str, wait_seconds: float | None = None, rebuild: bool = False) -> dict[str, Any]:
        meta = await asyncio.to_thread(self.load, proposal_id)
        if meta["state"] in ("applied", "rejected", "rolled_back"):
            raise ChangeError(f"proposal is {meta['state']}")
        if meta["stale"]:
            raise ChangeError("the configuration changed since this proposal was made; propose it again")
        task = self._builds.get(proposal_id)
        if meta["state"] == "built" and not rebuild:
            return meta
        if task is None or task.done():
            await asyncio.to_thread(self._update, proposal_id, state="building", out_path=None, build_log=None)
            task = asyncio.create_task(self._run_build(proposal_id, self._build_argv(meta)))
            self._builds[proposal_id] = task
        wait = self.config.build_wait if wait_seconds is None else max(0.0, min(wait_seconds, self.config.build_wait))
        await asyncio.wait({task}, timeout=wait)
        return await asyncio.to_thread(self.load, proposal_id)

    def _run(self, argv: list[str], timeout: float) -> tuple[int, str, str]:
        try:
            proc = subprocess.run(argv, capture_output=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            return 127, "", f"{argv[0]} not found"
        except subprocess.TimeoutExpired:
            return 124, "", f"{argv[0]} timed out"
        return proc.returncode, proc.stdout.decode(errors="replace"), proc.stderr.decode(errors="replace")

    def diff(self, proposal_id: str) -> dict[str, Any]:
        meta = self.load(proposal_id)
        base = f"refs/heads/{self.config.base_branch}"
        text = self.git("diff", f"{base}..refs/heads/{meta['branch']}")
        stat = self.git("diff", "--stat", f"{base}..refs/heads/{meta['branch']}")
        package_diff = None
        if meta.get("out_path"):
            values = {"current": str(self.config.current_system), "out": meta["out_path"]}
            code, out, err = self._run([a.format(**values) for a in self.config.nvd_command], self.config.command_timeout)
            package_diff = out if code == 0 else f"(nvd failed: {err.strip()[:500]})"
        return {
            "proposal": meta,
            "diff": text[:MAX_DIFF],
            "diff_truncated": len(text) > MAX_DIFF,
            "diffstat": stat,
            "package_diff": package_diff,
            "boot_level": meta.get("boot_level"),
            "boot_changes": meta.get("boot_changes", []),
        }

    def _helper(self, request: dict[str, Any]) -> dict[str, Any]:
        """Send one JSON line to the apply helper and read one JSON line back."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.config.apply_timeout)
        try:
            sock.connect(str(self.config.apply_socket))
            sock.sendall(json.dumps(request).encode() + b"\n")
            buf = b""
            while b"\n" not in buf and len(buf) < 65536:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
        except (OSError, TimeoutError) as exc:
            raise ChangeError(f"apply helper unavailable: {exc}") from None
        finally:
            sock.close()
        try:
            reply = json.loads(buf.split(b"\n", 1)[0])
        except ValueError:
            raise ChangeError("apply helper sent an invalid reply") from None
        if not isinstance(reply, dict):
            raise ChangeError("apply helper sent an invalid reply")
        return reply

    def apply(self, proposal_id: str, approval_token: str) -> dict[str, Any]:
        if not approval_token:
            raise ChangeError("missing approval token: the harness adds it when the user approves this change")
        meta = self.load(proposal_id)
        if meta["state"] != "built" or not meta.get("out_path"):
            raise ChangeError(f"only a built proposal can be applied (state: {meta['state']})")
        if meta["stale"]:
            raise ChangeError("the configuration changed since this proposal was made; propose it again")
        if not _SHA_RE.match(meta["commit"]) or self._rev(f"refs/heads/{meta['branch']}") != meta["commit"]:
            raise ChangeError("proposal branch does not match its recorded commit")
        # The helper rebuilds and re-checks everything itself; out_path and boot are advisory.
        request = {"proposal_id": proposal_id, "commit": meta["commit"], "out_path": meta["out_path"],
                   "boot": bool(meta.get("boot_level")), "approval_token": approval_token}
        self._update(proposal_id, state="applying")
        try:
            reply = self._helper(request)
        except ChangeError as exc:
            self._update(proposal_id, state="built", apply_error=str(exc))
            raise
        return {"proposal": self._record_reply(meta, reply), "helper": reply}

    def _record_reply(self, meta: dict[str, Any], reply: dict[str, Any]) -> dict[str, Any]:
        """Record what the apply helper did with a proposal."""
        proposal_id, status = meta["id"], reply.get("status")
        if reply.get("ok") and status in ("switched", "boot-scheduled"):
            with self._lock:
                self._advance_base(meta["commit"], meta["base"])
            return self._update(proposal_id, state="applied", applied=_now(), apply_status=status,
                                generation=reply.get("generation"))
        if status == "rolled-back":
            return self._update(proposal_id, state="rolled_back", apply_status=status, apply_error=reply.get("message"))
        if status == "refused" or reply.get("refused"):
            # Nothing was attempted (for example an invalid or expired approval token): the
            # proposal stays built so a valid approval can still apply it.
            return self._update(proposal_id, state="built", apply_status=status, apply_error=reply.get("message"))
        return self._update(proposal_id, state="apply_failed", apply_status=status, apply_error=reply.get("message"))

    def reconcile(self) -> None:
        """A proposal left "applying" lost the helper's reply: a switch restarts this connector
        while the helper is still applying. Record the helper's last result for it."""
        applying = [p for p in self.proposals() if p["state"] == "applying"]
        if not applying:
            return
        try:
            last = (self._helper({"status": True}) or {}).get("last") or {}
        except ChangeError:
            return
        for meta in applying:
            if last.get("proposal_id") != meta["id"]:
                continue
            try:
                self._record_reply(self.load(meta["id"]), last)
            except ChangeError as exc:
                # The system did switch; keep that fact and say what is left to do.
                self._update(meta["id"], state="applied", apply_status=last.get("status"),
                             generation=last.get("generation"), apply_error=str(exc))

    def _advance_base(self, commit: str, expected_base: str) -> None:
        """Move the base branch to the applied commit (fast-forward only)."""
        branch = self.config.base_branch
        head = ""
        try:
            head = self.git("symbolic-ref", "--quiet", "HEAD").strip()
        except ChangeError:
            pass
        bare = self.git("rev-parse", "--is-bare-repository").strip() == "true"
        current = self._rev(f"refs/heads/{branch}")
        if current != expected_base and self._rev(f"{commit}") and not self._is_ancestor(current, commit):
            # Main moved on while the change was applied (a release update commits there): merge
            # the applied commit instead of fast-forwarding. The two touch different files.
            if head == f"refs/heads/{branch}" and not bare:
                try:
                    self.git("merge", "--no-ff", "--no-edit", "--quiet", "-m", f"Applied {commit[:12]}", commit)
                except ChangeError:
                    self.git("merge", "--abort")
                    raise ChangeError(f"applied {commit[:12]}, but it does not merge into {branch}; resolve by hand")
                return
            raise ChangeError(f"applied {commit[:12]}, but {branch} moved on; merge it by hand")
        if head == f"refs/heads/{branch}" and not bare:
            self.git("merge", "--ff-only", "--quiet", commit)
        else:
            self.git("update-ref", f"refs/heads/{branch}", commit, expected_base)

    def _is_ancestor(self, older: str | None, newer: str) -> bool:
        if not older:
            return True
        try:
            self.git("merge-base", "--is-ancestor", older, newer)
            return True
        except ChangeError:
            return False

    def reject(self, proposal_id: str, reason: str | None = None) -> dict[str, Any]:
        meta = self.load(proposal_id)
        if meta["state"] in ("applied", "applying"):
            raise ChangeError(f"proposal is {meta['state']}; use rollback instead")
        return self._update(proposal_id, state="rejected", rejected=_now(), reject_reason=reason)

    def rollback(self, approval_token: str, generation: int | None = None) -> dict[str, Any]:
        if not approval_token:
            raise ChangeError("missing approval token: the harness adds it when the user approves this change")
        if generation is not None and generation < 1:
            raise ChangeError("generation must be a positive number")
        return {"helper": self._helper({"rollback": generation, "approval_token": approval_token})}

    def disk_status(self) -> dict[str, Any]:
        """Disk encryption state from the apply helper (it alone may read the LUKS header)."""
        reply = self._helper({"disk": "status"})
        if not reply.get("ok"):
            raise ChangeError(reply.get("error") or "disk status unavailable")
        out = {k: reply.get(k) for k in ("encrypted", "initial_passphrase", "prompt_open") if k in reply}
        if out.get("initial_passphrase"):
            out["warning"] = ("The disk still opens with the public initial passphrase: anyone with this "
                              "machine's disk can read it. Change the disk passphrase.")
        return out

    def disk_passphrase(self, approval_token: str) -> dict[str, Any]:
        if not approval_token:
            raise ChangeError("missing approval token: the harness adds it when the user approves this change")
        return {"helper": self._helper({"disk": "change-passphrase", "approval_token": approval_token})}

    def recovery_status(self) -> dict[str, Any]:
        reply = self._helper({"recovery": "status"})
        if not reply.get("ok"):
            raise ChangeError(reply.get("error") or "recovery status unavailable")
        return {k: reply.get(k) for k in ("enabled", "password_set", "prompt_open") if k in reply}

    def recovery_password(self, approval_token: str) -> dict[str, Any]:
        if not approval_token:
            raise ChangeError("missing approval token: the harness adds it when the user approves this change")
        return {"helper": self._helper({"recovery": "set-password", "approval_token": approval_token})}

    def generations(self, limit: int = 10) -> list[dict[str, Any]]:
        current = os.path.realpath(self.config.current_system)
        gens = []
        for link in self.config.profiles_dir.glob("system-*-link"):
            m = re.match(r"system-(\d+)-link$", link.name)
            if not m:
                continue
            target = os.path.realpath(link)
            gens.append({"generation": int(m.group(1)), "path": target, "current": target == current,
                         "date": datetime.fromtimestamp(link.lstat().st_mtime, UTC).isoformat(timespec="seconds")})
        gens.sort(key=lambda g: g["generation"], reverse=True)
        return gens[:limit]

    def _disk_summary(self) -> dict[str, Any]:
        try:
            return self.disk_status()
        except ChangeError as exc:
            return {"error": str(exc)}

    def _recovery_summary(self) -> dict[str, Any]:
        try:
            return self.recovery_status()
        except ChangeError as exc:
            return {"error": str(exc)}

    def status(self) -> dict[str, Any]:
        self.reconcile()
        branch = self.config.base_branch
        head = self._rev(f"refs/heads/{branch}")
        subject = self.git("log", "-1", "--format=%s", head).strip() if head else None
        branches = self.git("for-each-ref", "--format=%(refname:short)", "refs/heads/proposal/").split()
        gens = self.generations()
        proposals = self.proposals()
        return {
            "host": self.config.host,
            "base_branch": branch,
            "base_commit": head,
            "base_subject": subject,
            "current_system": os.path.realpath(self.config.current_system),
            "current_generation": next((g["generation"] for g in gens if g["current"]), None),
            "generations": gens,
            "proposal_branches": branches,
            # One line per proposal (diff shows the full one): build logs and package diffs made this
            # answer over 100 KB, most of the model's context.
            "pending": [_brief(p) for p in proposals if p["state"] in ("proposed", "building", "built", "build_failed", "apply_failed")],
            "recent": [_brief(p) for p in proposals],
            "disk": self._disk_summary(),
            "recovery": self._recovery_summary(),
            "checked": time.time(),
        }
