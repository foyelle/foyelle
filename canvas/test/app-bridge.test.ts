/**
 * Round trip between the real ext-apps AppBridge + PostMessageTransport
 * (host side, wired by src/renderer/surfaces.ts) and the canvas View runtime
 * (built-in views), over fake windows: postMessage delivers a "message"
 * event whose `source` is the sending window, as a browser does.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppBridge, PostMessageTransport } from "@modelcontextprotocol/ext-apps/app-bridge";
import { HOST_INFO, hostCapabilities, messageText, wireBridge, type SurfaceHandlers } from "../src/renderer/surfaces";
import { ViewConnection } from "../src/renderer/builtin-views/view-runtime";

let bus: EventTarget;

interface FakeWindow {
  postMessage(data: unknown, targetOrigin?: string): void;
}

function deliver(data: unknown, source: FakeWindow) {
  setTimeout(() => {
    const ev = new Event("message");
    Object.defineProperties(ev, { data: { value: structuredClone(data) }, source: { value: source } });
    bus.dispatchEvent(ev);
  }, 0);
}

// hostWin is the View's `window.parent`; viewWin is the iframe's contentWindow.
const hostWin: FakeWindow = { postMessage: (d) => deliver(d, viewWin) };
const viewWin: FakeWindow = { postMessage: (d) => deliver(d, hostWin) };

beforeEach(() => {
  bus = new EventTarget();
  (globalThis as unknown as { window: EventTarget }).window = bus;
  vi.spyOn(console, "debug").mockImplementation(() => {});
});
afterEach(() => {
  delete (globalThis as unknown as { window?: EventTarget }).window;
  vi.restoreAllMocks();
});

async function connect(kind: "app" | "system", handlers: Partial<SurfaceHandlers>, extra: { toolInput?: Record<string, unknown>; toolResult?: any } = {}) {
  const callTool = vi.fn(async () => ({ content: [{ type: "text" as const, text: "ok" }] }));
  const h: SurfaceHandlers = { callTool, ...handlers };
  const bridge = new AppBridge(null, HOST_INFO, hostCapabilities(kind), { hostContext: { theme: "dark", displayMode: "inline" } });
  const ready = wireBridge(bridge, { kind, handlers: h, ...extra }, { debug() {}, warn() {}, error() {} });
  await bridge.connect(new PostMessageTransport(viewWin as unknown as Window, viewWin as unknown as Window));
  const view = new ViewConnection({ name: "test-view", parent: hostWin, listenOn: bus as unknown as Window });
  const inputs: Record<string, unknown>[] = [];
  const results: unknown[] = [];
  view.ontoolinput = (a) => inputs.push(a);
  view.ontoolresult = (r) => results.push(r);
  await view.connect();
  await ready;
  return { bridge, view, inputs, results, handlers: h };
}

describe("AppBridge <-> View round trip", () => {
  it("initialises, shares host context and delivers tool input then result", async () => {
    const { view, inputs, results, bridge } = await connect(
      "app",
      {},
      { toolInput: { path: "~/Documents" }, toolResult: { content: [{ type: "text", text: "6 entries" }], structuredContent: { n: 6 } } },
    );
    expect(view.hostInfo).toMatchObject({ name: "foyelle-canvas" });
    expect(view.hostContext.theme).toBe("dark");
    expect(bridge.getAppVersion()).toMatchObject({ name: "test-view" });
    await vi.waitFor(() => expect(results).toHaveLength(1));
    expect(inputs).toEqual([{ path: "~/Documents" }]);
    expect(results[0]).toMatchObject({ structuredContent: { n: 6 } });
  });

  it("routes tools/call to the host handler and returns its result", async () => {
    const { view, handlers } = await connect("system", {}, { toolInput: { kind: "approval", request: {} } });
    const r = await view.callTool("approval_respond", { choice: "once" });
    expect(handlers.callTool).toHaveBeenCalledWith({ name: "approval_respond", arguments: { choice: "once" } });
    expect(r.content[0]!.text).toBe("ok");
  });

  it("turns a throwing tool handler into an isError result", async () => {
    const { view } = await connect("app", {
      callTool: async () => {
        throw new Error("denied by policy");
      },
    });
    const r = await view.callTool("delete_file", {});
    expect(r.isError).toBe(true);
    expect(r.content[0]!.text).toBe("denied by policy");
  });

  it("forwards ui/message text and ui/open-link for app surfaces", async () => {
    const message = vi.fn(async () => {});
    const openLink = vi.fn(async () => {});
    const { view } = await connect("app", { message, openLink });
    await expect(view.sendMessage("summarise this")).resolves.toEqual({});
    expect(message).toHaveBeenCalledWith("summarise this");
    await view.openLink("https://example.org/x");
    expect(openLink).toHaveBeenCalledWith("https://example.org/x");
  });

  it("answers display-mode requests with inline and treats fullscreen as focus", async () => {
    const requestFocus = vi.fn();
    const { view } = await connect("app", { requestFocus });
    await expect(view.request("ui/request-display-mode", { mode: "fullscreen" })).resolves.toMatchObject({ mode: "inline" });
    expect(requestFocus).toHaveBeenCalled();
  });

  it("reports size changes and host context changes", async () => {
    const sizeChanged = vi.fn();
    const { view, bridge } = await connect("system", { sizeChanged });
    view.sendSizeChanged(320.4, 210.2);
    await vi.waitFor(() => expect(sizeChanged).toHaveBeenCalledWith({ width: 321, height: 211 }));
    const changed = vi.fn();
    view.onhostcontextchanged = changed;
    bridge.setHostContext({ theme: "light", displayMode: "inline" });
    await vi.waitFor(() => expect(changed).toHaveBeenCalled());
    expect(view.hostContext.theme).toBe("light");
  });

  it("tears down: the View gets ui/resource-teardown and answers", async () => {
    const { view, bridge } = await connect("system", {});
    const onteardown = vi.fn();
    view.onteardown = onteardown;
    await expect(bridge.teardownResource({}, { timeout: 1000 })).resolves.toEqual({});
    expect(onteardown).toHaveBeenCalled();
  });

  it("does not offer resources/read or messages to system views", async () => {
    const { view } = await connect("system", {});
    await expect(view.request("resources/read", { uri: "ui://x" })).rejects.toThrow();
    expect(hostCapabilities("system")).toEqual({ serverTools: {} });
  });
});

describe("messageText", () => {
  it("joins text blocks and ignores others", () => {
    expect(messageText([{ type: "text", text: "a" }, { type: "image", data: "x" }, { type: "text", text: "b" }])).toBe("a\n\nb");
    expect(messageText("nope")).toBe("");
  });
});
