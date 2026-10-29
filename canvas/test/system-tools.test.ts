import { describe, expect, it } from "vitest";
import { resolveSystemTool } from "../src/renderer/system-tools";
import { normalizeApproval, normalizeClarify, normalizeSecret } from "../src/renderer/builtin-views/normalize";

describe("resolveSystemTool", () => {
  it("maps approval_respond to {choice}, only for offered choices", () => {
    expect(resolveSystemTool("approval", {}, "approval_respond", { choice: "once" })).toEqual({ ok: true, result: { choice: "once" } });
    expect(resolveSystemTool("approval", { choices: ["once", "deny"] }, "approval_respond", { choice: "always" })).toMatchObject({ ok: false });
    expect(resolveSystemTool("approval", {}, "approval_respond", { choice: "yolo" })).toMatchObject({ ok: false });
    expect(resolveSystemTool("approval", {}, "approval_respond", {})).toMatchObject({ ok: false });
  });

  it("maps clarify_respond to {answers} with string|null values", () => {
    expect(resolveSystemTool("clarify", {}, "clarify_respond", { answers: { a: "x", b: null, c: 3 } })).toEqual({
      ok: true,
      result: { answers: { a: "x", b: null, c: null } },
    });
    expect(resolveSystemTool("clarify", {}, "clarify_respond", { answers: [] })).toMatchObject({ ok: false });
  });

  it("maps secret_respond to {value} and secret_cancel to an error", () => {
    expect(resolveSystemTool("secret", {}, "secret_respond", { value: "s3cret" })).toEqual({ ok: true, result: { value: "s3cret" } });
    expect(resolveSystemTool("secret", {}, "secret_respond", { value: 1 })).toMatchObject({ ok: false });
    expect(resolveSystemTool("secret", {}, "secret_cancel", {})).toEqual({ ok: true, fail: "cancelled by the user" });
    expect(resolveSystemTool("sudo", {}, "secret_respond", { value: "pw" })).toEqual({ ok: true, result: { value: "pw" } });
  });

  it("refuses tools that do not belong to the prompt kind", () => {
    expect(resolveSystemTool("approval", {}, "secret_respond", { value: "x" })).toMatchObject({ ok: false });
    expect(resolveSystemTool("link", {}, "approval_respond", { choice: "once" })).toMatchObject({ ok: false });
    expect(resolveSystemTool("link", {}, "link_close", {})).toEqual({ ok: true });
  });
});

describe("normalize", () => {
  it("reads common approval spellings and defaults the choices", () => {
    const a = normalizeApproval({ tool: "mcp_email_send", arguments: { to: "x" }, reason: "outbound" });
    expect(a).toMatchObject({ tool: "mcp_email_send", args: { to: "x" }, description: "outbound", choices: ["once", "session", "always", "deny"] });
    expect(normalizeApproval({ command: "rm -rf /tmp/x", choices: ["once", "deny"] })).toMatchObject({ command: "rm -rf /tmp/x", choices: ["once", "deny"] });
    expect(normalizeApproval(null).choices).toHaveLength(4);
  });

  it("reads clarify questions in list or single form", () => {
    expect(normalizeClarify({ questions: [{ id: "q1", question: "When?", choices: ["now", { label: "later" }] }] }).questions).toEqual([
      { id: "q1", text: "When?", choices: ["now", "later"] },
    ]);
    expect(normalizeClarify({ question: "Which?", id: "x" }).questions).toEqual([{ id: "x", text: "Which?", choices: [] }]);
  });

  it("reads secret prompts", () => {
    expect(normalizeSecret({ env_var: "API_KEY", description: "Paste it" })).toEqual({ prompt: "Paste it", name: "API_KEY" });
  });
});
