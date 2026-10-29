# M4 gate. The System connector proposes, builds (a prebuilt specialisation stands in for
# `nix build`), and applies through the root helper with a one-time token. A healthy change is
# kept; a change that breaks the harness rolls back on its own; nothing applies without a token.
import json, secrets, time

machine.wait_for_unit("multi-user.target")
machine.wait_for_unit("foyelle-connector-system.service")
machine.wait_for_unit("foyelle-apply.socket")
machine.wait_for_unit("foyelle-harness.service")
machine.wait_until_succeeds("test -d /var/lib/foyelle/connectors/system/config/.git", timeout=60)

CLIENT = r'''
import asyncio, json, sys
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main(tool, args):
    async with streamablehttp_client("http://127.0.0.1:7305/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool(tool, json.loads(args))
            out = res.structuredContent if res.structuredContent is not None else [c.text for c in res.content]
            print(json.dumps({"isError": bool(res.isError), "result": out}))

asyncio.run(main(sys.argv[1], sys.argv[2]))
'''
machine.succeed(f"cat > /tmp/mcp-call.py <<'PY'\n{CLIENT}\nPY")
machine.succeed("chmod 644 /tmp/mcp-call.py")


def call(tool, args):
    # The firewall admits only the harness uid to connector ports, so call as the harness.
    machine.succeed(f"echo '{json.dumps(args)}' > /tmp/args.json && chmod 644 /tmp/args.json")
    out = machine.succeed(
        "su -s /bin/sh foyelle-harness -c "
        f"'python3 /tmp/mcp-call.py {tool} \"$(cat /tmp/args.json)\"'")
    return json.loads(out.strip().splitlines()[-1])


def token(proposal_id, tool="system.apply"):
    # What the policy plugin writes after the user approves (harness-owned, 0700 directory).
    name = secrets.token_urlsafe(24)
    body = {"tool": tool, "proposal_id": proposal_id, "expires": time.time() + 300}
    machine.succeed(f"echo '{json.dumps(body)}' > /run/foyelle/approvals/{name}")
    machine.succeed(f"chown foyelle-harness /run/foyelle/approvals/{name}")
    return name


def propose(title):
    res = call("propose", {"title": title, "summary": f"switch to the {title} specialisation",
                           "files": {"configuration.nix": f"# {title}\n{{ ... }}: {{ }}\n"}})
    assert not res["isError"], res
    pid = res["result"]["id"] if isinstance(res["result"], dict) else json.loads(res["result"][0])["id"]
    for _ in range(30):  # build returns the proposal; call again until the build finished
        built = call("build", {"proposal_id": pid})
        assert not built["isError"], built
        if built["result"].get("state") == "built":
            break
        assert built["result"].get("state") == "building", built
        time.sleep(2)
    else:
        raise AssertionError(f"proposal {pid} never built")
    return pid


def generation():
    return machine.succeed("readlink /nix/var/nix/profiles/system").strip()


# Test VMs boot without a system profile; give the running system one, as an installed machine has.
machine.succeed("nix-env --profile /nix/var/nix/profiles/system --set $(readlink -f /run/current-system)")
start = generation()

# 1. No token, no change.
pid = propose("good")
res = call("apply", {"proposal_id": pid, "approval_token": ""})
assert res["isError"], res
res = call("apply", {"proposal_id": pid, "approval_token": "forged-token-forged-token"})
assert res["isError"] or "refused" in json.dumps(res), res
assert generation() == start

# 2. Approved healthy change: applied, and the marker comes from the good specialisation.
res = call("apply", {"proposal_id": pid, "approval_token": token(pid)})
print(res)
assert not res["isError"], res
machine.succeed("grep -qx good /etc/foyelle-generation")
good = generation()
assert good != start

# 3. Approved broken change (the harness cannot start): rolled back automatically.
bad = propose("bad")
res = call("apply", {"proposal_id": bad, "approval_token": token(bad)})
print(res)
assert "rolled" in json.dumps(res), res
machine.succeed("grep -qx good /etc/foyelle-generation")
machine.wait_for_unit("foyelle-harness.service")

# 4. The helper's record matches.
last = json.loads(machine.succeed("cat /var/lib/foyelle/apply/last.json"))
assert last["status"] == "rolled-back" and last["rolled_back"], last
