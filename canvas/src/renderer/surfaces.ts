/**
 * MCP Apps host for one surface: a sandboxed iframe loaded from the host's
 * surface protocol, connected with ext-apps `AppBridge` + `PostMessageTransport`.
 *
 * The iframe has `sandbox="allow-scripts"` and no `allow-same-origin`, so the
 * document has an opaque origin; its CSP comes from the response headers the
 * host serves (see src/shared/csp.ts).
 */
import {
  AppBridge,
  PostMessageTransport,
  type McpUiHostCapabilities,
  type McpUiHostContext,
  type McpUiResourceCsp,
} from "@modelcontextprotocol/ext-apps/app-bridge";
import type { CallToolResult, ReadResourceResult } from "@modelcontextprotocol/client";
import type { FoyelleBridge } from "../shared/bridge";
import type { SurfaceCsp } from "../shared/csp";

export const HOST_INFO = { name: "foyelle-canvas", version: "0.1.0" };

export interface SurfaceHandlers {
  /** `tools/call` from the View. */
  callTool(params: { name: string; arguments?: Record<string, unknown> }): Promise<CallToolResult>;
  /** `resources/read` from the View (app surfaces only). */
  readResource?(params: { uri: string }): Promise<ReadResourceResult>;
  /** `ui/message`: text the View wants to send as the user. */
  message?(text: string): Promise<void>;
  /** `ui/open-link`. */
  openLink?(url: string): Promise<void>;
  /** `ui/request-display-mode` asked for more than inline: treat as "focus me". */
  requestFocus?(): void;
  /** `ui/notifications/request-teardown`. */
  requestClose?(): void;
  /** `ui/notifications/size-changed`. */
  sizeChanged?(size: { width?: number; height?: number }): void;
}

export interface MountSpec {
  /** Local key; also the authority of the surface URL. */
  key: string;
  title: string;
  html: string;
  csp?: SurfaceCsp | null;
  kind: "app" | "system";
  toolInput?: Record<string, unknown>;
  toolResult?: CallToolResult;
  handlers: SurfaceHandlers;
}

export interface HostEnvironment {
  foyelle: FoyelleBridge;
  hostContext(): McpUiHostContext;
  log?: Pick<Console, "debug" | "warn" | "error">;
}

/** Capabilities the canvas offers a View. System views get only `serverTools`. */
export function hostCapabilities(kind: "app" | "system", csp?: SurfaceCsp | null): McpUiHostCapabilities {
  if (kind === "system") return { serverTools: {} };
  return {
    openLinks: {},
    serverTools: {},
    serverResources: {},
    logging: {},
    message: { text: {} },
    sandbox: { csp: (csp ?? {}) as McpUiResourceCsp },
  };
}

/** Joins the text blocks of a `ui/message` into one prompt. */
export function messageText(content: unknown): string {
  if (!Array.isArray(content)) return "";
  return content
    .map((b) => (b && typeof b === "object" && (b as { type?: string }).type === "text" ? String((b as { text?: unknown }).text ?? "") : ""))
    .filter(Boolean)
    .join("\n\n")
    .trim();
}

/** Sent to a View when the agent changed its helper's data, so it can reload what it shows. */
export const CHANGED_NOTIFICATION = "foyelle/notifications/changed";

