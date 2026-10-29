#!/usr/bin/env python3
"""Foyelle apply helper: the only privileged step of an agent-driven system change.

Runs as root (systemd unit ``foyelle-apply``) on the Unix socket ``/run/foyelle/apply.sock``, which
only the System connector may open. One request per connection, one JSON object per line:

  {"proposal_id": "p-…", "commit": "<40 hex>", "approval_token": "…"}   apply a proposal
  {"rollback": null | <generation>, "approval_token": "…"}               roll back
  {"status": true}                                                       last result, current generation
  {"disk": "status"}                                                     is the root encrypted, does the
                                                                         public initial passphrase still open it
  {"disk": "change-passphrase", "approval_token": "…"}                   open the passphrase prompt on its
                                                                         own console (the passphrase never
                                                                         passes through this helper)
  {"recovery": "status"} / {"recovery": "set-password", "approval_token": "…"}
                                                                         the same for the recovery
                                                                         console's password (passwd)

It never trusts the connector's word about what to switch to. For an apply it checks the one-time
approval token the harness wrote when the user approved (bound to the proposal), checks the
commit is the head of ``proposal/<id>`` in the config repo, builds that commit itself, decides
whether the change is boot-level (kernel, initrd or bootloader), switches (or installs it as the
next boot entry), runs the health check and rolls back automatically when it fails.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PROPOSAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
MAX_REQUEST = 4096


@dataclass
class Config:
    repo: Path = Path(os.environ.get("FOYELLE_CONFIG_REPO", "/var/lib/foyelle/config"))
    host: str = os.environ.get("FOYELLE_HOST", "foyelle")
    approvals: Path = Path(os.environ.get("FOYELLE_APPROVALS_DIR", "/run/foyelle/approvals"))
    state: Path = Path(os.environ.get("FOYELLE_APPLY_STATE", "/var/lib/foyelle/apply"))
    profile: str = os.environ.get("FOYELLE_SYSTEM_PROFILE", "/nix/var/nix/profiles/system")
    current: str = os.environ.get("FOYELLE_CURRENT_SYSTEM", "/run/current-system")
    health_units: list[str] = field(default_factory=lambda: os.environ.get(
        "FOYELLE_HEALTH_UNITS", "foyelle-harness.service cage-tty1.service").split())
    health_timeout: float = float(os.environ.get("FOYELLE_HEALTH_TIMEOUT", "60"))
    # For tests: a command that prints the out path for a commit instead of building it.
    build_cmd: str = os.environ.get("FOYELLE_APPLY_BUILD_CMD", "")
    disk_device: str = os.environ.get("FOYELLE_DISK_DEVICE", "/dev/disk/by-partlabel/foyelle-root")
    disk_initial_key: Path = Path(os.environ.get("FOYELLE_DISK_INITIAL_KEY", "/etc/foyelle/disk-initial-passphrase"))
    disk_unit: str = os.environ.get("FOYELLE_DISK_UNIT", "foyelle-disk-passphrase.service")
    recovery_user: str = os.environ.get("FOYELLE_RECOVERY_USER", "recovery")
    recovery_unit: str = os.environ.get("FOYELLE_RECOVERY_UNIT", "foyelle-recovery-password.service")


class Refused(Exception):
    """A request the helper will not carry out; the message goes back to the connector."""


def run(argv: list[str], *, timeout: float = 600, cwd: Optional[Path] = None) -> str:
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"{argv[0]} failed ({proc.returncode}): {proc.stderr.strip()[-2000:]}")
    return proc.stdout


# ── approval tokens ────────────────────────────────────────────────────────────────────────────

def consume_token(cfg: Config, token: object, *, tool: str, proposal_id: Optional[str] = None) -> dict:
    """Check and delete a one-time approval token. Bound to the tool and, for apply, the proposal."""
    if not isinstance(token, str) or not TOKEN_RE.match(token):
        raise Refused("missing or malformed approval token")
    path = cfg.approvals / token
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refused("unknown approval token") from None
    finally:
        # Single use, whatever happens next.
        try:
            path.unlink()
        except OSError:
            pass
    if body.get("tool") != tool:
        raise Refused(f"approval token is for {body.get('tool')!r}, not {tool!r}")
    if float(body.get("expires") or 0) < time.time():
        raise Refused("approval token expired")
    if proposal_id is not None and body.get("proposal_id") != proposal_id:
        raise Refused("approval token is for a different proposal")
    return body


# ── system profile helpers ─────────────────────────────────────────────────────────────────────

def resolve(path: str) -> str:
    return os.path.realpath(path)


def boot_level(cfg: Config, out_path: str) -> bool:
    """A change is boot-level when the kernel, initrd or bootloader configuration differs."""
    current = resolve(cfg.current)
    for name in ("kernel", "initrd", "kernel-params"):
        a, b = Path(current, name), Path(out_path, name)
        if a.exists() != b.exists():
            return True
        if a.is_symlink() or b.is_symlink():
            if resolve(str(a)) != resolve(str(b)):
                return True
        elif a.is_file() and b.is_file() and a.read_bytes() != b.read_bytes():
            return True
    return False


def generation(cfg: Config) -> Optional[int]:
    target = os.readlink(cfg.profile) if os.path.islink(cfg.profile) else ""
    match = re.search(r"-(\d+)-link$", target)
    return int(match.group(1)) if match else None


def set_profile(cfg: Config, out_path: str) -> None:
    run(["nix-env", "--profile", cfg.profile, "--set", out_path], timeout=120)


def switch(out_path: str, action: str) -> None:
    run([f"{out_path}/bin/switch-to-configuration", action], timeout=900)


def healthy(cfg: Config, sleep: Callable[[float], None] = time.sleep) -> bool:
    deadline = time.monotonic() + cfg.health_timeout
    while True:
        states = []
        for unit in cfg.health_units:
            proc = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, check=False)
            states.append(proc.stdout.strip())
        if all(s == "active" for s in states):
            return True
        if time.monotonic() >= deadline or any(s == "failed" for s in states):
            return False
        sleep(2)


# ── requests ───────────────────────────────────────────────────────────────────────────────────

def build(cfg: Config, commit: str) -> str:
    if cfg.build_cmd:
        out = run([*shlex.split(cfg.build_cmd), commit], timeout=3600)
    else:
        # Build exactly that commit, exported by git into a private folder: Nix reading the repo
        # itself (git+file:) refuses it, since it belongs to the System connector's user.
        with tempfile.TemporaryDirectory(prefix="foyelle-apply-") as tmp:
            src = Path(tmp) / "config"
            src.mkdir()
            archive = Path(tmp) / "config.tar"
            run(["git", "-C", str(cfg.repo), "archive", "--format=tar", "-o", str(archive), commit], timeout=120)
            run(["tar", "-xf", str(archive), "-C", str(src)], timeout=120)
            flake = f"path:{src}#nixosConfigurations.{cfg.host}.config.system.build.toplevel"
            out = run(["nix", "--extra-experimental-features", "nix-command flakes", "build", "--no-link",
                       "--print-out-paths", flake], timeout=3600)
    out_path = out.strip().splitlines()[-1] if out.strip() else ""
    if not out_path.startswith("/nix/store/") or not Path(out_path, "bin", "switch-to-configuration").exists():
        raise RuntimeError(f"build did not produce a NixOS system: {out_path!r}")
    return out_path


def apply(cfg: Config, req: dict) -> dict:
    proposal_id, commit = req.get("proposal_id"), req.get("commit")
    if not isinstance(proposal_id, str) or not PROPOSAL_RE.match(proposal_id):
        raise Refused("malformed proposal_id")
    if not isinstance(commit, str) or not SHA_RE.match(commit):
        raise Refused("commit must be a full 40-character hash")
    consume_token(cfg, req.get("approval_token"), tool="system.apply", proposal_id=proposal_id)
    head = run(["git", "-C", str(cfg.repo), "rev-parse", f"refs/heads/proposal/{proposal_id}"], timeout=30).strip()
    if head != commit:
        raise Refused(f"{commit[:12]} is not the head of proposal/{proposal_id} ({head[:12]})")

    previous_path, previous_gen = resolve(cfg.profile), generation(cfg)
    out_path = build(cfg, commit)
    boot = boot_level(cfg, out_path)
    set_profile(cfg, out_path)
    result = {"ok": True, "proposal_id": proposal_id, "commit": commit, "out_path": out_path,
              "boot_level": boot, "generation": generation(cfg), "previous_generation": previous_gen}
    if boot:
        # Kernel/initrd/bootloader: install as the next boot entry. systemd-boot boot counting
        # falls back to the previous entry if the new one does not reach boot-complete.target.
        switch(out_path, "boot")
        result.update(status="boot-scheduled", health="pending-reboot", rolled_back=False)
        return record(cfg, result)
    try:
        switch(out_path, "switch")
        ok = healthy(cfg)
    except Exception as exc:  # a failed activation counts as unhealthy
        result["error"] = str(exc)
        ok = False
    if ok:
        result.update(status="switched", health="ok", rolled_back=False)
        return record(cfg, result)
    # Automatic rollback to the generation that was running before.
    set_profile(cfg, previous_path)
    switch(previous_path, "switch")
    result.update(ok=False, status="rolled-back", health="failed", rolled_back=True, generation=generation(cfg),
                  message="the new system was unhealthy; rolled back to the previous generation")
    return record(cfg, result)


def rollback(cfg: Config, req: dict) -> dict:
    body = consume_token(cfg, req.get("approval_token"), tool="system.rollback")
    target = req.get("rollback")
    if target is not None and (not isinstance(target, int) or target < 1):
        raise Refused("rollback target must be a generation number")
    if body.get("generation") not in (None, target):
        raise Refused("approval token is for a different generation")
    if target is None:
        run(["nix-env", "--profile", cfg.profile, "--rollback"], timeout=120)
    else:
        run(["nix-env", "--profile", cfg.profile, "--switch-generation", str(target)], timeout=120)
    out_path = resolve(cfg.profile)
    switch(out_path, "switch")
    return record(cfg, {"ok": True, "status": "rolled-back", "rolled_back": True, "generation": generation(cfg),
                        "out_path": out_path,
                        "health": "ok" if healthy(cfg) else "failed"})


def disk_encrypted(cfg: Config) -> bool:
    return subprocess.run(["cryptsetup", "isLuks", cfg.disk_device], capture_output=True,
                          check=False).returncode == 0


def initial_passphrase_works(cfg: Config) -> Optional[bool]:
    """Whether the public initial passphrase still opens a key slot (None: not known here)."""
    if not cfg.disk_initial_key.is_file():
        return None
    # Opens nothing: --test-passphrase only checks that the key unlocks a key slot.
    return subprocess.run(["cryptsetup", "open", "--test-passphrase", "--key-file", str(cfg.disk_initial_key),
                           cfg.disk_device], capture_output=True, check=False, timeout=120).returncode == 0


def unit_active(unit: str) -> bool:
    proc = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, check=False)
    return proc.stdout.strip() in ("active", "activating")


def disk(cfg: Config, req: dict) -> dict:
    action = req.get("disk")
    if action == "status":
        if not disk_encrypted(cfg):
            return {"ok": True, "encrypted": False}
        return {"ok": True, "encrypted": True, "initial_passphrase": initial_passphrase_works(cfg),
                "prompt_open": unit_active(cfg.disk_unit)}
    if action == "change-passphrase":
        consume_token(cfg, req.get("approval_token"), tool="system.disk_passphrase")
        if not disk_encrypted(cfg):
            raise Refused("the disk is not encrypted")
        run(["systemctl", "start", "--no-block", cfg.disk_unit], timeout=30)
        return {"ok": True, "status": "prompt-open",
                "message": "The passphrase prompt is open on the screen. Type the current passphrase, "
                           "then the new one twice; Foyelle comes back when you are done."}
    raise Refused("unknown disk request")


def recovery_password_set(cfg: Config) -> Optional[bool]:
    """Whether the recovery user has a usable password (passwd -S: P set, L locked, NP none)."""
    proc = subprocess.run(["passwd", "-S", cfg.recovery_user], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return None
    fields = proc.stdout.split()
    return len(fields) > 1 and fields[1] == "P"


def recovery(cfg: Config, req: dict) -> dict:
    action = req.get("recovery")
    if action == "status":
        password = recovery_password_set(cfg)
        return {"ok": True, "enabled": password is not None, "password_set": bool(password),
                "prompt_open": unit_active(cfg.recovery_unit)}
    if action == "set-password":
        consume_token(cfg, req.get("approval_token"), tool="system.recovery_password")
        if recovery_password_set(cfg) is None:
            raise Refused("this machine has no recovery console")
        run(["systemctl", "start", "--no-block", cfg.recovery_unit], timeout=30)
        return {"ok": True, "status": "prompt-open",
                "message": "The password prompt is open on the screen. Type the new recovery password "
                           "twice; Foyelle comes back when you are done."}
    raise Refused("unknown recovery request")


def record(cfg: Config, result: dict) -> dict:
    result["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cfg.state.mkdir(parents=True, exist_ok=True)
    (cfg.state / "last.json").write_text(json.dumps(result, indent=1) + "\n")
    with open(cfg.state / "history.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(result) + "\n")
    return result


def handle(cfg: Config, req: object) -> dict:
    if not isinstance(req, dict):
        return {"ok": False, "error": "request must be a JSON object"}
    try:
        if req.get("status"):
            last = cfg.state / "last.json"
            return {"ok": True, "generation": generation(cfg), "current": resolve(cfg.current),
                    "last": json.loads(last.read_text()) if last.exists() else None}
        if "disk" in req:
            return disk(cfg, req)
        if "recovery" in req:
            return recovery(cfg, req)
        if "rollback" in req:
            return rollback(cfg, req)
        return apply(cfg, req)
    except Refused as exc:
        return {"ok": False, "status": "refused", "refused": True, "error": str(exc), "message": str(exc)}
    except Exception as exc:
        return {"ok": False, "status": "failed", "error": str(exc), "message": str(exc)}


def serve(cfg: Config) -> None:
    """Accept connections on the socket systemd passes in (socket activation, fd 3)."""
    listener = socket.socket(fileno=3) if os.environ.get("LISTEN_FDS") == "1" else None
    if listener is None:
        raise SystemExit("foyelle-apply expects a systemd socket (LISTEN_FDS=1)")
    listener.settimeout(None)
    while True:
        conn, _ = listener.accept()
        with conn:
            conn.settimeout(30)
            data = b""
            try:
                while b"\n" not in data and len(data) < MAX_REQUEST:
                    chunk = conn.recv(1024)
                    if not chunk:
                        break
                    data += chunk
                req = json.loads(data.split(b"\n", 1)[0] or b"null")
            except (OSError, ValueError):
                req = None
            conn.settimeout(None)
            reply = handle(cfg, req)
            try:
                conn.sendall((json.dumps(reply) + "\n").encode())
            except OSError:
                pass


if __name__ == "__main__":
    serve(Config())
    sys.exit(0)
