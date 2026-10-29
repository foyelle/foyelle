/**
 * Approvals, clarifying questions, secrets, sudo and link notices, rendered
 * as built-in MCP App views through the same sandboxed iframe + AppBridge
 * path as connector surfaces. The host answers their `tools/call` for the
 * host-local tools by settling the pending gateway server request.
 */
import type { CallToolResult } from "@modelcontextprotocol/client";
import approvalHtml from "builtin-view:approval";
import clarifyHtml from "builtin-view:clarify";
import secretHtml from "builtin-view:secret";
import linkHtml from "builtin-view:link";
import type { ServerRequest } from "./gateway";
import { h } from "./dom";
import { mountSurface, type HostEnvironment, type MountedSurface } from "./surfaces";
import { normalizeApproval } from "./builtin-views/normalize";
import { resolveSystemTool, type PromptKind } from "./system-tools";

const VIEWS: Record<PromptKind, string> = {
  approval: approvalHtml,
  clarify: clarifyHtml,
  secret: secretHtml,
  sudo: secretHtml,
  link: linkHtml,
};

const TITLES: Record<PromptKind, string> = {
  approval: "Before I go on",
  clarify: "Question",
  secret: "Secret",
  sudo: "Administrator password",
  link: "Link",
};

export interface PromptItem {
  id: string;
  kind: PromptKind;
  request: ServerRequest | null;
  input: Record<string, unknown>;
  title: string;
}

let counter = 0;
function newKey(): string {
  counter++;
  return `sys-${counter.toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export class SystemPrompts {
  private readonly queue: PromptItem[] = [];
  private current: { item: PromptItem; mounted: MountedSurface | null } | null = null;
  private advancing = false;

  constructor(
    private readonly layer: HTMLElement,
    private readonly host: HTMLElement,
    private readonly head: HTMLElement,
    private readonly env: HostEnvironment,
    private readonly onChange: () => void,
    private readonly maxHeight: () => number,
  ) {}

  /** Number of approvals waiting (shown and queued). */
  get pendingApprovals(): number {
    return this.all().filter((i) => i.kind === "approval").length;
  }

  get active(): boolean {
    return !!this.current;
  }

  all(): PromptItem[] {
    return [...(this.current ? [this.current.item] : []), ...this.queue];
  }

  /** Accepts a gateway server request; returns false if this is not a prompt kind. */
  enqueue(req: ServerRequest): boolean {
    const kind = req.method as PromptKind;
    if (!["approval", "clarify", "secret", "sudo"].includes(kind)) return false;
    const item: PromptItem = {
      id: String(req.id),
      kind,
      request: req,
      input: { kind, request: req.params ?? {} },
      title: kind === "approval" ? approvalTitle(req.params) : TITLES[kind],
    };
    req.signal.addEventListener("abort", () => this.remove(item.id), { once: true });
    this.push(item);
    return true;
  }

  showLink(url: string, from: string): void {
    this.push({ id: `link-${++counter}`, kind: "link", request: null, input: { url, from }, title: TITLES.link });
  }

  /** Withdrawn by the server (request.cancel) or answered. */
  remove(id: string): void {
    const qi = this.queue.findIndex((i) => i.id === id);
    if (qi >= 0) {
      this.queue.splice(qi, 1);
      this.renderHead();
      this.onChange();
      return;
    }
    if (this.current?.item.id === id) void this.advance();
  }

  private push(item: PromptItem): void {
    this.queue.push(item);
    this.onChange();
    if (!this.current && !this.advancing) void this.advance();
    else this.renderHead();
  }

  private async advance(): Promise<void> {
    this.advancing = true;
    try {
      const prev = this.current;
      this.current = null;
      if (prev?.mounted) await prev.mounted.teardown(800);
      const next = this.queue.shift();
      if (!next) {
        this.layer.hidden = true;
        this.onChange();
        return;
      }
      this.current = { item: next, mounted: null };
      this.layer.hidden = false;
      this.renderHead();
      this.onChange();
      const wrapper = h("div", { class: "system-frame-wrap" });
      this.host.append(wrapper);
      try {
        const mounted = await mountSurface(
          wrapper,
          {
            key: newKey(),
            title: next.title,
            html: VIEWS[next.kind],
            kind: "system",
            toolInput: next.input,
            handlers: {
              callTool: async (p) => this.handleTool(next, p.name, p.arguments ?? {}),
              sizeChanged: (size) => {
                if (typeof size.height === "number") {
                  wrapper.style.height = `${Math.max(120, Math.min(size.height, this.maxHeight()))}px`;
                }
              },
            },
          },
          this.env,
        );
        if (this.current?.item === next) this.current.mounted = mounted;
        else await mounted.teardown(200); // withdrawn while mounting
      } catch (err) {
        wrapper.remove();
        this.env.log?.error("[canvas] could not show system prompt", err);
        next.request?.fail("canvas could not show the prompt");
        if (this.current?.item === next) await this.advance();
      }
    } finally {
      this.advancing = false;
    }
  }

  private handleTool(item: PromptItem, name: string, args: Record<string, unknown>): CallToolResult {
    const outcome = resolveSystemTool(item.kind, item.request?.params, name, args);
    if (!outcome.ok) return { content: [{ type: "text", text: outcome.error }], isError: true };
    let sent = true;
    if (item.request) {
      sent = outcome.fail ? item.request.fail(outcome.fail) : item.request.respond(outcome.result);
    }
    queueMicrotask(() => this.remove(item.id));
    return sent
      ? { content: [{ type: "text", text: "ok" }] }
      : { content: [{ type: "text", text: "The request is no longer pending." }], isError: true };
  }

  private renderHead(): void {
    const cur = this.current?.item;
    const waiting = this.queue.length;
    // The view names itself; the head only appears when more prompts wait.
    this.head.setAttribute("aria-label", cur ? cur.title : "");
    if (waiting) this.head.replaceChildren(h("span", { class: "system-queue" }, `${waiting} more waiting after this`));
    else this.head.replaceChildren();
  }

  /** Re-sends host context (theme, size) to the visible prompt. */
  updateHostContext(): void {
    this.current?.mounted?.setHostContext(this.env.hostContext());
  }
}

/** The card header: what kind of approval this is, in a few words. */
function approvalTitle(params: unknown): string {
  const kind = normalizeApproval(params).request?.kind;
  return kind === "outbound" || kind === "tainted"
    ? "Leaving your home"
    : kind === "system"
      ? "Changing your computer"
      : kind === "private-remote"
        ? "Sharing with the online model"
        : kind === "unreviewed"
          ? "A tool Foyelle hasn't reviewed"
        : "Before I go on";
}
