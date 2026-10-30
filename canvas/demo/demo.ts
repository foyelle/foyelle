/**
 * The web demo (foyelle.org/demo): the canvas in a visitor's browser with no server and no model.
 * Loaded before app.js, it stands in for the Electron preload as dev/dev-shim.ts does, and:
 *
 *  - runs the mock harness (dev/mock-harness.ts) in the page, behind a WebSocket that never leaves
 *    it: the canvas's gateway client talks to it as to the real harness;
 *  - answers the helpers' tools from the recording (demo/recorded-tools.ts);
 *  - serves every card from a static file, cards/<sha-256 of its page>, which the site sends with
 *    the cards' Content-Security-Policy (site/_headers). The iframe stays sandboxed without
 *    allow-same-origin, as in Electron, so a card has an opaque origin and only its own policy;
 *  - says it is a demo, offers the scripted paths as suggestions, and on a phone shows the
 *    conversation and the screen one at a time.
 */
import { MockHarness, type MockConnection } from "../dev/mock-harness";
import type { FoyelleBridge, HarnessConfig, RegisterSurfaceSpec, Transcript } from "../src/shared/bridge";
import cardFiles from "demo-cards";
import { RecordedTools, type Recording } from "./recorded-tools";
import recording from "./recording.json";

/** The scripted paths: a short label, and what it asks (shown in the conversation as typed). */
const SUGGESTIONS: Array<{ label: string; text: string }> = [
  { label: "My documents", text: "What's in my documents?" },
  { label: "A thank-you note", text: "Write a thank-you note to Rita for the dinner" },
  { label: "An email (asks first)", text: "Email the Q3 report to Jo" },
  { label: "The themes", text: "Show me the themes" },
  { label: "Tokyo Night", text: "Switch to the Tokyo Night theme" },
  { label: "All the cards", text: "/cards" },
];

