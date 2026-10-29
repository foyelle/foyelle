/**
 * Host-local tools of the built-in views, mapped onto gateway server
 * request responses (architecture.md §1):
 *   approval → {choice}, clarify → {answers}, secret → {value}.
 */
import { APPROVAL_CHOICES, normalizeApproval } from "./builtin-views/normalize";

export type PromptKind = "approval" | "clarify" | "secret" | "sudo" | "link";

export type ToolOutcome =
  | { ok: true; result?: Record<string, unknown>; fail?: string }
  | { ok: false; error: string };

export function resolveSystemTool(kind: PromptKind, params: unknown, name: string, args: Record<string, unknown>): ToolOutcome {
  switch (kind) {
    case "approval": {
      if (name !== "approval_respond") break;
      const offered = normalizeApproval(params).choices;
      const choice = args.choice;
      if (typeof choice !== "string" || !offered.includes(choice) || !(APPROVAL_CHOICES as string[]).includes(choice)) {
        return { ok: false, error: `choice must be one of: ${offered.join(", ")}` };
      }
      return { ok: true, result: { choice } };
    }
    case "clarify": {
      if (name !== "clarify_respond") break;
      const raw = args.answers;
      if (!raw || typeof raw !== "object" || Array.isArray(raw)) return { ok: false, error: "answers must be an object" };
      const answers: Record<string, string | null> = {};
      for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
        answers[k] = typeof v === "string" ? v : null;
      }
      return { ok: true, result: { answers } };
    }
    case "secret":
    case "sudo": {
      if (name === "secret_cancel") return { ok: true, fail: "cancelled by the user" };
      if (name !== "secret_respond") break;
      if (typeof args.value !== "string") return { ok: false, error: "value must be a string" };
      // `sudo`'s response shape is not fixed by the contract; send both spellings.
      return { ok: true, result: { value: args.value } };
    }
    case "link":
      if (name === "link_close") return { ok: true };
      break;
  }
  return { ok: false, error: `unknown tool ${name}` };
}
