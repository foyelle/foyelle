"""Drive the harness gateway the way the canvas does, for tests.

  python3 gateway-client.py ping
  python3 gateway-client.py chat "TEXT" [--approve once|deny] [--expect-surface] [--call-surface-tool NAME ARGS_JSON]
  python3 gateway-client.py open-resource SERVER URI

Reads the token from $FOYELLE_HARNESS_TOKEN_FILE (default /run/foyelle/harness-token) and prints
one JSON summary on stdout. Exit status 0 only when the expectations hold.
"""

import argparse
import asyncio
import json
import os
import sys

import websockets

URL = os.environ.get("FOYELLE_HARNESS_URL", "ws://127.0.0.1:9119/api/ws")
TOKEN_FILE = os.environ.get("FOYELLE_HARNESS_TOKEN_FILE", "/run/foyelle/harness-token")


class Client:
    def __init__(self, ws):
        self.ws = ws
        self.next_id = 1
        self.pending = {}
        self.events = []
        self.requests = []
        self.event_waiters = []

    async def run(self, on_request):
        async for raw in self.ws:
            frame = json.loads(raw)
            if "id" in frame and ("result" in frame or "error" in frame) and frame["id"] in self.pending:
                self.pending.pop(frame["id"]).set_result(frame)
            elif frame.get("method") == "event":
                self.events.append(frame["params"])
                for waiter in list(self.event_waiters):
                    waiter(frame["params"])
            elif "method" in frame and "id" in frame:
                self.requests.append(frame)
                result = on_request(frame)
                await self.ws.send(json.dumps({"jsonrpc": "2.0", "id": frame["id"], "result": result}))

    async def call(self, method, params=None, timeout=120):
        rid = self.next_id
        self.next_id += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        await self.ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}))
        frame = await asyncio.wait_for(fut, timeout)
        if "error" in frame:
            raise RuntimeError(f"{method}: {frame['error']}")
        return frame["result"]

    async def wait_event(self, predicate, timeout=180):
        for event in self.events:
            if predicate(event):
                return event
        fut = asyncio.get_running_loop().create_future()

        def check(event):
            if not fut.done() and predicate(event):
                fut.set_result(event)

        self.event_waiters.append(check)
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.event_waiters.remove(check)


async def main(args):
    token = open(TOKEN_FILE).read().strip()
    async with websockets.connect(f"{URL}?token={token}", max_size=8 * 1024 * 1024) as ws:
        client = Client(ws)
        answer = {"approval": {"choice": args.approve}, "clarify": {"answers": {}}, "secret": {"value": ""},
                  "sudo": {"value": ""}}
        reader = asyncio.create_task(client.run(lambda frame: answer.get(frame["method"], {})))
        await client.wait_event(lambda e: e.get("type") == "gateway.ready", timeout=30)
        await client.call("client.capabilities", {"server_requests": True})
        out = {"ok": True}
        if args.command == "ping":
            out["ping"] = await client.call("ping")
        elif args.command == "open-resource":
            doc = await client.call("surface.open_resource", {"server": args.server, "uri": args.uri})
            out.update(uri=doc["uri"], html_bytes=len(doc["html"]))
        elif args.command == "list-surfaces":
            out["surfaces"] = (await client.call("surface.list"))["surfaces"]
        elif args.command == "chat":
            session = await client.call("session.create", {})
            sid = session["session_id"]
            await client.call("prompt.submit", {"session_id": sid, "text": args.text})
            done = await client.wait_event(lambda e: e.get("type") == "message.complete" and e.get("session_id") == sid)
            out["reply"] = (done.get("payload") or {}).get("text")
            out["tools"] = [e["payload"]["name"] for e in client.events if e.get("type") == "tool.complete"]
            out["approvals"] = [r["params"] for r in client.requests if r["method"] == "approval"]
            surfaces = [e["payload"] for e in client.events if e.get("type") == "surface.open"]
            out["surfaces"] = [{"server": s["server"], "uri": s["uri"], "html_bytes": len(s["html"])} for s in surfaces]
            if args.expect_surface and not surfaces:
                out["ok"] = False
                out["error"] = "no surface.open event"
            if args.call_surface_tool and surfaces:
                name, arguments = args.call_surface_tool
                if arguments.startswith("@"):
                    arguments = open(arguments[1:]).read()
                out["surface_call"] = await client.call("surface.call_tool", {
                    "session_id": sid, "surface_id": surfaces[0]["surface_id"], "server": surfaces[0]["server"],
                    "name": name, "arguments": json.loads(arguments)})
        reader.cancel()
        print(json.dumps(out))
        return 0 if out["ok"] else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["ping", "chat", "open-resource", "list-surfaces"])
    p.add_argument("text", nargs="?")
    p.add_argument("uri", nargs="?")
    p.add_argument("--server")
    p.add_argument("--approve", default="once")
    p.add_argument("--expect-surface", action="store_true")
    p.add_argument("--call-surface-tool", nargs=2, metavar=("NAME", "ARGS_JSON"))
    a = p.parse_args()
    if a.command == "open-resource":
        a.server, a.uri = a.text, a.uri
    sys.exit(asyncio.run(main(a)))
