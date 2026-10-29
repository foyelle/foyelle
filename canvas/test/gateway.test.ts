import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildSocketUrl,
  ERR_CONNECTION_CLOSED,
  ERR_METHOD_NOT_FOUND,
  ERR_NOT_CONNECTED,
  ERR_TIMEOUT,
  GatewayClient,
  RpcError,
  type GatewayOptions,
  type ServerRequest,
} from "../src/renderer/gateway";
import { FakeSocket } from "./fake-socket";

const quiet = { debug: () => {}, warn: () => {}, error: () => {} };

function setup(opts: Partial<GatewayOptions> = {}) {
  const sockets: FakeSocket[] = [];
  let token = "tok-1";
  const config = vi.fn(async () => ({ url: "ws://127.0.0.1:9119/api/ws", token }));
  const client = new GatewayClient({
    config,
    socketFactory: (url) => {
      const s = new FakeSocket(url);
      sockets.push(s);
      return s;
    },
    random: () => 0.5, // no jitter
    logger: quiet,
    pingIntervalMs: 0,
    ...opts,
  });
  return {
    client,
    sockets,
    config,
    setToken: (t: string) => (token = t),
    last: () => sockets[sockets.length - 1]!,
  };
}

/** Starts the client and completes the handshake. */
async function ready(env: ReturnType<typeof setup>) {
  env.client.start();
  await vi.advanceTimersByTimeAsync(0);
  const s = env.last();
  s.open();
  s.event("gateway.ready", { version: "test" });
  return s;
}

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("buildSocketUrl", () => {
  it("appends the token as a query parameter", () => {
    expect(buildSocketUrl("ws://h:1/api/ws", "a b&c")).toBe("ws://h:1/api/ws?token=a%20b%26c");
    expect(buildSocketUrl("ws://h:1/api/ws?x=1", "t")).toBe("ws://h:1/api/ws?x=1&token=t");
    expect(buildSocketUrl("ws://h:1/api/ws", "")).toBe("ws://h:1/api/ws");
  });
});

describe("handshake", () => {
  it("connects with the token and becomes ready after gateway.ready", async () => {
    const env = setup();
    const states: string[] = [];
    env.client.onState((s) => states.push(s));
    env.client.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(env.last().url).toBe("ws://127.0.0.1:9119/api/ws?token=tok-1");
    env.last().open();
    expect(env.client.state).toBe("handshaking");
    env.last().event("gateway.ready", { version: "x" });
    expect(env.client.state).toBe("ready");
    expect(env.client.readyInfo).toEqual({ version: "x" });
    expect(states).toEqual(["connecting", "handshaking", "ready"]);
  });

  it("sends client.capabilities {server_requests:true} right after gateway.ready", async () => {
    const env = setup();
    const s = await ready(env);
    const caps = s.lastRequest("client.capabilities");
    expect(caps).toMatchObject({ jsonrpc: "2.0", method: "client.capabilities", params: { server_requests: true } });
    expect(typeof caps.id).toBe("number");
  });

  it("stays ready if client.capabilities fails (older gateway)", async () => {
    const env = setup();
    const s = await ready(env);
    const caps = s.lastRequest("client.capabilities");
    s.receive({ jsonrpc: "2.0", id: caps.id, error: { code: -32601, message: "unknown method" } });
    await vi.advanceTimersByTimeAsync(0);
    expect(env.client.state).toBe("ready");
  });

  it("reconnects when gateway.ready does not arrive in time", async () => {
    const env = setup({ handshakeTimeoutMs: 1000, backoff: { initialMs: 100 } });
    env.client.start();
    await vi.advanceTimersByTimeAsync(0);
    env.last().open();
    await vi.advanceTimersByTimeAsync(1000);
    expect(env.sockets[0]!.closed?.code).toBe(4000);
    expect(env.client.state).toBe("reconnecting");
    await vi.advanceTimersByTimeAsync(100);
    expect(env.sockets.length).toBe(2);
  });
});

