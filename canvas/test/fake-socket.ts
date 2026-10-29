import { SOCKET_OPEN, type SocketLike } from "../src/renderer/gateway";

/** In-memory stand-in for a browser WebSocket, driven by the test. */
export class FakeSocket implements SocketLike {
  readyState = 0;
  sent: any[] = [];
  closed: { code?: number; reason?: string } | null = null;
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: { code?: number; reason?: string }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;

  constructor(readonly url: string) {}

  send(data: string): void {
    if (this.readyState !== SOCKET_OPEN) throw new Error("not open");
    this.sent.push(JSON.parse(data));
  }

  close(code?: number, reason?: string): void {
    this.closed = { code, reason };
    this.readyState = 3;
  }

  // --- test controls -------------------------------------------------------

  open(): void {
    this.readyState = SOCKET_OPEN;
    this.onopen?.({});
  }

  receive(frame: unknown): void {
    this.onmessage?.({ data: typeof frame === "string" ? frame : JSON.stringify(frame) });
  }

  event(type: string, payload: unknown = {}, sessionId?: string): void {
    this.receive({ jsonrpc: "2.0", method: "event", params: { type, ...(sessionId ? { session_id: sessionId } : {}), payload } });
  }

  serverClose(code = 1006, reason = ""): void {
    this.readyState = 3;
    this.onclose?.({ code, reason });
  }

  /** The last request with this method the client sent. */
  lastRequest(method: string): any {
    return [...this.sent].reverse().find((f) => f.method === method);
  }

  reply(method: string, result: unknown): void {
    const req = this.lastRequest(method);
    if (!req) throw new Error(`no ${method} request`);
    this.receive({ jsonrpc: "2.0", id: req.id, result });
  }
}
