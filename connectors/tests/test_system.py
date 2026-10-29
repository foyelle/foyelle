from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import check_surface, hints, mcp_session, serving

from foyelle_connectors.system.server import build_server
from foyelle_connectors.system.service import ChangeError, SystemConfig, SystemService, boot_changes

FAKE_NIX = r'''
import os, pathlib, sys
flake, root, mode = sys.argv[1:4]
if mode == "fail":
    print("error: undefined variable 'pkgs'", file=sys.stderr)
    sys.exit(1)
rev = flake.split("rev=")[1].split("#")[0]
out = pathlib.Path(root) / f"{rev[:12]}-nixos-system-test"
(out / "bin").mkdir(parents=True, exist_ok=True)
kernel = pathlib.Path(root) / ("kernel-B" if mode == "newkernel" else "kernel-A")
for name, target in (("kernel", kernel), ("initrd", pathlib.Path(root) / "initrd")):
    if not (out / name).exists():
        os.symlink(target, out / name)
(out / "kernel-params").write_text("quiet")
(out / "bin" / "switch-to-configuration").write_text("export INSTALL_BOOTLOADER=/nix/store/abc-install-systemd-boot.sh\n")
print("building the system configuration...", file=sys.stderr)
print(out)
'''

FAKE_NVD = r'''
import sys
print(f"<<< {sys.argv[1]}\n>>> {sys.argv[2]}\nVersion changes:\n[U.]  #1  hello  2.10 -> 2.12")
'''


def git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null"}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env).stdout


class FakeHelper:
    """The apply helper's socket: records one JSON request per connection and replies."""

    def __init__(self, path: Path, reply: dict[str, Any]) -> None:
        self.path, self.reply, self.requests = path, reply, []
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(path))
        self.sock.listen()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                data = b""
                while not data.endswith(b"\n"):
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                self.requests.append(json.loads(data))
                conn.sendall(json.dumps(self.reply).encode() + b"\n")

    def close(self) -> None:
        self.sock.close()