/** Wires the canvas's handlers onto an AppBridge. Exported for tests. */
export function wireBridge(bridge: AppBridge, spec: Pick<MountSpec, "kind" | "toolInput" | "toolResult" | "handlers">, log: Pick<Console, "debug" | "warn" | "error"> = console): Promise<void> {
  const h = spec.handlers;

  bridge.oncalltool = async (params) => {
    try {
      return await h.callTool({ name: params.name, arguments: (params.arguments ?? {}) as Record<string, unknown> });
    } catch (err) {
      return { content: [{ type: "text", text: (err as Error).message || "tool call failed" }], isError: true };
    }
  };

  if (spec.kind === "app") {
    bridge.onreadresource = async (params) => {
      if (!h.readResource) throw new Error("resources are not available");
      return h.readResource({ uri: params.uri });
    };
    bridge.onmessage = async (params) => {
      const text = messageText(params.content);
      if (!text || !h.message) return { isError: true };
      try {
        await h.message(text);
        return {};
      } catch {
        return { isError: true };
      }
    };
    bridge.onopenlink = async (params) => {
      if (!h.openLink) return { isError: true };
      await h.openLink(params.url);
      return {};
    };
    bridge.onloggingmessage = (params) => log.debug("[surface]", params.level, params.data);
  }

  bridge.onrequestdisplaymode = async (params) => {
    if (params.mode !== "inline") h.requestFocus?.();
    return { mode: "inline" };
  };
  bridge.onsizechange = (params) => h.sizeChanged?.(params);
  bridge.onrequestteardown = () => h.requestClose?.();

  return new Promise<void>((resolve) => {
    bridge.oninitialized = () => {
      void (async () => {
        try {
          if (spec.toolInput !== undefined || spec.toolResult !== undefined) {
            await bridge.sendToolInput({ arguments: spec.toolInput ?? {} });
          }
          if (spec.toolResult !== undefined) await bridge.sendToolResult(spec.toolResult);
        } catch (err) {
          log.warn("[surface] could not deliver tool data", err);
        }
        resolve();
      })();
    };
  });
}

export class MountedSurface {
  readonly frame: HTMLIFrameElement;
  /** Resolves once the View has initialised and received its tool data. */
  readonly ready: Promise<void>;
  private torn = false;

  constructor(
    readonly spec: MountSpec,
    readonly wrapper: HTMLElement,
    frame: HTMLIFrameElement,
    readonly bridge: AppBridge,
    ready: Promise<void>,
    private readonly env: HostEnvironment,
  ) {
    this.frame = frame;
    this.ready = ready;
  }

  get key(): string {
    return this.spec.key;
  }

  setHostContext(ctx: McpUiHostContext): void {
    if (this.torn) return;
    try {
      this.bridge.setHostContext(ctx);
    } catch (err) {
      this.env.log?.debug("[surface] host context not sent", err);
    }
  }

  /**
   * Tells the View that the agent changed something in its helper's data (Foyelle's own
   * notification, outside the MCP Apps set; a View that doesn't know it ignores it).
   */
  notifyChanged(params: { server: string; tool: string }): void {
    if (this.torn) return;
    const send = (this.bridge as unknown as { notification(n: { method: string; params: unknown }): Promise<void> }).notification;
    send.call(this.bridge, { method: CHANGED_NOTIFICATION, params }).catch((err: unknown) =>
      this.env.log?.debug("[surface] change notice not sent", err));
  }

  /** Asks the View to tear down (bounded wait), then removes the frame and its registration. */
  async teardown(timeoutMs = 1500): Promise<void> {
    if (this.torn) return;
    this.torn = true;
    try {
      await this.bridge.teardownResource({}, { timeout: timeoutMs });
    } catch {
      /* the View may be gone or slow; remove it anyway */
    }
    try {
      await this.bridge.close();
    } catch {
      /* ignore */
    }
    this.wrapper.remove();
    try {
      await this.env.foyelle.unregisterSurface(this.spec.key);
    } catch {
      /* ignore */
    }
  }
}

/**
 * Creates the iframe inside `wrapper`, connects the bridge before the
 * document loads (so `ui/initialize` is never missed), then points the
 * frame at the registered surface URL.
 */
export async function mountSurface(wrapper: HTMLElement, spec: MountSpec, env: HostEnvironment): Promise<MountedSurface> {
  const url = await env.foyelle.registerSurface({ surfaceId: spec.key, html: spec.html, csp: spec.csp ?? null });

  const frame = document.createElement("iframe");
  frame.setAttribute("sandbox", "allow-scripts");
  frame.setAttribute("referrerpolicy", "no-referrer");
  frame.setAttribute("title", spec.title);
  frame.className = "surface-frame";
  wrapper.append(frame);

  const win = frame.contentWindow;
  if (!win) throw new Error("surface frame has no window");

  const bridge = new AppBridge(null, HOST_INFO, hostCapabilities(spec.kind, spec.csp), {
    hostContext: env.hostContext(),
  });
  const ready = wireBridge(bridge, spec, env.log);
  await bridge.connect(new PostMessageTransport(win, win));
  frame.src = url;
  return new MountedSurface(spec, wrapper, frame, bridge, ready, env);
}