describe("requests", () => {
  it("frames requests as JSON-RPC 2.0 with increasing numeric ids", async () => {
    const env = setup();
    const s = await ready(env);
    void env.client.request("session.create", {});
    void env.client.request("prompt.submit", { session_id: "s1", text: "hi" });
    const a = s.lastRequest("session.create");
    const b = s.lastRequest("prompt.submit");
    expect(a).toEqual({ jsonrpc: "2.0", id: a.id, method: "session.create", params: {} });
    expect(b).toEqual({ jsonrpc: "2.0", id: b.id, method: "prompt.submit", params: { session_id: "s1", text: "hi" } });
    expect(b.id).toBeGreaterThan(a.id);
  });

  it("resolves with the result of the matching response", async () => {
    const env = setup();
    const s = await ready(env);
    const p = env.client.request("session.create");
    s.reply("session.create", { session_id: "abc" });
    await expect(p).resolves.toEqual({ session_id: "abc" });
  });

  it("rejects with an RpcError carrying the error code", async () => {
    const env = setup();
    const s = await ready(env);
    const p = env.client.request("session.resume", { session_id: "nope" });
    const req = s.lastRequest("session.resume");
    s.receive({ jsonrpc: "2.0", id: req.id, error: { code: -32004, message: "unknown session", data: { x: 1 } } });
    await expect(p).rejects.toMatchObject({ name: "RpcError", code: -32004, message: "unknown session", data: { x: 1 } });
  });

  it("times out", async () => {
    const env = setup({ requestTimeoutMs: 500 });
    await ready(env);
    const p = env.client.request("slow");
    const assertion = expect(p).rejects.toMatchObject({ code: ERR_TIMEOUT });
    await vi.advanceTimersByTimeAsync(500);
    await assertion;
  });

  it("honours a per-request timeout", async () => {
    const env = setup({ requestTimeoutMs: 500 });
    const s = await ready(env);
    const p = env.client.request("surface.call_tool", {}, { timeoutMs: 5000 });
    await vi.advanceTimersByTimeAsync(1000);
    s.reply("surface.call_tool", { content: [] });
    await expect(p).resolves.toEqual({ content: [] });
  });

  it("rejects requests while not connected", async () => {
    const env = setup();
    await expect(env.client.request("ping")).rejects.toMatchObject({ code: ERR_NOT_CONNECTED });
  });

  it("ignores responses with unknown ids and malformed frames", async () => {
    const env = setup();
    const s = await ready(env);
    s.receive({ jsonrpc: "2.0", id: 999, result: {} });
    s.receive("{not json");
    s.receive({ hello: "world" });
    s.receive(JSON.stringify([1, 2]));
    expect(env.client.state).toBe("ready");
  });
});

describe("events", () => {
  it("dispatches by type and to * listeners, with session_id and payload", async () => {
    const env = setup();
    const s = await ready(env);
    const typed = vi.fn();
    const any = vi.fn();
    env.client.on("message.delta", typed);
    env.client.on("*", any);
    s.event("message.delta", { text: "he" }, "sess-1");
    expect(typed).toHaveBeenCalledWith({ type: "message.delta", session_id: "sess-1", payload: { text: "he" } });
    expect(any).toHaveBeenCalledTimes(1); // only the delta: gateway.ready came before subscribing
  });

  it("unsubscribes", async () => {
    const env = setup();
    const s = await ready(env);
    const h = vi.fn();
    const off = env.client.on("tool.start", h);
    off();
    s.event("tool.start", {});
    expect(h).not.toHaveBeenCalled();
  });

  it("keeps dispatching when a handler throws", async () => {
    const env = setup();
    const s = await ready(env);
    const good = vi.fn();
    env.client.on("x", () => {
      throw new Error("boom");
    });
    env.client.on("x", good);
    s.event("x");
    expect(good).toHaveBeenCalled();
  });

  it("accepts a batch (array) frame", async () => {
    const env = setup();
    const s = await ready(env);
    const h = vi.fn();
    env.client.on("message.delta", h);
    s.receive([
      { jsonrpc: "2.0", method: "event", params: { type: "message.delta", payload: { text: "a" } } },
      { jsonrpc: "2.0", method: "event", params: { type: "message.delta", payload: { text: "b" } } },
    ]);
    expect(h).toHaveBeenCalledTimes(2);
  });
});