@pytest.fixture
def env(tmp_path: Path) -> Iterator[dict[str, Any]]:
    repo = tmp_path / "config"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "flake.nix").write_text("{ outputs = _: {}; }\n")
    (repo / "configuration.nix").write_text("{ pkgs, ... }: { environment.systemPackages = [ ]; }\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "initial")
    store = tmp_path / "store"
    store.mkdir()
    for name in ("kernel-A", "kernel-B", "initrd"):
        (store / name).write_text(name)
    current = store / "current-system"
    (current / "bin").mkdir(parents=True)
    os.symlink(store / "kernel-A", current / "kernel")
    os.symlink(store / "initrd", current / "initrd")
    (current / "kernel-params").write_text("quiet")
    (current / "bin" / "switch-to-configuration").write_text("export INSTALL_BOOTLOADER=/nix/store/abc-install-systemd-boot.sh\n")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (store / "older-system").mkdir()
    os.symlink(store / "older-system", profiles / "system-1-link")
    os.symlink(current, profiles / "system-2-link")
    nix, nvd = tmp_path / "fake_nix.py", tmp_path / "fake_nvd.py"
    nix.write_text(FAKE_NIX)
    nvd.write_text(FAKE_NVD)
    sockdir = Path(tempfile.mkdtemp(prefix="fy"))  # AF_UNIX paths must be short
    cfg = SystemConfig(
        repo=repo, host="foyelle", apply_socket=sockdir / "apply.sock", current_system=current,
        profiles_dir=profiles, build_command=[sys.executable, str(nix), "{flake}", str(store), "ok"],
        nvd_command=[sys.executable, str(nvd), "{current}", "{out}"], build_wait=20,
    )
    yield {"repo": repo, "store": store, "config": cfg, "nix": nix}
    shutil.rmtree(sockdir, ignore_errors=True)


def test_propose_creates_branch_without_touching_main(env: dict[str, Any]) -> None:
    svc, repo = SystemService(env["config"]), env["repo"]
    main_before = git(repo, "rev-parse", "main").strip()
    out = svc.propose("Add hello", "Installs GNU hello for the demo.",
                      {"configuration.nix": "{ pkgs, ... }: { environment.systemPackages = [ pkgs.hello ]; }\n",
                       "modules/extra.nix": "{ }\n", "flake.nix": None})
    assert out["state"] == "proposed" and out["branch"] == f"proposal/{out['id']}"
    assert git(repo, "rev-parse", "main").strip() == main_before
    assert git(repo, "rev-parse", out["branch"]).strip() == out["commit"]
    assert "pkgs.hello" in git(repo, "show", f"{out['branch']}:configuration.nix")
    assert git(repo, "ls-tree", "--name-only", "-r", out["branch"]).split() == ["configuration.nix", "modules/extra.nix"]
    assert "Foyelle-Proposal: " + out["id"] in git(repo, "log", "-1", "--format=%B", out["branch"])
    assert git(repo, "status", "--porcelain") == ""  # working tree untouched
    assert "pkgs.hello" not in (repo / "configuration.nix").read_text()
    for bad in ("../x.nix", "/etc/x", ".git/config", "a/../../b", "a\\b"):
        with pytest.raises(ChangeError):
            svc.propose("Bad", "", {bad: "x"})
    with pytest.raises(ChangeError, match="identical"):
        svc.propose("Noop", "", {"flake.nix": "{ outputs = _: {}; }\n"})


async def test_build_diff_apply(env: dict[str, Any]) -> None:
    svc, repo = SystemService(env["config"]), env["repo"]
    p = svc.propose("Add hello", "Installs hello.", {"configuration.nix": "{ hello = true; }\n"})
    other = svc.propose("Other", "Competing change.", {"flake.nix": "{ outputs = _: { x = 1; }; }\n"})
    with pytest.raises(ChangeError, match="only a built"):
        svc.apply(p["id"], "tok")
    built = await svc.build(p["id"])
    assert built["state"] == "built" and built["out_path"].endswith("-nixos-system-test")
    assert built["boot_level"] is False and built["boot_changes"] == []
    assert "building the system" in built["build_log"]
    d = svc.diff(p["id"])
    assert "+{ hello = true; }" in d["diff"] and "configuration.nix" in d["diffstat"]
    assert "hello  2.10 -> 2.12" in d["package_diff"] and d["boot_level"] is False
    with pytest.raises(ChangeError, match="approval token"):
        svc.apply(p["id"], "")

    helper = FakeHelper(env["config"].apply_socket, {"ok": True, "status": "switched", "generation": 3})
    try:
        result = svc.apply(p["id"], "tok-123")
        assert helper.requests == [{"proposal_id": p["id"], "commit": p["commit"], "out_path": built["out_path"],
                                    "boot": False, "approval_token": "tok-123"}]
        assert len(p["commit"]) == 40
        assert result["proposal"]["state"] == "applied" and result["helper"]["generation"] == 3
        assert git(repo, "rev-parse", "main").strip() == p["commit"]
        assert (repo / "configuration.nix").read_text() == "{ hello = true; }\n"  # fast-forwarded checkout
        assert svc.load(other["id"])["stale"] is True
        with pytest.raises(ChangeError, match="changed since"):
            await svc.build(other["id"])
        rb = svc.rollback("tok-9", generation=2)
        assert helper.requests[-1] == {"rollback": 2, "approval_token": "tok-9"} and rb["helper"]["ok"]
        svc.rollback("tok-10")
        assert helper.requests[-1] == {"rollback": None, "approval_token": "tok-10"}
    finally:
        helper.close()
    status = svc.status()
    assert status["current_generation"] == 2 and [g["generation"] for g in status["generations"]] == [2, 1]
    assert status["base_commit"] == p["commit"] and f"proposal/{other['id']}" in status["proposal_branches"]


async def test_build_failure_and_reject(env: dict[str, Any]) -> None:
    cfg = env["config"]
    cfg.build_command[-1] = "fail"
    svc = SystemService(cfg)
    p = svc.propose("Broken", "Typo.", {"configuration.nix": "{ broken"})
    built = await svc.build(p["id"])
    assert built["state"] == "build_failed" and "undefined variable" in built["build_log"]
    rejected = svc.reject(p["id"], "broken")
    assert rejected["state"] == "rejected"
    with pytest.raises(ChangeError):
        await svc.build(p["id"])


async def test_apply_rolled_back_by_helper(env: dict[str, Any]) -> None:
    svc, repo = SystemService(env["config"]), env["repo"]
    main_before = git(repo, "rev-parse", "main").strip()
    p = svc.propose("Bad service", "Breaks the canvas.", {"configuration.nix": "{ broken = true; }\n"})
    await svc.build(p["id"])
    helper = FakeHelper(env["config"].apply_socket, {"ok": False, "status": "rolled-back", "message": "health check failed"})
    try:
        result = svc.apply(p["id"], "tok")
    finally:
        helper.close()
    assert result["proposal"]["state"] == "rolled_back"
    assert git(repo, "rev-parse", "main").strip() == main_before


async def test_boot_level_detection(env: dict[str, Any]) -> None:
    cfg = env["config"]
    cfg.build_command[-1] = "newkernel"
    svc = SystemService(cfg)
    p = svc.propose("New kernel", "Latest kernel.", {"configuration.nix": "{ kernel = 2; }\n"})
    built = await svc.build(p["id"])
    assert built["boot_level"] is True and built["boot_changes"] == ["kernel"]
    assert boot_changes(cfg.current_system, env["store"] / "missing") == ["kernel", "initrd", "kernel-params", "bootloader"]


async def test_end_to_end(env: dict[str, Any]) -> None:
    svc = SystemService(env["config"])
    with serving(build_server(svc)) as url:
        async with mcp_session(url) as session:
            tools = await check_surface(session, "diff", "ui://system/review")
            await check_surface(session, "status", "ui://system/review")
            assert set(tools) == {"status", "propose", "propose_service", "build", "diff", "apply", "reject", "rollback",
                                  "disk_status", "disk_passphrase", "recovery_password", "howto"}
            # No tool takes a passphrase or password: they are typed only into the console prompt.
            assert set(tools["disk_passphrase"].inputSchema["properties"]) == {"approval_token"}
            assert set(tools["recovery_password"].inputSchema["properties"]) == {"approval_token"}
            assert hints(tools["diff"]) == (True, False, False)
            assert hints(tools["apply"]) == (False, True, False)
            assert hints(tools["propose"]) == (False, False, False)
            result = await session.call_tool(
                "propose", {"title": "Add hello", "summary": "Installs hello.", "files": {"configuration.nix": "{ }\n"}})
            pid = result.structuredContent["id"]
            result = await session.call_tool("build", {"proposal_id": pid})
            assert result.structuredContent["state"] == "built"
            result = await session.call_tool("diff", {"proposal_id": pid})
            assert "hello" in result.structuredContent["package_diff"]
            result = await session.call_tool("apply", {"proposal_id": pid})
            assert result.isError and "approval token" in result.content[0].text



async def test_refused_apply_keeps_the_proposal_built(env: dict[str, Any]) -> None:
    svc = SystemService(env["config"])
    p = svc.propose("Add hello", "Installs hello.", {"configuration.nix": "{ hello = true; }\n"})
    assert (await svc.build(p["id"]))["state"] == "built"
    helper = FakeHelper(env["config"].apply_socket,
                        {"ok": False, "status": "refused", "refused": True, "message": "unknown approval token"})
    try:
        result = svc.apply(p["id"], "forged-token-value")
    finally:
        helper.close()
    assert result["proposal"]["state"] == "built"
    assert svc.load(p["id"])["apply_error"] == "unknown approval token"


def test_propose_service_adds_an_unreviewed_entry_to_services_json(env: dict[str, Any]) -> None:
    svc, repo = SystemService(env["config"]), env["repo"]
    out = svc.propose_service(title="Notion Notes", url="https://mcp.notion.example/mcp",
                              hosts=["mcp.notion.example", "api.notion.example"], auth="oauth",
                              signin_page="https://notion.example/oauth/authorize", signin_hosts=["notion.example"])
    assert out["state"] == "proposed" and out["title"] == "Add the service Notion Notes"
    assert "mcp.notion.example" in out["summary"] and "notion.example" in out["summary"] and "untrusted" in out["summary"]
    entry = json.loads(git(repo, "show", f"{out['branch']}:services.json"))["services"][0]
    assert entry["id"] == "notionnotes" and entry["slot"] == 0 and entry["reviewed"] is None
    assert entry["new_tools"] == "by_hint" and entry["tools"] == {} and entry["auth"]["kind"] == "oauth"
    # Applied (on main), the next one takes the next slot; the same name is refused.
    git(repo, "merge", "-q", "--ff-only", out["branch"])
    second = svc.propose_service(title="Weather Pro", url="https://mcp.weather.example", hosts=["mcp.weather.example"],
                                 private=False)
    assert [e["slot"] for e in json.loads(git(repo, "show", f"{second['branch']}:services.json"))["services"]] == [0, 1]
    with pytest.raises(ChangeError, match="already a service"):
        svc.propose_service(title="Notion notes", url="https://mcp.notion.example/mcp", hosts=["mcp.notion.example"])
    with pytest.raises(ChangeError, match="already a service"):
        svc.propose_service(title="Todoist", url="https://mcp.t.example", hosts=["mcp.t.example"])  # in the directory
    with pytest.raises(ChangeError, match="include the url's host"):
        svc.propose_service(title="Odd", url="https://mcp.odd.example", hosts=["other.example"])
    with pytest.raises(ChangeError, match="https URL"):
        svc.propose_service(title="Plain", url="http://mcp.plain.example", hosts=["mcp.plain.example"])


def test_disk_status_warns_about_the_initial_passphrase(env: dict[str, Any]) -> None:
    svc = SystemService(env["config"])
    helper = FakeHelper(env["config"].apply_socket,
                        {"ok": True, "encrypted": True, "initial_passphrase": True, "prompt_open": False})
    try:
        disk = svc.disk_status()
        status = svc.status()
    finally:
        helper.close()
    assert disk["encrypted"] and disk["initial_passphrase"] and "public initial passphrase" in disk["warning"]
    assert status["disk"]["encrypted"]
    assert helper.requests == [{"disk": "status"}, {"disk": "status"}, {"recovery": "status"}]


def test_status_survives_a_missing_helper(env: dict[str, Any]) -> None:
    assert "error" in SystemService(env["config"]).status()["disk"]


def test_disk_passphrase_only_forwards_the_token(env: dict[str, Any]) -> None:
    svc = SystemService(env["config"])
    with pytest.raises(ChangeError):
        svc.disk_passphrase("")
    helper = FakeHelper(env["config"].apply_socket, {"ok": True, "status": "prompt-open"})
    try:
        result = svc.disk_passphrase("tok-1234567890abcdef")
    finally:
        helper.close()
    assert result["helper"]["status"] == "prompt-open"
    assert helper.requests == [{"disk": "change-passphrase", "approval_token": "tok-1234567890abcdef"}]


def test_recovery_password_only_forwards_the_token(env: dict[str, Any]) -> None:
    svc = SystemService(env["config"])
    with pytest.raises(ChangeError):
        svc.recovery_password("")
    helper = FakeHelper(env["config"].apply_socket, {"ok": True, "status": "prompt-open"})
    try:
        svc.recovery_password("tok-1234567890abcdef")
    finally:
        helper.close()
    assert helper.requests == [{"recovery": "set-password", "approval_token": "tok-1234567890abcdef"}]


def test_howto_guides(monkeypatch: pytest.MonkeyPatch) -> None:
    from foyelle_connectors.system.server import guide

    monkeypatch.setenv("FOYELLE_CONNECTOR_SLOTS", "files: port 7301, index 11;web: port 7304, index 14")
    text = guide("connector")["guide"]
    assert "foyelle.connectors.weather" in text and "- files: port 7301, index 11" in text
    assert 'FoyelleView.run("<connector>.<tool>"' in guide("surface")["guide"]
    with pytest.raises(ValueError):
        guide("other")


async def test_status_lists_proposals_briefly(env: dict[str, Any]) -> None:
    svc = SystemService(env["config"])
    p = svc.propose("Add hello", "Installs hello.", {"configuration.nix": "{ hello = true; }\n"})
    await svc.build(p["id"])
    status = svc.status()
    (recent,) = status["recent"]
    assert recent["id"] == p["id"] and recent["state"] == "built"
    assert "build_log" not in recent and "files" not in recent


async def test_reconcile_records_an_apply_whose_reply_was_lost(env: dict[str, Any]) -> None:
    svc = SystemService(env["config"])
    p = svc.propose("Add hello", "Installs hello.", {"configuration.nix": "{ hello = true; }\n"})
    await svc.build(p["id"])
    svc._update(p["id"], state="applying")  # the connector was restarted while the helper switched
    commit = svc.load(p["id"])["commit"]
    helper = FakeHelper(env["config"].apply_socket, {"ok": True, "last": {
        "ok": True, "proposal_id": p["id"], "commit": commit, "status": "switched", "generation": 7}})
    try:
        svc.reconcile()
    finally:
        helper.close()
    meta = svc.load(p["id"])
    assert meta["state"] == "applied" and meta["generation"] == 7
    assert svc._rev("refs/heads/main") == commit


async def test_reconcile_merges_when_main_moved_on(env: dict[str, Any]) -> None:
    svc = SystemService(env["config"])
    p = svc.propose("Add hello", "Installs hello.", {"configuration.nix": "{ hello = true; }\n"})
    await svc.build(p["id"])
    svc._update(p["id"], state="applying")
    # A release update commits to main while the change is being applied.
    repo = env["config"].repo
    (Path(repo) / "README.md").write_text("new release\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "-q", "-m", "Follow the running Foyelle release")
    commit = svc.load(p["id"])["commit"]
    helper = FakeHelper(env["config"].apply_socket, {"ok": True, "last": {
        "ok": True, "proposal_id": p["id"], "commit": commit, "status": "switched", "generation": 8}})
    try:
        svc.reconcile()
    finally:
        helper.close()
    assert svc.load(p["id"])["state"] == "applied"
    head = svc._rev("refs/heads/main")
    assert svc._is_ancestor(commit, head)  # the applied change is in main, merged
    assert (Path(repo) / "README.md").read_text() == "new release\n"
