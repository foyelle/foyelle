import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { argsKey, NOT_IN_DEMO, RecordedTools, type Recording } from "../demo/recorded-tools";
import { MockHarness } from "../dev/mock-harness";
import { buildCanvasCsp, buildSurfaceCsp, surfaceHeaders } from "../src/shared/csp";

const demo = path.join(__dirname, "..", "demo");
const recording = JSON.parse(readFileSync(path.join(demo, "recording.json"), "utf8")) as Recording;
const tools = () => new RecordedTools(recording, async (file) => readFileSync(path.join(demo, "cards", file), "utf8"));
const json = (r: any) => JSON.parse(r.content[0].text);

const headersFile = path.join(__dirname, "..", "..", "site", "_headers");

/** The headers site/_headers gives one exact rule. */
function siteHeaders(rule: string): Record<string, string> {
  const lines = readFileSync(headersFile, "utf8").split("\n");
  const out: Record<string, string> = {};
  for (let i = lines.indexOf(rule) + 1; i > 0 && i < lines.length && /^\s+\S/.test(lines[i]!); i++) {
    const line = lines[i]!.trim();
    out[line.slice(0, line.indexOf(":"))] = line.slice(line.indexOf(":") + 1).trim();
  }
  return out;
}

// The canvas package (nixos/pkgs/canvas.nix) builds from canvas/ alone; CI checks out the whole repository.
describe.skipIf(!existsSync(headersFile))("the web demo's policies (site/_headers)", () => {
  it("gives the canvas page the canvas's policy, and no framing", () => {
    expect(siteHeaders("/demo/")["Content-Security-Policy"]).toBe(
      `${buildCanvasCsp({ connect: ["'self'"], frame: ["'self'"] })}; frame-ancestors 'none'`,
    );
  });

  it("gives every card the cards' default policy, as Foyelle does", () => {
    const cards = siteHeaders("/demo/cards/*");
    expect(cards["Content-Security-Policy"]).toBe(buildSurfaceCsp(null));
    expect(cards["Permissions-Policy"]).toBe(surfaceHeaders(null)["Permissions-Policy"]);
  });
});

describe("the demo's recording", () => {
  it("has a page for every card it lists, all with the default policy", () => {
    for (const s of recording.surfaces) {
      const page = recording.pages[`${s.server} ${s.uri}`];
      expect(page, s.uri).toBeDefined();
      expect(existsSync(path.join(demo, "cards", page!.file))).toBe(true);
      expect(buildSurfaceCsp(page!.csp as never)).toBe(buildSurfaceCsp(null));
    }
  });

  it("names nothing of the laptop it was recorded on", () => {
    const text = readFileSync(path.join(demo, "recording.json"), "utf8");
    expect(text).not.toMatch(/\/Users\/|\/home\/(?!foyelle)|\/private\/|\/tmp\//);
  });
});

describe("the demo's helpers (RecordedTools)", () => {
  it("starts as a first start: no name, the welcome not done", async () => {
    const s = json(await tools().call("shell", "settings_get", {}));
    expect(s.effective.ui.welcomed).not.toBe(true);
    expect(s.effective.user?.name).toBeUndefined();
    expect(s.theme.id).toBe("ember");
  });

  it("keeps the visit's name, look and theme", async () => {
    const t = tools();
    await t.call("shell", "user_name_set", { name: "Ana" });
    await t.call("shell", "settings_set", { key: "ui.theme", value: "dark" });
    expect(json(await t.call("shell", "theme_use", { theme_id: "tokyo-night" }))).toEqual({ current: "tokyo-night", name: "Tokyo Night" });
    const s = json(await t.call("shell", "settings_get", {}));
    expect(s.effective.user.name).toBe("Ana");
    expect(s.effective.ui.theme).toBe("dark");
    expect(s.theme.id).toBe("tokyo-night");
    expect(json(await t.call("shell", "theme_list", {}))).toMatchObject({ current: "tokyo-night", scheme: "dark" });
  });

  it("refuses settings that don't exist and themes it doesn't have", async () => {
    expect((await tools().call("shell", "settings_set", { key: "model.provider", value: "x" })).isError).toBe(true);
    expect((await tools().call("shell", "theme_use", { theme_id: "nope" })).isError).toBe(true);
  });

  it("answers as recorded, whatever the order of the arguments", async () => {
    const r = await tools().call("files", "list", { path: "documents/Letters" });
    expect(json(r).entries.map((e: { name: string }) => e.name)).toContain("Thank you, Rita.md");
    expect(argsKey({ b: 1, a: { d: 2, c: 3 } })).toBe('{"a":{"c":3,"d":2},"b":1}');
  });

  it("runs and drops what is waiting, and keeps what it wrote", async () => {
    const t = tools();
    const [item] = json(await t.call("shell", "waiting_list", {})).items;
    expect(item.summary).toBe("Add candles to the shopping list");
    expect((await t.call(item.connector, item.tool, item.args)).isError).toBeFalsy();
    expect(json(await t.call("files", "read", { path: item.args.path })).text).toContain("Candles");
    expect(json(await t.call("shell", "waiting_drop", { item_id: item.id }))).toEqual({ dropped: item.id });
    expect(json(await t.call("shell", "waiting_list", {})).items).toEqual([]);
    expect((await t.call("shell", "waiting_drop", { item_id: item.id })).isError).toBe(true);
    expect(json(await tools().call("files", "read", { path: item.args.path })).text).not.toContain("Candles");
  });

  it("says so for a call it has no answer for", async () => {
    const r = await tools().call("files", "write", { path: "documents/x.txt", content: "hi" });
    expect(r).toEqual({ content: [{ type: "text", text: NOT_IN_DEMO }], isError: true });
  });

  it("answers calls that carry a time with any recorded run", async () => {
    expect((await tools().call("shell", "routine_results", { since: 1, limit: 80 })).isError).toBeFalsy();
  });

  it("serves the cards' pages", async () => {
    const page = await tools().resource("files", "ui://files/browser");
    expect(page.html).toContain("<html");
    await expect(tools().resource("design", "ui://design/glass")).rejects.toThrow();
  });
});

describe("the mock harness in the demo", () => {
  async function ask(text: string, demoMode: boolean): Promise<string> {
    const harness = new MockHarness({ demo: demoMode, tools: tools(), loadSurfaceHtml: () => Promise.reject(new Error("no")) });
    let onText: (t: string) => void = () => undefined;
    const frames: any[] = [];
    harness.connect({ send: (f) => frames.push(f), onMessage: (cb) => (onText = cb), onClose: () => undefined });
    onText(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "session.create", params: {} }));
    await new Promise((r) => setTimeout(r, 10));
    const sid = frames.find((f) => f.id === 1).result.session_id;
    onText(JSON.stringify({ jsonrpc: "2.0", id: 2, method: "prompt.submit", params: { session_id: sid, text } }));
    for (let i = 0; i < 200; i++) {
      const done = frames.find((f) => f.params?.type === "message.complete");
      if (done) return done.params.payload.text;
      await new Promise((r) => setTimeout(r, 50));
    }
    throw new Error("no answer");
  }

  it("answers anything off the scripted paths with a word about the demo", async () => {
    expect(await ask("Book me a table for two", true)).toMatch(/canned answers, and no AI model/);
  });

  it("leaves out the developer's test flows", async () => {
    expect(await ask("I need a secret", true)).toMatch(/canned answers/);
    expect(await ask("error", true)).toMatch(/canned answers/);
  });
});