describe("server requests", () => {
  it("delivers server requests and sends result frames", async () => {
    const env = setup();
    const s = await ready(env);
    let got: ServerRequest | null = null;
    env.client.onServerRequest((r) => (got = r));
    s.receive({ jsonrpc: "2.0", id: "srq-1", method: "approval", params: { tool_name: "mcp_email_send" } });
    expect(got).not.toBeNull();
    expect(got!.method).toBe("approval");
    expect(got!.params).toEqual({ tool_name: "mcp_email_send" });
    expect(got!.respond({ choice: "once" })).toBe(true);
    expect(s.sent[s.sent.length - 1]).toEqual({ jsonrpc: "2.0", id: "srq-1", result: { choice: "once" } });
    // A second answer is refused.
    expect(got!.respond({ choice: "deny" })).toBe(false);
    expect(got!.settled).toBe(true);
  });

  it("sends JSON-RPC errors with fail()", async () => {
    const env = setup();
    const s = await ready(env);
    let got: ServerRequest | null = null;
    env.client.onServerRequest((r) => (got = r));
    s.receive({ jsonrpc: "2.0", id: "srq-2", method: "secret", params: {} });
    got!.fail("cancelled by the user");
    expect(s.sent[s.sent.length - 1]).toEqual({ jsonrpc: "2.0", id: "srq-2", error: { code: -32000, message: "cancelled by the user" } });
  });

  it("fails server requests nobody handles", async () => {
    const env = setup();
    const s = await ready(env);
    s.receive({ jsonrpc: "2.0", id: "srq-3", method: "sudo", params: {} });
    expect(s.sent[s.sent.length - 1]).toMatchObject({ id: "srq-3", error: { code: ERR_METHOD_NOT_FOUND } });
  });

  it("request.cancel aborts the request and blocks a late answer", async () => {
    const env = setup();
    const s = await ready(env);
    let got: ServerRequest | null = null;
    env.client.onServerRequest((r) => (got = r));
    const cancelled = vi.fn();
    const evt = vi.fn();
    env.client.on("request.cancel", evt);
    s.receive({ jsonrpc: "2.0", id: "srq-4", method: "clarify", params: {} });
    got!.signal.addEventListener("abort", cancelled);
    s.event("request.cancel", { id: "srq-4" });
    expect(cancelled).toHaveBeenCalled();
    expect(evt).toHaveBeenCalled();
    const before = s.sent.length;
    expect(got!.respond({ answers: {} })).toBe(false);
    expect(s.sent.length).toBe(before);
  });

  it("request.cancel for an unknown id is harmless", async () => {
    const env = setup();
    const s = await ready(env);
    s.event("request.cancel", { id: "srq-unknown" });
    expect(env.client.state).toBe("ready");
  });
});

