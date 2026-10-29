/**
 * Normalises the params of gateway server requests for the built-in views.
 * The contract fixes the responses but not every request field name, so the
 * views accept the common spellings and always also show the raw params.
 */

export type ApprovalChoice = "once" | "session" | "always" | "deny";

export const APPROVAL_CHOICES: ApprovalChoice[] = ["once", "session", "always", "deny"];

export const CHOICE_LABELS: Record<string, string> = {
  once: "Allow once",
  session: "Allow for this conversation",
  always: "Always allow",
  deny: "Not now",
};

/** The structured line Foyelle's policy adds to an approval (harness/plugin, describe()). */
export interface FoyelleRequest {
  kind: "outbound" | "system" | "tainted" | "private-remote" | "unknown" | string;
  connector: string | null;
  tool: string | null;
  tier: string | null;
  origin: string | null;
  args: Record<string, unknown>;
}

const MARKER = "Foyelle-Request: ";

/** Splits a policy description into its plain sentence and its structured line, when present. */
export function parseFoyelleRequest(description: string | null): { plain: string | null; request: FoyelleRequest | null } {
  if (!description || !description.includes(MARKER)) return { plain: description, request: null };
  const line = description.split("\n").find((l) => l.startsWith(MARKER));
  let request: FoyelleRequest | null = null;
  try {
    const v = JSON.parse(line!.slice(MARKER.length)) as FoyelleRequest;
    if (v && typeof v === "object") request = { ...v, args: v.args && typeof v.args === "object" ? v.args : {} };
  } catch {
    request = null;
  }
  return { plain: description.split("\n\n")[0]!.trim() || null, request };
}

export interface ApprovalView {
  tool: string | null;
  server: string | null;
  command: string | null;
  args: unknown;
  description: string | null;
  /** "surface" when a surface (not the model) started the call. */
  origin: string | null;
  choices: string[];
  raw: Record<string, unknown>;
  /** Present when Foyelle's policy asked: what kind of approval, for which connector and tool. */
  request: FoyelleRequest | null;
  /** The plain sentence of the description, without the structured and technical parts. */
  plain: string | null;
}

function str(v: unknown): string | null {
  if (typeof v === "string" && v.trim()) return v;
  if (typeof v === "number") return String(v);
  return null;
}

function obj(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

export function normalizeApproval(params: unknown): ApprovalView {
  const a = obj(params);
  const choicesRaw = Array.isArray(a.choices) ? a.choices : Array.isArray(a.options) ? a.options : null;
  const choices = (choicesRaw ?? APPROVAL_CHOICES)
    .map((c) => (typeof c === "string" ? c : str(obj(c).value) ?? str(obj(c).id)))
    .filter((c): c is string => !!c);
  const description = str(a.description) ?? str(a.reason) ?? str(a.message) ?? str(a.prompt) ?? str(a.title);
  const parsed = parseFoyelleRequest(description);
  return {
    request: parsed.request,
    plain: parsed.plain,
    tool: str(a.tool_name) ?? str(a.tool) ?? str(a.name) ?? str(a.function),
    server: str(a.server) ?? str(a.connector),
    command: str(a.command) ?? str(a.cmd),
    args: a.args ?? a.arguments ?? a.input ?? a.tool_args ?? null,
    description: str(a.description) ?? str(a.reason) ?? str(a.message) ?? str(a.prompt) ?? str(a.title),
    origin: str(a.origin),
    choices: choices.length ? choices : [...APPROVAL_CHOICES],
    raw: a,
  };
}

export interface ClarifyQuestion {
  id: string;
  text: string;
  choices: string[];
}

export function normalizeClarify(params: unknown): { title: string | null; questions: ClarifyQuestion[] } {
  const a = obj(params);
  const list: unknown[] = Array.isArray(a.questions) ? a.questions : [a];
  const questions = list.map((q, i) => {
    const o = obj(q);
    const choicesRaw = Array.isArray(o.choices) ? o.choices : Array.isArray(o.options) ? o.options : [];
    return {
      id: str(o.id) ?? str(o.qid) ?? str(o.key) ?? (list.length === 1 ? str(a.id) ?? "q0" : `q${i}`),
      text: str(o.question) ?? str(o.text) ?? str(o.prompt) ?? str(o.message) ?? "The agent has a question.",
      choices: choicesRaw
        .map((c) => (typeof c === "string" ? c : str(obj(c).label) ?? str(obj(c).value)))
        .filter((c): c is string => !!c),
    };
  });
  return { title: list.length > 1 ? str(a.title) : null, questions };
}

export function normalizeSecret(params: unknown): { prompt: string; name: string | null } {
  const a = obj(params);
  return {
    prompt: str(a.prompt) ?? str(a.description) ?? str(a.message) ?? str(a.text) ?? "The agent needs a secret value.",
    name: str(a.name) ?? str(a.key) ?? str(a.env_var) ?? str(a.var),
  };
}

export function prettyJson(v: unknown): string {
  try {
    return JSON.stringify(v, null, 2) ?? String(v);
  } catch {
    return String(v);
  }
}
