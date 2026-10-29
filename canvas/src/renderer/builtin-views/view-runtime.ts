/**
 * A minimal MCP Apps View runtime (the View side of SEP-1865) for the
 * canvas's built-in views. It speaks the same postMessage JSON-RPC protocol
 * as the ext-apps `App` class (ui/initialize, ui/notifications/initialized,
 * tool-input, tool-result, host-context-changed, size-changed,
 * ui/resource-teardown, tools/call), so built-in views go through exactly
 * the same AppBridge path as any connector surface. It exists because the
 * `App` class bundles to ~590 KB with its dependencies, and these views are
 * a few kilobytes each.
 */

export const PROTOCOL_VERSION = "2026-01-26";

export interface HostContext {
  theme?: "light" | "dark";
  displayMode?: string;
  containerDimensions?: Record<string, number>;
  styles?: { variables?: Record<string, string | undefined> };
  /** Foyelle's own extras for its views (display font, highlight colours): see theme.ts. */
  foyelle?: { styles?: Record<string, string | undefined> };
  [key: string]: unknown;
}

export interface CallToolResult {
  content: Array<{ type: string; text?: string; [k: string]: unknown }>;
  structuredContent?: Record<string, unknown>;
  isError?: boolean;
}

interface WindowLike {
  postMessage(message: unknown, targetOrigin: string): void;
}

interface ListenTarget {
  addEventListener(type: "message", listener: (ev: MessageEvent) => void): void;
  removeEventListener(type: "message", listener: (ev: MessageEvent) => void): void;
}

export interface ViewOptions {
  name: string;
  version?: string;
  /** Window to talk to (default `window.parent`). */
  parent?: WindowLike;
  /** Where `message` events arrive (default `window`). */
  listenOn?: ListenTarget;
  requestTimeoutMs?: number;
}

type Pending = { resolve: (v: any) => void; reject: (e: Error) => void; timer: ReturnType<typeof setTimeout> };

export class ViewConnection {
  hostContext: HostContext = {};
  hostInfo: { name?: string; version?: string } = {};
  ontoolinput?: (args: Record<string, unknown>) => void;
  ontoolresult?: (result: CallToolResult) => void;
  ontoolcancelled?: (reason?: string) => void;
  onhostcontextchanged?: (ctx: HostContext) => void;
  onteardown?: () => void | Promise<void>;

  private readonly parent: WindowLike;
  private readonly listenOn: ListenTarget;
  private readonly timeoutMs: number;
  private nextId = 1;
  private readonly pending = new Map<number, Pending>();
  private readonly listener = (ev: MessageEvent) => this.onMessage(ev);

  constructor(private readonly opts: ViewOptions) {
    this.parent = opts.parent ?? window.parent;
    this.listenOn = opts.listenOn ?? window;
    this.timeoutMs = opts.requestTimeoutMs ?? 0;
  }

  /** Performs the ui/initialize handshake and announces readiness. */
  async connect(): Promise<void> {
    this.listenOn.addEventListener("message", this.listener);
    const result = await this.request("ui/initialize", {
      appInfo: { name: this.opts.name, version: this.opts.version ?? "1.0.0" },
      appCapabilities: { availableDisplayModes: ["inline"] },
      protocolVersion: PROTOCOL_VERSION,
    });
    this.hostContext = (result?.hostContext as HostContext) ?? {};
    this.hostInfo = result?.hostInfo ?? {};
    this.notify("ui/notifications/initialized", {});
  }

  close(): void {
    this.listenOn.removeEventListener("message", this.listener);
    for (const p of this.pending.values()) {
      clearTimeout(p.timer);
      p.reject(new Error("closed"));
    }
    this.pending.clear();
  }

  callTool(name: string, args: Record<string, unknown> = {}): Promise<CallToolResult> {
    return this.request("tools/call", { name, arguments: args });
  }

  sendMessage(text: string): Promise<unknown> {
    return this.request("ui/message", { role: "user", content: [{ type: "text", text }] });
  }

  openLink(url: string): Promise<unknown> {
    return this.request("ui/open-link", { url });
  }

  sendSizeChanged(width: number, height: number): void {
    this.notify("ui/notifications/size-changed", { width: Math.ceil(width), height: Math.ceil(height) });
  }