describe("disconnect and reconnect", () => {
  it("rejects pending requests and aborts server requests when the socket closes", async () => {
    const env = setup();
    const s = await ready(env);
    let got: ServerRequest | null = null;
    env.client.onServerRequest((r) => (got = r));
    s.receive({ jsonrpc: "2.0", id: "srq-5", method: "approval", params: {} });
    const p = env.client.request("prompt.submit", {});
    s.serverClose();
    await expect(p).rejects.toMatchObject({ code: ERR_CONNECTION_CLOSED });
    expect(got!.signal.aborted).toBe(true);
    expect(env.client.state).toBe("reconnecting");
  });

  it("backs off exponentially, capped, and resets after a successful handshake", async () => {
    const env = setup({ backoff: { initialMs: 100, factor: 2, maxMs: 350, jitter: 0.2 } });
    const s = await ready(env);
    s.serverClose();
    const delays: number[] = [];
    for (let i = 0; i < 4; i++) {
      const before = env.sockets.length;
      let waited = 0;
      while (env.sockets.length === before) {
        await vi.advanceTimersByTimeAsync(10);
        waited += 10;
        if (waited > 5000) throw new Error("no reconnect");
      }
      delays.push(waited);
      env.last().serverClose(); // fail again before opening
    }
    expect(delays).toEqual([100, 200, 350, 350]);

    // A successful handshake resets the attempt counter.
    let before = env.sockets.length;
    while (env.sockets.length === before) await vi.advanceTimersByTimeAsync(10);
    env.last().open();
    env.last().event("gateway.ready");
    expect(env.client.reconnectAttempts).toBe(0);
    env.last().serverClose();
    before = env.sockets.length;
    await vi.advanceTimersByTimeAsync(100);
    expect(env.sockets.length).toBe(before + 1);
  });

  it("applies jitter from the random source", async () => {
    const env = setup({ random: () => 0, backoff: { initialMs: 1000, jitter: 0.5 } });
    const s = await ready(env);
    s.serverClose();
    await vi.advanceTimersByTimeAsync(499);
    expect(env.sockets.length).toBe(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(env.sockets.length).toBe(2);
  });

  it("re-reads the token before every reconnect", async () => {
    const env = setup({ backoff: { initialMs: 50 } });
    const s = await ready(env);
    env.setToken("tok-2");
    s.serverClose();
    await vi.advanceTimersByTimeAsync(50);
    expect(env.config).toHaveBeenCalledTimes(2);
    expect(env.last().url).toBe("ws://127.0.0.1:9119/api/ws?token=tok-2");
  });

  it("retries when the config (token file) cannot be loaded", async () => {
    const env = setup({ backoff: { initialMs: 50 } });
    env.config.mockRejectedValueOnce(new Error("ENOENT"));
    env.client.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(env.sockets.length).toBe(0);
    expect(env.client.state).toBe("reconnecting");
    await vi.advanceTimersByTimeAsync(50);
    expect(env.sockets.length).toBe(1);
  });

  it("ignores frames from a replaced socket", async () => {
    const env = setup({ backoff: { initialMs: 10 } });
    const old = await ready(env);
    old.serverClose();
    await vi.advanceTimersByTimeAsync(10);
    const h = vi.fn();
    env.client.on("message.delta", h);
    old.event("message.delta", { text: "stale" });
    expect(h).not.toHaveBeenCalled();
  });

  it("stop() closes the socket and never reconnects", async () => {
    const env = setup({ backoff: { initialMs: 10 } });
    const s = await ready(env);
    const p = env.client.request("x");
    env.client.stop();
    await expect(p).rejects.toBeInstanceOf(RpcError);
    expect(s.closed?.code).toBe(1000);
    await vi.advanceTimersByTimeAsync(1000);
    expect(env.sockets.length).toBe(1);
    expect(env.client.state).toBe("stopped");
  });
});

describe("heartbeat", () => {
  it("pings while ready", async () => {
    const env = setup({ pingIntervalMs: 1000, pingTimeoutMs: 500 });
    const s = await ready(env);
    await vi.advanceTimersByTimeAsync(1000);
    expect(s.lastRequest("ping")).toMatchObject({ method: "ping", params: {} });
    s.reply("ping", {});
    await vi.advanceTimersByTimeAsync(600);
    expect(env.client.state).toBe("ready");
  });

  it("reconnects when a ping goes unanswered", async () => {
    const env = setup({ pingIntervalMs: 1000, pingTimeoutMs: 500, backoff: { initialMs: 100 } });
    const s = await ready(env);
    await vi.advanceTimersByTimeAsync(1500);
    expect(s.closed?.code).toBe(4001);
    expect(env.client.state).toBe("reconnecting");
    await vi.advanceTimersByTimeAsync(100);
    expect(env.sockets.length).toBe(2);
  });
});