async function sha256(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

const tools = new RecordedTools(recording as Recording, async (file) => {
  const url = cardFiles.byName[file];
  if (!url) throw new Error(`The demo has no ${file}.`);
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Could not load the card (${res.status}).`);
  return res.text();
});
const harness = new MockHarness({
  demo: true,
  model: "canned-answers:demo",
  tools,
  loadSurfaceHtml: () => Promise.reject(new Error("not in the demo")),
});

/** A WebSocket that never leaves the page: the other end is the mock harness. */
class DemoSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  readyState = DemoSocket.CONNECTING;
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: { code?: number; reason?: string }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  private toHarness: ((text: string) => void) | null = null;
  private readonly closed: Array<() => void> = [];

  constructor(_url: string) {
    setTimeout(() => {
      if (this.readyState !== DemoSocket.CONNECTING) return;
      this.readyState = DemoSocket.OPEN;
      this.onopen?.({});
      const conn: MockConnection = {
        // Asynchronous, in order, as frames arrive over a socket.
        send: (frame) =>
          setTimeout(() => {
            if (this.readyState === DemoSocket.OPEN) this.onmessage?.({ data: JSON.stringify(frame) });
          }, 0),
        onMessage: (cb) => (this.toHarness = cb),
        onClose: (cb) => this.closed.push(cb),
      };
      harness.connect(conn);
    }, 20);
  }

  send(data: string): void {
    if (this.readyState !== DemoSocket.OPEN) throw new Error("the demo's socket is not open");
    const deliver = this.toHarness;
    setTimeout(() => deliver?.(data), 0);
  }

  close(code = 1000, reason = ""): void {
    if (this.readyState >= DemoSocket.CLOSING) return;
    this.readyState = DemoSocket.CLOSED;
    for (const cb of this.closed) cb();
    setTimeout(() => this.onclose?.({ code, reason }), 0);
  }
}
(window as unknown as { WebSocket: unknown }).WebSocket = DemoSocket;

const bridge: FoyelleBridge = {
  platform: "web",
  async harness(): Promise<HarnessConfig> {
    return { url: "ws://demo.invalid/api/ws", token: "", surfaceTitles: { workshop: "Workshop" }, services: { todoist: "Todoist" } };
  },
  async registerSurface(spec: RegisterSurfaceSpec): Promise<string> {
    // Only pages built into the demo, each from its own file with the cards' policy.
    const url = cardFiles.bySha[await sha256(spec.html)];
    if (!url) throw new Error("This card isn't part of the demo.");
    return url;
  },
  async unregisterSurface(): Promise<void> {},
  onShortcut() {
    return () => undefined;
  },
  async transcribe(): Promise<Transcript> {
    throw new Error("Talking needs a real Foyelle.");
  },
  setZoom() {},
  async signinBegin() {
    throw new Error("Signing in needs a real Foyelle.");
  },
  async signinOpen() {
    throw new Error("Signing in needs a real Foyelle.");
  },
  async signinEnd() {},
};
(window as unknown as { foyelle: FoyelleBridge }).foyelle = bridge;

// ----- what the visitor sees around the canvas -------------------------------------------------

function el<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Record<string, string>, ...children: Array<Node | string>): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  e.append(...children);
  return e;
}

/** On a phone the conversation and the screen take turns; the switch is in the banner. */
type View = "chat" | "screen";
const root = document.documentElement;
const tabs = new Map<View, HTMLButtonElement>();
function show(view: View): void {
  root.dataset.demoView = view;
  for (const [v, b] of tabs) b.setAttribute("aria-pressed", String(v === view));
}

const viewSwitch = el("div", { class: "demo-views", role: "group", "aria-label": "Show" });
for (const [view, label] of [["chat", "Chat"], ["screen", "Screen"]] as const) {
  const b = el("button", { type: "button", "aria-pressed": "false" }, label);
  b.addEventListener("click", () => show(view));
  tabs.set(view, b);
  viewSwitch.append(b);
}
const banner = el(
  "header",
  { class: "demo-banner", role: "note" },
  el("span", { class: "demo-flag" }, "Demo with canned answers, no AI model"),
  el("span", { class: "demo-more" }, "It all runs in your browser. Nothing you type leaves it."),
  viewSwitch,
  el("a", { class: "demo-home", href: "/" }, "About Foyelle"),
);
document.body.prepend(banner);
root.classList.add("demo");

const chips = el("div", { class: "demo-chips", role: "group", "aria-label": "Try" }, el("span", { class: "demo-try" }, "Try"));
for (const s of SUGGESTIONS) {
  const chip = el("button", { type: "button", class: "home-chip demo-chip" }, s.label);
  chip.addEventListener("click", () => {
    const input = document.getElementById("input") as HTMLTextAreaElement | null;
    const form = document.getElementById("bar") as HTMLFormElement | null;
    if (!input || !form) return;
    input.value = s.text;
    input.dispatchEvent(new Event("input"));
    form.requestSubmit();
    show("chat");
  });
  chips.append(chip);
}
document.getElementById("bar")?.before(chips);

show("screen"); // the welcome comes first
const empty = document.getElementById("empty");
const frames = document.getElementById("frames");
const layer = document.getElementById("system-layer");
let welcoming = false;
if (empty) {
  new MutationObserver(() => {
    const now = empty.classList.contains("is-welcome");
    if (welcoming && !now) show("chat");
    if (now) show("screen");
    welcoming = now;
  }).observe(empty, { attributes: true, attributeFilter: ["class"] });
}
// A card that opens, and a card asking for an approval, bring the screen up.
if (frames) {
  new MutationObserver((records) => {
    if (records.some((r) => [...r.addedNodes].some((n) => n instanceof HTMLElement))) show("screen");
  }).observe(frames, { childList: true });
}
if (layer) {
  new MutationObserver(() => show(layer.hidden ? "chat" : "screen")).observe(layer, { attributes: true, attributeFilter: ["hidden"] });
}