  /** Reports the document size whenever it changes. */
  autoResize(el: Element = document.documentElement): void {
    let last = "";
    const report = () => {
      // The element's own box (not scrollHeight, which grows with the
      // iframe and would feed back into the reported size).
      const r = el.getBoundingClientRect();
      const key = `${Math.ceil(r.width)}x${Math.ceil(r.height)}`;
      if (key === last) return;
      last = key;
      this.sendSizeChanged(r.width, r.height);
    };
    new ResizeObserver(report).observe(el);
    report();
  }

  request(method: string, params: Record<string, unknown>): Promise<any> {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = this.timeoutMs
        ? setTimeout(() => {
            this.pending.delete(id);
            reject(new Error(`${method} timed out`));
          }, this.timeoutMs)
        : (0 as unknown as ReturnType<typeof setTimeout>);
      this.pending.set(id, { resolve, reject, timer });
      this.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
    });
  }

  notify(method: string, params: Record<string, unknown>): void {
    this.parent.postMessage({ jsonrpc: "2.0", method, params }, "*");
  }

  private respond(id: unknown, result: unknown): void {
    this.parent.postMessage({ jsonrpc: "2.0", id, result }, "*");
  }

  private respondError(id: unknown, code: number, message: string): void {
    this.parent.postMessage({ jsonrpc: "2.0", id, error: { code, message } }, "*");
  }

  private onMessage(ev: MessageEvent): void {
    if (ev.source !== (this.parent as unknown)) return;
    const msg = ev.data;
    if (!msg || typeof msg !== "object" || msg.jsonrpc !== "2.0") return;

    // Response to one of our requests.
    if (msg.method === undefined && msg.id !== undefined) {
      const p = this.pending.get(msg.id);
      if (!p) return;
      this.pending.delete(msg.id);
      clearTimeout(p.timer);
      if (msg.error) p.reject(new Error(msg.error.message ?? "error"));
      else p.resolve(msg.result);
      return;
    }

    if (typeof msg.method !== "string") return;
    const params = msg.params ?? {};

    // Requests from the host.
    if (msg.id !== undefined) {
      if (msg.method === "ui/resource-teardown") {
        Promise.resolve(this.onteardown?.())
          .catch(() => undefined)
          .then(() => this.respond(msg.id, {}));
      } else if (msg.method === "ping") {
        this.respond(msg.id, {});
      } else {
        this.respondError(msg.id, -32601, `Method not found: ${msg.method}`);
      }
      return;
    }

    // Notifications from the host.
    switch (msg.method) {
      case "ui/notifications/tool-input":
        this.ontoolinput?.(params.arguments ?? {});
        break;
      case "ui/notifications/tool-result":
        this.ontoolresult?.(params as CallToolResult);
        break;
      case "ui/notifications/tool-cancelled":
        this.ontoolcancelled?.(params.reason);
        break;
      case "ui/notifications/host-context-changed":
        this.hostContext = { ...this.hostContext, ...params };
        this.onhostcontextchanged?.(this.hostContext);
        break;
      default:
        break;
    }
  }
}

/** Applies the host theme and style variables to the document. */
export function applyHostContext(ctx: HostContext, root: HTMLElement = document.documentElement): void {
  if (ctx.theme) root.dataset.theme = ctx.theme;
  for (const vars of [ctx.styles?.variables, ctx.foyelle?.styles]) {
    if (!vars) continue;
    for (const [k, v] of Object.entries(vars)) {
      if (typeof v === "string" && k.startsWith("--")) root.style.setProperty(k, v);
    }
  }
}

/** `textContent` helper for building view DOM without innerHTML. */
export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs: Record<string, string> = {},
  ...children: Array<Node | string | null | undefined>
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) {
    if (c === null || c === undefined) continue;
    node.append(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

/** Connects a view and wires theme handling; returns the connection. */
export async function startView(name: string, setup: (view: ViewConnection) => void): Promise<ViewConnection> {
  const view = new ViewConnection({ name, version: "0.1.0" });
  view.onhostcontextchanged = (ctx) => applyHostContext(ctx);
  setup(view);
  await view.connect();
  applyHostContext(view.hostContext);
  view.autoResize();
  return view;
}
