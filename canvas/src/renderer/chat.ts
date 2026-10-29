/** The chat log in the rail: streaming assistant text, tool rows, notices. */
import { describeTool } from "./words";
import { h } from "./dom";
import { renderMarkdown } from "./markdown";

interface ToolRow {
  row: HTMLElement;
  state: HTMLElement;
  detail: HTMLElement;
}

export class ChatLog {
  private current: { el: HTMLElement; body: HTMLElement; text: string; frame: number | null } | null = null;
  /**
   * Text of the bubbles already closed in this turn (a tool row closes the
   * bubble before it). `message.complete` carries the whole message text, so
   * only the part after this prefix belongs in the last bubble.
   */
  private turnText = "";
  private readonly tools = new Map<string, ToolRow>();

  /** Follow new output unless the user has scrolled up to read. */
  private stuck = true;

  constructor(private readonly root: HTMLElement) {
    root.addEventListener("scroll", () => {
      this.stuck = root.scrollHeight - root.scrollTop - root.clientHeight < 80;
    });
    new ResizeObserver(() => {
      if (this.stuck) this.toBottom();
    }).observe(root);
  }

  private toBottom(): void {
    this.root.scrollTop = this.root.scrollHeight;
  }

  private add(node: HTMLElement): HTMLElement {
    this.root.append(node);
    if (this.stuck) this.toBottom();
    return node;
  }

  private stickAfter(fn: () => void): void {
    fn();
    if (this.stuck) this.toBottom();
  }

  clear(): void {
    this.root.replaceChildren();
    this.current = null;
    this.turnText = "";
    this.tools.clear();
  }

  user(text: string, via?: string): void {
    this.stuck = true; // sending something always jumps back to the latest
    const body = h("div", { class: "msg-body" });
    body.innerHTML = renderMarkdown(text);
    this.add(h("div", { class: "msg msg-user" }, via ? h("div", { class: "msg-via" }, `from ${via}`) : null, body));
  }

  /** A new assistant message (`message.start`). */
  messageStart(): void {
    this.finishCurrent();
    this.turnText = "";
    this.assistantStart();
  }

  assistantStart(): void {
    this.finishCurrent();
    const body = h("div", { class: "msg-body" });
    const el = h("div", { class: "msg msg-assistant streaming" }, body);
    this.add(el);
    this.current = { el, body, text: "", frame: null };
  }

  assistantDelta(text: string): void {
    if (!this.current) this.assistantStart();
    const cur = this.current!;
    cur.text += text;
    if (cur.frame === null) {
      cur.frame = requestAnimationFrame(() => {
        cur.frame = null;
        this.stickAfter(() => (cur.body.innerHTML = renderMarkdown(cur.text)));
      });
    }
  }

  /** `message.complete`: the authoritative full text of the message. */
  messageComplete(text: string | undefined, status?: string, error?: string): void {
    let rest = typeof text === "string" ? text : undefined;
    if (rest !== undefined && this.turnText && rest.startsWith(this.turnText)) rest = rest.slice(this.turnText.length);
    if (rest !== undefined && !this.current && !rest.trim() && !error && (!status || status === "complete")) {
      this.turnText = "";
      return;
    }
    this.assistantComplete(rest, status, error);
    this.turnText = "";
  }

  private assistantComplete(text: string | undefined, status?: string, error?: string): void {
    if (!this.current && ((text && text.trim()) || error || (status && status !== "complete"))) this.assistantStart();
    const cur = this.current;
    if (cur) {
      if (cur.frame !== null) cancelAnimationFrame(cur.frame);
      const final = typeof text === "string" && text.trim().length ? text : cur.text;
      this.stickAfter(() => {
        cur.body.innerHTML = renderMarkdown(final);
        cur.el.classList.remove("streaming");
        if (!final.trim()) cur.el.classList.add("empty");
        if (status && status !== "complete" && status !== "completed" && status !== "ok") {
          cur.el.append(h("div", { class: "msg-status" }, status === "interrupted" ? "Stopped" : status));
        }
        if (error) cur.el.append(h("div", { class: "msg-error" }, error));
      });
    }
    this.current = null;
  }

  private finishCurrent(): void {
    if (!this.current) return;
    this.turnText += this.current.text;
    this.assistantComplete(undefined);
  }

