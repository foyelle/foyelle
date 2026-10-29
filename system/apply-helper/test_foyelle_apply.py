import json
import os
import subprocess
import time
from pathlib import Path

import pytest

import foyelle_apply as fa


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    repo = tmp_path / "config"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.org")
    _git(repo, "config", "user.name", "t")
    (repo / "configuration.nix").write_text("{ }\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "checkout", "-qb", "proposal/p1")
    (repo / "configuration.nix").write_text("{ services.foo.enable = true; }\n")
    _git(repo, "commit", "-qam", "enable foo")
    commit = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")

    def system(name, kernel):
        root = tmp_path / "store" / name
        (root / "bin").mkdir(parents=True)
        (root / "bin" / "switch-to-configuration").write_text("#!/bin/sh\necho \"$0 $1\" >> \"$SWITCH_LOG\"\n")
        (root / "bin" / "switch-to-configuration").chmod(0o755)
        (root / "kernel").write_text(kernel)
        (root / "initrd").write_text("initrd")
        return root

    old, new_same_kernel, new_kernel = system("old", "k1"), system("new", "k1"), system("newk", "k2")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "system-1-link").symlink_to(old)
    (profiles / "system").symlink_to("system-1-link")
    current = tmp_path / "current-system"
    current.symlink_to(old)

    state = {"build": str(new_same_kernel), "healthy": True}
    calls = []
    monkeypatch.setenv("SWITCH_LOG", str(tmp_path / "switch.log"))

    def fake_run(argv, *, timeout=600, cwd=None):
        calls.append(argv)
        if argv[0] == "nix-env":
            profile = Path(argv[2])
            links = sorted(profiles.glob("system-*-link"))
            if argv[3] == "--set":
                n = len(links) + 1
                (profiles / f"system-{n}-link").symlink_to(argv[4])
                profile.unlink()
                profile.symlink_to(f"system-{n}-link")
            elif argv[3] == "--rollback":
                cur = int(os.readlink(profile).split("-")[1])
                profile.unlink()
                profile.symlink_to(f"system-{cur - 1}-link")
            return ""
        if argv[0].endswith("switch-to-configuration"):
            subprocess.run(argv, check=True)
            return ""
        return subprocess.run(argv, check=True, capture_output=True, text=True).stdout

    monkeypatch.setattr(fa, "run", fake_run)
    monkeypatch.setattr(fa, "healthy", lambda cfg, sleep=None: state["healthy"])
    monkeypatch.setattr(fa, "build", lambda cfg, commit: state["build"])
    cfg = fa.Config(repo=repo, host="foyelle", approvals=tmp_path / "approvals", state=tmp_path / "state",
                    profile=str(profiles / "system"), current=str(current), health_units=["x.service"],
                    health_timeout=1)
    cfg.approvals.mkdir()
    return type("Env", (), dict(cfg=cfg, commit=commit, state=state, calls=calls, tmp=tmp_path,
                                new_kernel=str(new_kernel), old=str(old), profiles=profiles))


def token(env, tool="system.apply", proposal_id="p1", expires=None, generation=None):
    name = "tok" + os.urandom(12).hex()
    body = {"tool": tool, "proposal_id": proposal_id, "generation": generation,
            "expires": expires if expires is not None else time.time() + 60}
    (env.cfg.approvals / name).write_text(json.dumps(body))
    return name


def test_apply_switches_and_reports_health(env):
    reply = fa.handle(env.cfg, {"proposal_id": "p1", "commit": env.commit, "approval_token": token(env)})
    assert reply["ok"] and reply["health"] == "ok" and not reply["boot_level"]
    assert reply["generation"] == 2 and reply["previous_generation"] == 1
    assert "switch" in (env.tmp / "switch.log").read_text()
    assert json.loads((env.cfg.state / "last.json").read_text())["commit"] == env.commit


def test_unhealthy_switch_rolls_back(env):
    env.state["healthy"] = False
    reply = fa.handle(env.cfg, {"proposal_id": "p1", "commit": env.commit, "approval_token": token(env)})
    assert not reply["ok"] and reply["rolled_back"] and reply["health"] == "failed"
    assert os.path.realpath(env.cfg.profile) == os.path.realpath(env.old)


def test_boot_level_change_is_installed_for_next_boot(env):
    env.state["build"] = env.new_kernel
    reply = fa.handle(env.cfg, {"proposal_id": "p1", "commit": env.commit, "approval_token": token(env)})
    assert reply["ok"] and reply["boot_level"] and reply["health"] == "pending-reboot"
    assert (env.tmp / "switch.log").read_text().split()[-1] == "boot"


def test_tokens_are_single_use(env):
    tok = token(env)
    assert fa.handle(env.cfg, {"proposal_id": "p1", "commit": env.commit, "approval_token": tok})["ok"]
    again = fa.handle(env.cfg, {"proposal_id": "p1", "commit": env.commit, "approval_token": tok})
    assert again["refused"] and "unknown" in again["error"]


