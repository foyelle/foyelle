/**
 * The command bar is both the chat input and the command line. Plain text is
 * a prompt; text starting with `/` is a local command. `//text` sends the
 * literal text `/text` as a prompt.
 */

export type Command =
  | { kind: "empty" }
  | { kind: "prompt"; text: string }
  | { kind: "focus"; ref: string }
  | { kind: "pin"; ref?: string }
  | { kind: "unpin"; ref?: string }
  | { kind: "dismiss"; ref?: string }
  | { kind: "surfaces"; ref?: string }
  | { kind: "new" }
  | { kind: "welcome" }
  | { kind: "accounts" }
  | { kind: "stop" }
  | { kind: "help" }
  | { kind: "harness"; command: string }
  | { kind: "error"; message: string };

export const COMMAND_HELP: ReadonlyArray<[string, string]> = [
  ["/focus <n|name>", "Bring a card to the front"],
  ["/pin [n|name]", "Keep a card on the side"],
  ["/unpin [n|name]", "Stop keeping it"],
  ["/dismiss [n|name]", "Close a card"],
  ["/cards [name]", "Every card, to search and open, or open one by name (also /surfaces)"],
  ["/new", "Start a new chat (also /clear, or the New chat button)"],
  ["/stop", "Stop what I'm doing"],
  ["/welcome", "Meet Foyelle again: your name, the look, the key"],
  ["/accounts", "Connect your mail and calendar"],
  ["/help", "Show this list"],
  ["/skills pending", "Skills I learned, waiting for your review"],
  ["/<other>", "Other commands go to Foyelle"],
];

const NAMES = ["focus", "pin", "unpin", "dismiss", "surfaces", "cards", "new", "clear", "stop", "welcome", "accounts", "help"] as const;

export function parseCommand(input: string): Command {
  const raw = input.replace(/\s+$/, "");
  if (!raw.trim()) return { kind: "empty" };
  const trimmedStart = raw.replace(/^\s+/, "");
  if (!trimmedStart.startsWith("/")) return { kind: "prompt", text: raw.trim() };
  if (trimmedStart.startsWith("//")) return { kind: "prompt", text: trimmedStart.slice(1).trim() };

  const m = /^\/(\S+)\s*([\s\S]*)$/.exec(trimmedStart);
  if (!m) return { kind: "error", message: "Empty command. Type /help for the list." };
  const name = m[1]!.toLowerCase();
  const arg = m[2]!.trim();
  const ref = arg || undefined;

  switch (name) {
    case "focus":
    case "f":
      if (!ref) return { kind: "error", message: "Usage: /focus <number or name>" };
      return { kind: "focus", ref };
    case "pin":
      return { kind: "pin", ref };
    case "unpin":
      return { kind: "unpin", ref };
    case "dismiss":
    case "close":
      return { kind: "dismiss", ref };
    case "surfaces":
    case "cards":
    case "s":
      return { kind: "surfaces", ref };
    case "new":
    case "clear":
      return arg ? { kind: "error", message: `/${name} takes no arguments` } : { kind: "new" };
    case "stop":
      return arg ? { kind: "error", message: "/stop takes no arguments" } : { kind: "stop" };
    case "welcome":
      return arg ? { kind: "error", message: "/welcome takes no arguments" } : { kind: "welcome" };
    case "accounts":
      return arg ? { kind: "error", message: "/accounts takes no arguments" } : { kind: "accounts" };
    case "help":
    case "?":
      return { kind: "help" };
    default: {
      // A prefix of a canvas command is a typo for it; anything else is the harness's own
      // command (for example /skills pending, /skills diff <id>, /skills approve <id>).
      const near = NAMES.find((n) => n.startsWith(name));
      if (near && name.length < near.length) {
        return { kind: "error", message: `Unknown command /${name}. Did you mean /${near}?` };
      }
      return { kind: "harness", command: trimmedStart.trim() };
    }
  }
}

/** Completion for the command bar: the commands matching a `/` prefix. */
export function completions(input: string): string[] {
  const m = /^\/(\S*)$/.exec(input.trim());
  if (!m) return [];
  const p = m[1]!.toLowerCase();
  return NAMES.filter((n) => n.startsWith(p)).map((n) => `/${n}`);
}