  toolStart(id: string, name: string, args: unknown): void {
    const state = h("span", { class: "tool-state running", "aria-label": "running" });
    // Plain words for what is happening; the tool's own name and arguments on hover.
    const said = describeTool(name, args);
    const detail = h("span", { class: "tool-detail" }, said.detail ?? "");
    const row = h("div", { class: "tool-row", title: `${prettyToolName(name)}  ${summarizeArgs(args, 300)}`.trim() }, state, h("span", { class: "tool-name" }, said.label), detail);
    row.title = name;
    // A tool row closes the streaming bubble; an empty one is dropped so later
    // text appears below the tool row, in order.
    if (this.current && this.current.text.trim()) this.finishCurrent();
    else if (this.current) {
      if (this.current.frame !== null) cancelAnimationFrame(this.current.frame);
      this.turnText += this.current.text;
      this.current.el.remove();
      this.current = null;
    }
    this.add(row);
    this.tools.set(id, { row, state, detail });
  }

  toolComplete(id: string, name: string, summary?: string, isError?: boolean): void {
    let t = this.tools.get(id);
    if (!t) {
      this.toolStart(id, name, undefined);
      t = this.tools.get(id)!;
    }
    t.state.className = `tool-state ${isError ? "failed" : "done"}`;
    t.state.setAttribute("aria-label", isError ? "failed" : "done");
    // Keep the plain description; the tool's own summary is technical, so it goes on hover,
    // except a failure, which the user should read.
    if (summary) t.row.title = `${t.row.title}\n${summary}`.trim();
    if (isError && summary) t.detail.textContent = summary;
    this.tools.delete(id);
  }

  notice(text: string, kind: "info" | "error" | "warn" = "info"): HTMLElement {
    return this.add(h("div", { class: `notice notice-${kind}`, role: kind === "error" ? "alert" : undefined }, text));
  }

  /** What a routine said while nobody was asking: a note with its name and time, and the answer. */
  routineNote(name: string, when: string, text: string, failed: boolean): void {
    this.finishCurrent();
    const body = h("div", { class: "msg-body" });
    body.innerHTML = renderMarkdown(text || (failed ? "It didn't finish." : "Nothing to report."));
    this.add(
      h("div", { class: `msg msg-routine${failed ? " is-failed" : ""}` },
        h("div", { class: "routine-head" }, h("span", { class: "routine-glyph", "aria-hidden": "true" }), h("span", { class: "routine-name" }, name), h("span", { class: "routine-when" }, when)),
        body),
    );
  }

  divider(label: string): void {
    this.finishCurrent();
    this.add(h("div", { class: "divider" }, h("span", {}, label)));
  }

  /** A numbered list with optional actions (used by /surfaces and /help). */
  list(title: string, rows: Array<{ label: string; detail?: string; action?: () => void; key?: string }>): void {
    const items = rows.map((r, i) => {
      const key = r.key ?? String(i + 1);
      const content = [key ? h("span", { class: "list-key" }, key) : null, h("span", { class: "list-label" }, r.label), r.detail ? h("span", { class: "list-detail" }, r.detail) : null];
      if (!r.action) return h("li", {}, ...content);
      const b = h("button", { type: "button", class: "list-btn" }, ...content);
      b.addEventListener("click", r.action);
      return h("li", {}, b);
    });
    this.add(h("div", { class: "list-card" }, h("div", { class: "list-title" }, title), h("ol", {}, ...items)));
  }

  surfaceLink(label: string, onFocus: () => void): void {
    const b = h("button", { type: "button", class: "surface-link" }, h("span", { class: "surface-glyph", "aria-hidden": "true" }), label);
    b.addEventListener("click", onFocus);
    this.add(h("div", { class: "surface-row" }, b));
  }
}

export function prettyToolName(name: string): string {
  // mcp__<server>__<tool> (or the older mcp_<server>_<tool>) -> server · tool
  const m = /^mcp__([a-z0-9-]+)__(.+)$/i.exec(name) ?? /^mcp_([a-z0-9-]+)_(.+)$/i.exec(name);
  return m ? `${m[1]} · ${m[2]}` : name;
}

export function summarizeArgs(args: unknown, max = 80): string {
  if (args === undefined || args === null) return "";
  let s: string;
  if (typeof args === "string") s = args;
  else if (typeof args === "object" && !Array.isArray(args)) {
    s = Object.entries(args as Record<string, unknown>)
      .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
      .join("  ");
  } else s = JSON.stringify(args);
  s = s.replace(/\s+/g, " ").trim();
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

export { describeTool };