@pytest.mark.parametrize("bad", [
    {"approval_token": None},
    {"approval_token": "../../etc/passwd"},
    {"commit": "abc"},
    {"proposal_id": "../x"},
])
def test_malformed_requests_are_refused(env, bad):
    req = {"proposal_id": "p1", "commit": env.commit, "approval_token": token(env), **bad}
    reply = fa.handle(env.cfg, req)
    assert reply["ok"] is False and reply.get("refused")


def test_token_for_another_proposal_is_refused(env):
    reply = fa.handle(env.cfg, {"proposal_id": "p1", "commit": env.commit, "approval_token": token(env, proposal_id="p2")})
    assert reply["refused"] and "different proposal" in reply["error"]


def test_expired_token_is_refused(env):
    reply = fa.handle(env.cfg, {"proposal_id": "p1", "commit": env.commit,
                                "approval_token": token(env, expires=time.time() - 1)})
    assert reply["refused"] and "expired" in reply["error"]


def test_commit_must_be_the_proposal_head(env):
    main = _git(env.cfg.repo, "rev-parse", "main")
    reply = fa.handle(env.cfg, {"proposal_id": "p1", "commit": main, "approval_token": token(env)})
    assert reply["refused"] and "not the head" in reply["error"]


def test_rollback_needs_its_own_token(env):
    fa.handle(env.cfg, {"proposal_id": "p1", "commit": env.commit, "approval_token": token(env)})
    refused = fa.handle(env.cfg, {"rollback": None, "approval_token": token(env)})  # an apply token
    assert refused["refused"]
    reply = fa.handle(env.cfg, {"rollback": None, "approval_token": token(env, tool="system.rollback")})
    assert reply["ok"] and reply["generation"] == 1


def test_status(env):
    assert fa.handle(env.cfg, {"status": True})["generation"] == 1


def test_boot_level_detects_kernel_change(env):
    assert fa.boot_level(env.cfg, env.new_kernel)
    assert not fa.boot_level(env.cfg, env.old)


@pytest.fixture()
def luks(env, monkeypatch):
    disk = {"encrypted": True, "initial": True, "active": False}
    monkeypatch.setattr(fa, "disk_encrypted", lambda cfg: disk["encrypted"])
    monkeypatch.setattr(fa, "initial_passphrase_works", lambda cfg: disk["initial"])
    monkeypatch.setattr(fa, "unit_active", lambda unit: disk["active"])
    inner = fa.run

    def run(argv, **kw):
        if argv[0] == "systemctl":
            env.calls.append(argv)
            return ""
        return inner(argv, **kw)

    monkeypatch.setattr(fa, "run", run)
    return disk


def test_disk_status_reports_the_initial_passphrase(env, luks):
    assert fa.handle(env.cfg, {"disk": "status"}) == {
        "ok": True, "encrypted": True, "initial_passphrase": True, "prompt_open": False}
    luks.update(initial=False, active=True)
    reply = fa.handle(env.cfg, {"disk": "status"})
    assert reply["initial_passphrase"] is False and reply["prompt_open"] is True
    luks["encrypted"] = False
    assert fa.handle(env.cfg, {"disk": "status"}) == {"ok": True, "encrypted": False}


def test_passphrase_prompt_needs_its_own_token(env, luks):
    refused = fa.handle(env.cfg, {"disk": "change-passphrase", "approval_token": token(env)})  # an apply token
    assert refused["refused"]
    assert not any(c[:2] == ["systemctl", "start"] for c in env.calls)
    reply = fa.handle(env.cfg, {"disk": "change-passphrase",
                                "approval_token": token(env, tool="system.disk_passphrase", proposal_id=None)})
    assert reply["ok"] and reply["status"] == "prompt-open"
    assert ["systemctl", "start", "--no-block", env.cfg.disk_unit] in env.calls


def test_passphrase_prompt_refused_without_encryption(env, luks):
    luks["encrypted"] = False
    reply = fa.handle(env.cfg, {"disk": "change-passphrase",
                                "approval_token": token(env, tool="system.disk_passphrase", proposal_id=None)})
    assert reply["refused"] and "not encrypted" in reply["error"]


def test_unknown_disk_request_is_refused(env, luks):
    assert fa.handle(env.cfg, {"disk": "format"})["refused"]


def test_recovery_status_and_prompt(env, luks, monkeypatch):
    state = {"password": False}
    monkeypatch.setattr(fa, "recovery_password_set", lambda cfg: state["password"])
    assert fa.handle(env.cfg, {"recovery": "status"}) == {
        "ok": True, "enabled": True, "password_set": False, "prompt_open": False}
    refused = fa.handle(env.cfg, {"recovery": "set-password",
                                  "approval_token": token(env, tool="system.disk_passphrase", proposal_id=None)})
    assert refused["refused"]
    reply = fa.handle(env.cfg, {"recovery": "set-password",
                                "approval_token": token(env, tool="system.recovery_password", proposal_id=None)})
    assert reply["status"] == "prompt-open"
    assert ["systemctl", "start", "--no-block", env.cfg.recovery_unit] in env.calls
    state["password"] = None  # no recovery user on this machine
    assert fa.handle(env.cfg, {"recovery": "status"})["enabled"] is False
