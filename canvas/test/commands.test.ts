import { describe, expect, it } from "vitest";
import { completions, parseCommand } from "../src/renderer/commands";

describe("parseCommand", () => {
  it("treats plain text as a prompt", () => {
    expect(parseCommand("  hello world  ")).toEqual({ kind: "prompt", text: "hello world" });
    expect(parseCommand("line one\nline two")).toEqual({ kind: "prompt", text: "line one\nline two" });
    expect(parseCommand("what is 1/2?")).toEqual({ kind: "prompt", text: "what is 1/2?" });
  });

  it("ignores blank input", () => {
    expect(parseCommand("")).toEqual({ kind: "empty" });
    expect(parseCommand("  \n ")).toEqual({ kind: "empty" });
  });

  it("escapes a leading slash with //", () => {
    expect(parseCommand("//etc/hosts is a file")).toEqual({ kind: "prompt", text: "/etc/hosts is a file" });
  });

  it("parses surface commands with optional references", () => {
    expect(parseCommand("/focus 2")).toEqual({ kind: "focus", ref: "2" });
    expect(parseCommand("/focus  system status ")).toEqual({ kind: "focus", ref: "system status" });
    expect(parseCommand("/pin")).toEqual({ kind: "pin", ref: undefined });
    expect(parseCommand("/pin files")).toEqual({ kind: "pin", ref: "files" });
    expect(parseCommand("/unpin 1")).toEqual({ kind: "unpin", ref: "1" });
    expect(parseCommand("/dismiss")).toEqual({ kind: "dismiss", ref: undefined });
    expect(parseCommand("/close 3")).toEqual({ kind: "dismiss", ref: "3" });
    expect(parseCommand("/surfaces")).toEqual({ kind: "surfaces", ref: undefined });
    expect(parseCommand("/surfaces memory")).toEqual({ kind: "surfaces", ref: "memory" });
  });

  it("parses session commands", () => {
    expect(parseCommand("/new")).toEqual({ kind: "new" });
    expect(parseCommand("/clear")).toEqual({ kind: "new" });
    expect(parseCommand("/clear now").kind).toBe("error");
    expect(parseCommand("/STOP")).toEqual({ kind: "stop" });
    expect(parseCommand("/help")).toEqual({ kind: "help" });
    expect(parseCommand("/new now")).toMatchObject({ kind: "error" });
  });

  it("requires a reference for /focus", () => {
    expect(parseCommand("/focus")).toMatchObject({ kind: "error", message: expect.stringContaining("Usage") });
  });

  it("suggests a canvas command for a prefix of one, and sends anything else to the harness", () => {
    expect(parseCommand("/sur")).toMatchObject({ kind: "error", message: expect.stringContaining("/surfaces") });
    expect(parseCommand("/skills pending")).toEqual({ kind: "harness", command: "/skills pending" });
    expect(parseCommand("  /skills approve abc12 ")).toEqual({ kind: "harness", command: "/skills approve abc12" });
    expect(parseCommand("/foo")).toEqual({ kind: "harness", command: "/foo" });
    expect(parseCommand("/")).toMatchObject({ kind: "error" });
  });
});

describe("completions", () => {
  it("lists matching commands for a slash prefix", () => {
    expect(completions("/")).toHaveLength(12);
    expect(completions("/p")).toEqual(["/pin"]);
    expect(completions("/s")).toEqual(["/surfaces", "/stop"]);
    expect(completions("hello")).toEqual([]);
    expect(completions("/pin x")).toEqual([]);
  });
});

describe("plain words for tools", () => {
  it("describes known tools and hides tool names", async () => {
    const { describeTool } = await import("../src/renderer/chat");
    expect(describeTool("mcp__files__list", { path: "documents" })).toEqual({ label: "Looking in", detail: "documents" });
    expect(describeTool("mcp__web__search", { query: "rain tomorrow" })).toEqual({ label: "Searching the web for", detail: "rain tomorrow" });
    expect(describeTool("memory", {}).label).toBe("Updating what I remember");
    expect(describeTool("mcp__weather__current_city", { city: "Oslo" }).label).toBe("Using weather");
    expect(describeTool("mcp__files__list_resources", {}).label).toBe("Getting ready");
  });
  it("/cards is /surfaces", () => {
    expect(parseCommand("/cards")).toEqual({ kind: "surfaces", ref: undefined });
  });
});
