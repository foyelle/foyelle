/**
 * The web demo's helpers: the real connectors' answers on the sample data, recorded by
 * demo/record.mjs, played back to the mock harness in the page. Some of a visit's changes are kept
 * here, so the canvas answers to them as on a real Foyelle: the settings (the visitor's name,
 * light or dark, the theme), what is waiting for the visitor, and a document written over.
 * Everything else answers as it was recorded, and a call that was not recorded gets a friendly
 * error that the card shows.
 */
import type { ToolsHost } from "../dev/mock-harness";

export interface Recording {
  surfaces: Array<{ server: string; uri: string; name?: string; title?: string }>;
  /** "server uri" -> the card's page (a file in cards/), title and CSP. */
  pages: Record<string, { file: string; title: string | null; csp: unknown }>;
  /** Theme id -> shell.settings_get with that theme on. */
  settings: Record<string, CallResult>;
  /** "server.tool {args as sorted JSON}" -> the result. */
  replies: Record<string, CallResult>;
}

interface CallResult {
  content?: Array<{ type: string; text?: string }>;
  structuredContent?: unknown;
  isError?: boolean;
}

/** Arguments that change with every call (a time, a conversation's id), so any recorded answer does. */
const LOOSE = new Set(["shell.routine_results", "shell.conversation_services"]);

export const NOT_IN_DEMO =
  "This demo only has recorded answers, and not one for this. On a real Foyelle it would work.";

export function argsKey(args: unknown): string {
  return JSON.stringify(args, (_, v) =>
    v && typeof v === "object" && !Array.isArray(v) ? Object.fromEntries(Object.entries(v).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))) : v,
  );
}

const text = (data: unknown): CallResult => ({ content: [{ type: "text", text: JSON.stringify(data, null, 2) }] });
const failed = (message: string): CallResult => ({ content: [{ type: "text", text: message }], isError: true });
const parse = (r: CallResult | undefined): any => {
  try {
    return JSON.parse(r?.content?.[0]?.text ?? "{}");
  } catch {
    return {};
  }
};

export class RecordedTools implements ToolsHost {
  /** Settings changed in this visit, as overrides ("ui.style" -> "tokyo-night"). */
  private readonly overrides = new Map<string, unknown>();
  /** Waiting items approved or dropped in this visit. */
  private readonly dropped = new Set<string>();
  /** Documents written over in this visit: path -> text. */
  private readonly written = new Map<string, string>();

  constructor(
    private readonly rec: Recording,
    /** The card's page by its file name (fetched from cards/). */
    private readonly page: (file: string) => Promise<string>,
  ) {}

  async surfaces() {
    return this.rec.surfaces;
  }

  async resource(server: string, uri: string): Promise<{ html: string; csp: unknown; title?: string }> {
    const p = this.rec.pages[`${server} ${uri}`];
    if (!p) throw new Error(`The demo has no ${uri} card.`);
    return { html: await this.page(p.file), csp: p.csp, ...(p.title ? { title: p.title } : {}) };
  }

  async call(server: string, name: string, args: Record<string, unknown>): Promise<CallResult> {
    const id = `${server}.${name}`;
    switch (id) {
      case "shell.settings_get":
        return this.settings();
      case "shell.settings_set":
        return this.set(String(args.key), args.value);
      case "shell.user_name_set":
        this.overrides.set("user.name", String(args.name ?? "").trim());
        return text({ name: this.overrides.get("user.name") });
      case "shell.theme_use": {
        const theme = this.themes().find((t) => t.id === args.theme_id);
        if (!theme) return failed(`There is no theme ${String(args.theme_id)}.`);
        this.overrides.set("ui.style", theme.id);
        return text({ current: theme.id, name: theme.name });
      }
      case "shell.theme_list":
        return this.themeList();
      case "shell.waiting_list":
        return this.waitingList();
      case "shell.waiting_drop": {
        const item = String(args.item_id);
        if (!this.waitingItems().some((w) => w.id === item && !this.dropped.has(item))) return failed("that item is no longer waiting");
        this.dropped.add(item);
        return text({ dropped: item });
      }
      case "files.write":
        return this.write(String(args.path), String(args.content ?? ""), args.overwrite === true);
      case "files.read":
        if (this.written.has(String(args.path)) && !args.offset) return this.readWritten(String(args.path));
    }
    const reply = this.rec.replies[`${id} ${argsKey(args)}`];
    if (reply) return reply;
    if (LOOSE.has(id)) {
      const any = Object.keys(this.rec.replies).find((k) => k.startsWith(`${id} `));
      if (any) return this.rec.replies[any]!;
    }
    return failed(NOT_IN_DEMO);
  }

  private waitingItems(): Array<{ id: string }> {
    return parse(this.rec.replies["shell.waiting_list {}"]).items ?? [];
  }

  private waitingList(): CallResult {
    return text({ items: this.waitingItems().filter((w) => !this.dropped.has(w.id)) });
  }

  /** Over a document the recording read; a new one isn't in the demo. */
  private write(path: string, content: string, overwrite: boolean): CallResult {
    if (!this.rec.replies[`files.read ${argsKey({ path })}`]) return failed(NOT_IN_DEMO);
    if (!overwrite) return failed(`${path} exists; pass overwrite=true to replace it (the old version is kept)`);
    this.written.set(path, content);
    const size = new TextEncoder().encode(content).length;
    return text({ path, size, operation: "overwrite", operation_id: `demo-${this.written.size}` });
  }

  private readWritten(path: string): CallResult {
    const doc = parse(this.rec.replies[`files.read ${argsKey({ path })}`]);
    const content = this.written.get(path)!;
    return text({ ...doc, text: content, size: new TextEncoder().encode(content).length, truncated: false, next_offset: null });
  }

  private themes(): Array<{ id: string; name: string }> {
    return parse(this.rec.replies["shell.theme_list {}"]).themes ?? [];
  }

  private style(): string {
    return String(this.overrides.get("ui.style") ?? "ember");
  }

  private set(key: string, value: unknown): CallResult {
    const allowed = parse(this.rec.settings.ember).allowed ?? {};
    if (!(key in allowed)) return failed(`${key} is not a setting.`);
    const previous = this.overrides.get(key) ?? null;
    this.overrides.set(key, value);
    return text({ key, value, previous, overrides: Object.fromEntries(this.overrides) });
  }

  /** The recorded settings with this theme on, and this visit's changes over them. */
  private settings(): CallResult {
    const doc = parse(this.rec.settings[this.style()] ?? this.rec.settings.ember);
    for (const [key, value] of this.overrides) {
      const [group, field] = key.split(".") as [string, string];
      doc.effective ??= {};
      doc.effective[group] = { ...doc.effective[group], [field]: value };
      doc.overrides = { ...doc.overrides, [key]: value };
    }
    return text(doc);
  }

  private themeList(): CallResult {
    const doc = parse(this.rec.replies["shell.theme_list {}"]);
    doc.current = this.style();
    const scheme = this.overrides.get("ui.theme");
    if (scheme === "light" || scheme === "dark") doc.scheme = scheme;
    return text(doc);
  }
}
