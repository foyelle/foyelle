/**
 * Client for the harness gateway: Hermes's legacy `tui_gateway` JSON-RPC 2.0
 * dialect over one WebSocket, one JSON frame per text message.
 *
 *  - client → server requests with numeric ids, timeouts
 *  - server events `{"method":"event","params":{type, session_id?, payload}}`
 *  - server → client requests (`approval`, `clarify`, `secret`, `sudo`) with
 *    string ids, answered by `respond` / `fail`, withdrawn by `request.cancel`
 *  - `client.capabilities {server_requests:true}` after `gateway.ready`
 *  - heartbeat with `ping`, reconnect with exponential backoff
 */

export type GatewayState = "idle" | "connecting" | "handshaking" | "ready" | "reconnecting" | "stopped";

/** The subset of the browser WebSocket the client needs (makes it testable). */
export interface SocketLike {
  readonly readyState: number;
  send(data: string): void;
  close(code?: number, reason?: string): void;
  onopen: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: ((ev: { code?: number; reason?: string }) => void) | null;
  onerror: ((ev: unknown) => void) | null;
}

export const SOCKET_OPEN = 1;

export interface GatewayEvent<P = any> {
  type: string;
  session_id?: string;
  payload: P;
}

export interface ServerRequest<P = any> {
  readonly id: string | number;
  readonly method: string;
  readonly params: P;
  /** Aborted when the server withdraws the request or the connection drops. */
  readonly signal: AbortSignal;
  readonly settled: boolean;
  /** Sends `result`. Returns false if the request was already settled or withdrawn. */
  respond(result: unknown): boolean;
  /** Sends a JSON-RPC error. Returns false if already settled or withdrawn. */
  fail(message: string, code?: number): boolean;
}

export class RpcError extends Error {
  constructor(
    message: string,
    readonly code: number,
    readonly data?: unknown,
  ) {
    super(message);
    this.name = "RpcError";
  }
}

export const ERR_TIMEOUT = -32001;
export const ERR_NOT_CONNECTED = -32002;
export const ERR_CONNECTION_CLOSED = -32003;
export const ERR_METHOD_NOT_FOUND = -32601;

export interface BackoffOptions {
  initialMs: number;
  maxMs: number;
  factor: number;
  /** 0..1: the delay is scaled by a random factor in [1 - jitter, 1 + jitter]. */
  jitter: number;
}

export interface GatewayOptions {
  /** Returns the URL and token; called before every (re)connect so a rotated token is picked up. */
  config: () => Promise<{ url: string; token: string }>;
  socketFactory?: (url: string) => SocketLike;
  requestTimeoutMs?: number;
  handshakeTimeoutMs?: number;
  pingIntervalMs?: number;
  pingTimeoutMs?: number;
  backoff?: Partial<BackoffOptions>;
  random?: () => number;
  logger?: Pick<Console, "debug" | "warn" | "error">;
}

interface Pending {
  resolve: (v: any) => void;
  reject: (e: Error) => void;
  timer: ReturnType<typeof setTimeout> | null;
  method: string;
}

type EventHandler = (ev: GatewayEvent) => void;
type ServerRequestHandler = (req: ServerRequest) => void;

const DEFAULT_BACKOFF: BackoffOptions = { initialMs: 500, maxMs: 15_000, factor: 2, jitter: 0.2 };

export function buildSocketUrl(url: string, token: string): string {
  if (!token) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}token=${encodeURIComponent(token)}`;
}

export class GatewayClient {
  private readonly opts: Required<Omit<GatewayOptions, "backoff" | "socketFactory" | "logger">> & {
    backoff: BackoffOptions;
    socketFactory: (url: string) => SocketLike;
    logger: Pick<Console, "debug" | "warn" | "error">;
  };
  private socket: SocketLike | null = null;
  private generation = 0;
  private nextId = 1;
  private readonly pending = new Map<number, Pending>();
  private readonly serverRequests = new Map<string | number, ServerRequestImpl>();
  private readonly eventHandlers = new Map<string, Set<EventHandler>>();
  private readonly serverRequestHandlers = new Set<ServerRequestHandler>();
  private readonly stateHandlers = new Set<(s: GatewayState) => void>();
  private _state: GatewayState = "idle";
  private attempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private handshakeTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private _readyInfo: unknown = null;

  constructor(options: GatewayOptions) {
    this.opts = {
      config: options.config,
      socketFactory: options.socketFactory ?? ((url) => new WebSocket(url) as unknown as SocketLike),
      requestTimeoutMs: options.requestTimeoutMs ?? 30_000,
      handshakeTimeoutMs: options.handshakeTimeoutMs ?? 15_000,
      pingIntervalMs: options.pingIntervalMs ?? 20_000,
      pingTimeoutMs: options.pingTimeoutMs ?? 10_000,
      backoff: { ...DEFAULT_BACKOFF, ...options.backoff },
      random: options.random ?? Math.random,
      logger: options.logger ?? console,
    };
  }

  get state(): GatewayState {
    return this._state;
  }

  /** Payload of the last `gateway.ready` event. */
  get readyInfo(): unknown {
    return this._readyInfo;
  }

  /** Number of consecutive failed connection attempts. */
  get reconnectAttempts(): number {
    return this.attempts;
  }

  start(): void {
    if (this._state !== "idle" && this._state !== "stopped") return;
    void this.connect();
  }

  stop(): void {
    this.setState("stopped");
    this.clearTimers();
    this.dropSocket(1000, "client stopped");
    this.failAllPending(new RpcError("client stopped", ERR_CONNECTION_CLOSED));
    this.abortServerRequests("client stopped");
  }

  /** Forces a reconnect now (e.g. a user-triggered retry). */
  reconnectNow(): void {
    if (this._state === "stopped") return;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    if (this._state === "ready" || this._state === "handshaking" || this._state === "connecting") return;
    void this.connect();
  }

  request<T = any>(method: string, params: Record<string, unknown> = {}, options: { timeoutMs?: number } = {}): Promise<T> {
    if (this._state !== "ready") {
      return Promise.reject(new RpcError(`not connected (${this._state})`, ERR_NOT_CONNECTED));
    }
    return this.rawRequest<T>(method, params, options.timeoutMs ?? this.opts.requestTimeoutMs);
  }

  /** Subscribe to events of one type, or `*` for all. Returns an unsubscribe function. */
  on(type: string, handler: EventHandler): () => void {
    let set = this.eventHandlers.get(type);
    if (!set) this.eventHandlers.set(type, (set = new Set()));
    set.add(handler);
    return () => set!.delete(handler);
  }

  onServerRequest(handler: ServerRequestHandler): () => void {
    this.serverRequestHandlers.add(handler);
    return () => this.serverRequestHandlers.delete(handler);
  }

  onState(handler: (s: GatewayState) => void): () => void {
    this.stateHandlers.add(handler);
    return () => this.stateHandlers.delete(handler);
  }

  // ---------------------------------------------------------------------------

  private setState(s: GatewayState): void {
    if (this._state === s) return;
    this._state = s;
    for (const h of [...this.stateHandlers]) {
      try {
        h(s);
      } catch (err) {
        this.opts.logger.error("[gateway] state handler failed", err);
      }
    }
  }

  private async connect(): Promise<void> {
    const gen = ++this.generation;
    this.setState(this.attempts === 0 && this._state !== "reconnecting" ? "connecting" : "reconnecting");
    let cfg: { url: string; token: string };
    try {
      cfg = await this.opts.config();
    } catch (err) {
      if (gen !== this.generation || this._state === "stopped") return;
      this.opts.logger.warn("[gateway] cannot load harness config", err);
      this.scheduleReconnect();
      return;
    }
    if (gen !== this.generation || this._state === "stopped") return;

    let sock: SocketLike;
    try {
      sock = this.opts.socketFactory(buildSocketUrl(cfg.url, cfg.token));
    } catch (err) {
      this.opts.logger.warn("[gateway] cannot open socket", err);
      this.scheduleReconnect();
      return;
    }
    this.socket = sock;
    sock.onopen = () => {
      if (gen !== this.generation) return;
      this.setState("handshaking");
    };
    sock.onmessage = (ev) => {
      if (gen !== this.generation) return;
      this.handleFrame(ev.data);
    };
    sock.onerror = () => {
      /* onclose follows */
    };
    sock.onclose = (ev) => {
      if (gen !== this.generation) return;
      this.handleClose(ev?.code, ev?.reason);
    };
    this.handshakeTimer = setTimeout(() => {
      if (gen !== this.generation || this._state === "ready") return;
      this.opts.logger.warn("[gateway] no gateway.ready in time; reconnecting");
      this.dropSocket(4000, "handshake timeout");
      this.handleClose(4000, "handshake timeout");
    }, this.opts.handshakeTimeoutMs);
  }

  private handleFrame(data: unknown): void {
    if (typeof data !== "string") return;
    let msg: any;
    try {
      msg = JSON.parse(data);
    } catch {
      this.opts.logger.warn("[gateway] dropped malformed frame");
      return;
    }
    const msgs = Array.isArray(msg) ? msg : [msg];
    for (const m of msgs) this.dispatch(m);
  }

  private dispatch(msg: any): void {
    if (!msg || typeof msg !== "object" || msg.jsonrpc !== "2.0") return;
    const hasMethod = typeof msg.method === "string";
    const hasId = msg.id !== undefined && msg.id !== null;

    if (!hasMethod && hasId) {
      this.handleResponse(msg);
    } else if (hasMethod && msg.method === "event") {
      this.handleEvent(msg.params);
    } else if (hasMethod && hasId) {
      this.handleServerRequest(msg);
    }
    // Other notifications are not part of the dialect; ignore them.
  }

  private handleResponse(msg: any): void {
    const id = typeof msg.id === "number" ? msg.id : Number(msg.id);
    const p = this.pending.get(id);
    if (!p) return;
    this.pending.delete(id);
    if (p.timer) clearTimeout(p.timer);
    if (msg.error) {
      const e = msg.error;
      p.reject(new RpcError(String(e.message ?? "error"), Number(e.code ?? -32000), e.data));
    } else {
      p.resolve(msg.result);
    }
  }

  private handleEvent(params: any): void {
    if (!params || typeof params.type !== "string") return;
    const ev: GatewayEvent = { type: params.type, session_id: params.session_id, payload: params.payload ?? {} };

    if (ev.type === "gateway.ready") this.onGatewayReady(ev.payload);
    if (ev.type === "request.cancel") {
      const id = ev.payload?.id;
      const req = id !== undefined ? this.serverRequests.get(id) : undefined;
      if (req) {
        this.serverRequests.delete(id);
        req.withdraw("cancelled by server");
      }
    }
    this.emit(ev);
  }

  private emit(ev: GatewayEvent): void {
    for (const key of [ev.type, "*"]) {
      const set = this.eventHandlers.get(key);
      if (!set) continue;
      for (const h of [...set]) {
        try {
          h(ev);
        } catch (err) {
          this.opts.logger.error(`[gateway] handler for ${ev.type} failed`, err);
        }
      }
    }
  }

  private onGatewayReady(payload: unknown): void {
    if (this.handshakeTimer) clearTimeout(this.handshakeTimer);
    this.handshakeTimer = null;
    this._readyInfo = payload;
    this.attempts = 0;
    // Advertise server→client requests. An older gateway may not know the
    // method; that only means no approvals will arrive, so do not fail.
    this.rawRequest("client.capabilities", { server_requests: true }, this.opts.requestTimeoutMs).catch((err) =>
      this.opts.logger.warn("[gateway] client.capabilities failed", err),
    );
    this.startPing();
    this.setState("ready");
  }

  private handleServerRequest(msg: any): void {
    const req = new ServerRequestImpl(msg.id, msg.method, msg.params ?? {}, (frame) => this.sendFrame(frame), () =>
      this.serverRequests.delete(msg.id),
    );
    this.serverRequests.set(msg.id, req);
    if (this.serverRequestHandlers.size === 0) {
      req.fail(`canvas cannot handle ${msg.method}`, ERR_METHOD_NOT_FOUND);
      return;
    }
    for (const h of [...this.serverRequestHandlers]) {
      try {
        h(req);
      } catch (err) {
        this.opts.logger.error("[gateway] server request handler failed", err);
      }
    }
  }

  private rawRequest<T>(method: string, params: Record<string, unknown>, timeoutMs: number): Promise<T> {
    const id = this.nextId++;
    return new Promise<T>((resolve, reject) => {
      const timer =
        timeoutMs > 0
          ? setTimeout(() => {
              if (this.pending.delete(id)) reject(new RpcError(`${method} timed out after ${timeoutMs} ms`, ERR_TIMEOUT));
            }, timeoutMs)
          : null;
      this.pending.set(id, { resolve, reject, timer, method });
      if (!this.sendFrame({ jsonrpc: "2.0", id, method, params })) {
        this.pending.delete(id);
        if (timer) clearTimeout(timer);
        reject(new RpcError("not connected", ERR_NOT_CONNECTED));
      }
    });
  }

  private sendFrame(frame: unknown): boolean {
    const s = this.socket;
    if (!s || s.readyState !== SOCKET_OPEN) return false;
    try {
      s.send(JSON.stringify(frame));
      return true;
    } catch (err) {
      this.opts.logger.warn("[gateway] send failed", err);
      return false;
    }
  }

  private startPing(): void {
    if (this.pingTimer) clearInterval(this.pingTimer);
    if (this.opts.pingIntervalMs <= 0) return;
    const gen = this.generation;
    this.pingTimer = setInterval(() => {
      if (gen !== this.generation || this._state !== "ready") return;
      this.rawRequest("ping", {}, this.opts.pingTimeoutMs).catch((err: RpcError) => {
        if (gen !== this.generation || this._state !== "ready") return;
        if (err.code === ERR_TIMEOUT || err.code === ERR_NOT_CONNECTED) {
          this.opts.logger.warn("[gateway] heartbeat lost; reconnecting");
          this.dropSocket(4001, "heartbeat timeout");
          this.handleClose(4001, "heartbeat timeout");
        }
      });
    }, this.opts.pingIntervalMs);
  }

  private handleClose(code?: number, reason?: string): void {
    this.generation++; // ignore anything else from the old socket
    this.socket = null;
    this.clearTimers();
    this.failAllPending(new RpcError(`connection closed${reason ? `: ${reason}` : ""}`, ERR_CONNECTION_CLOSED));
    this.abortServerRequests("connection closed");
    if (this._state === "stopped") return;
    this.opts.logger.debug?.(`[gateway] closed (${code ?? "?"})`);
    this.scheduleReconnect();
  }

  private scheduleReconnect(): void {
    if (this._state === "stopped") return;
    const b = this.opts.backoff;
    const base = Math.min(b.maxMs, b.initialMs * b.factor ** this.attempts);
    const scale = 1 - b.jitter + this.opts.random() * 2 * b.jitter;
    const delay = Math.max(0, Math.round(base * scale));
    this.attempts++;
    this.setState("reconnecting");
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      void this.connect();
    }, delay);
  }

  private dropSocket(code: number, reason: string): void {
    const s = this.socket;
    this.socket = null;
    if (!s) return;
    s.onopen = s.onmessage = s.onerror = s.onclose = null;
    try {
      s.close(code, reason);
    } catch {
      /* ignore */
    }
  }

  private clearTimers(): void {
    if (this.handshakeTimer) clearTimeout(this.handshakeTimer);
    if (this.pingTimer) clearInterval(this.pingTimer);
    if (this.reconnectTimer && this._state === "stopped") clearTimeout(this.reconnectTimer);
    this.handshakeTimer = null;
    this.pingTimer = null;
  }

  private failAllPending(err: RpcError): void {
    const all = [...this.pending.values()];
    this.pending.clear();
    for (const p of all) {
      if (p.timer) clearTimeout(p.timer);
      p.reject(err);
    }
  }

  private abortServerRequests(reason: string): void {
    const all = [...this.serverRequests.values()];
    this.serverRequests.clear();
    for (const r of all) r.withdraw(reason);
  }
}

class ServerRequestImpl implements ServerRequest {
  private readonly controller = new AbortController();
  private _settled = false;

  constructor(
    readonly id: string | number,
    readonly method: string,
    readonly params: any,
    private readonly send: (frame: unknown) => boolean,
    private readonly forget: () => void,
  ) {}

  get signal(): AbortSignal {
    return this.controller.signal;
  }

  get settled(): boolean {
    return this._settled;
  }

  respond(result: unknown): boolean {
    if (this._settled) return false;
    this._settled = true;
    this.forget();
    return this.send({ jsonrpc: "2.0", id: this.id, result: result ?? {} });
  }

  fail(message: string, code = -32000): boolean {
    if (this._settled) return false;
    this._settled = true;
    this.forget();
    return this.send({ jsonrpc: "2.0", id: this.id, error: { code, message } });
  }

  withdraw(reason: string): void {
    if (this._settled) return;
    this._settled = true;
    this.controller.abort(new Error(reason));
  }
}
