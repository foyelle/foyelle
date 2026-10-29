/**
 * The surface area: one focused surface plus a strip of tiles (pinned, then
 * recent). Iframes never move in the DOM (moving one would reload it); they
 * live in one absolutely positioned layer and are placed over the focus slot
 * or scaled down over a tile preview with a CSS transform.
 */
import { h } from "./dom";
import { SurfaceState, type Change } from "./surface-state";
import { mountSurface, type HostEnvironment, type MountedSurface, type SurfaceHandlers } from "./surfaces";
import type { SurfaceCsp } from "../shared/csp";
import type { CallToolResult } from "@modelcontextprotocol/client";

export interface AppSurfaceMeta {
  surfaceId: string | null;
  server: string | null;
  uri: string | null;
  sessionId: string | null;
  tool: string | null;
}

interface Slot {
  key: string;
  meta: AppSurfaceMeta;
  wrapper: HTMLElement;
  mounted: MountedSurface | null;
  reportedHeight: number | null;
  closing: boolean;
}

export interface OpenSpec {
  title: string;
  html: string;
  csp?: SurfaceCsp | null;
  meta: AppSurfaceMeta;
  toolInput?: Record<string, unknown>;
  toolResult?: CallToolResult;
  handlers: (key: string) => SurfaceHandlers;
  /** Replace an open, unpinned card showing the same view (the agent asking again), not stack another. */
  replaceSame?: boolean;
}

let counter = 0;
function newKey(): string {
  counter++;
  return `s${counter.toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export class Stage {
  readonly state = new SurfaceState({ maxPinned: 4, maxRecent: 3 });
  private readonly slots = new Map<string, Slot>();
  private readonly replacedBy = new Map<string, string>();
  private readonly tilePreviews = new Map<string, HTMLElement>();
  private layoutFrame: number | null = null;

  constructor(
    private readonly area: HTMLElement,
    private readonly focusHead: HTMLElement,
    private readonly focusBox: HTMLElement,
    private readonly tiles: HTMLElement,
    private readonly frames: HTMLElement,
    private readonly empty: HTMLElement,
    private readonly env: HostEnvironment,
    private readonly onChange: () => void,
  ) {
    new ResizeObserver(() => {
      this.scheduleLayout();
      this.pushHostContext();
    }).observe(this.focusBox);
    this.tiles.addEventListener("scroll", () => this.scheduleLayout(), { passive: true });
  }

  /** Size the focused surface gets; also used as host context container size. */
  focusSize(): { width: number; height: number } {
    const r = this.focusBox.getBoundingClientRect();
    return { width: Math.max(320, r.width), height: Math.max(240, r.height) };
  }

  get count(): number {
    return this.state.size;
  }

  title(key: string): string {
    return this.state.get(key)?.title ?? key;
  }

  findBySurfaceId(surfaceId: string): string | null {
    for (const s of this.slots.values()) if (s.meta.surfaceId === surfaceId) return s.key;
    return null;
  }

  async open(spec: OpenSpec): Promise<string> {
    if (spec.meta.surfaceId) {
      const existing = this.findBySurfaceId(spec.meta.surfaceId);
      if (existing) await this.dismiss(existing);
    }
    const key = newKey();
    if (spec.replaceSame && spec.meta.server && spec.meta.uri) {
      for (const [k, s] of [...this.slots]) {
        if (s.meta.server === spec.meta.server && s.meta.uri === spec.meta.uri && !this.state.get(k)?.pinned) {
          this.replacedBy.set(k, key);
          await this.dismiss(k);
        }
      }
    }
    const wrapper = h("div", { class: "surface-wrap", "data-key": key });
    this.frames.append(wrapper);
    const slot: Slot = { key, meta: spec.meta, wrapper, mounted: null, reportedHeight: null, closing: false };
    this.slots.set(key, slot);
    this.apply(this.state.open(key, spec.title));

    const handlers = spec.handlers(key);
    try {
      slot.mounted = await mountSurface(
        wrapper,
        {
          key,
          title: spec.title,
          html: spec.html,
          csp: spec.csp ?? null,
          kind: "app",
          toolInput: spec.toolInput,
          toolResult: spec.toolResult,
          handlers: {
            ...handlers,
            sizeChanged: (size) => {
              if (typeof size.height === "number" && size.height > 0) {
                slot.reportedHeight = size.height;
                this.scheduleLayout();
              }
              handlers.sizeChanged?.(size);
            },
          },
        },
        this.env,
      );
      if (slot.closing || !this.slots.has(key)) await slot.mounted.teardown(200);
    } catch (err) {
      this.env.log?.error("[canvas] could not mount surface", err);
      await this.dismiss(key);
      throw err;
    }
    return key;
  }

  meta(key: string): AppSurfaceMeta | undefined {
    return this.slots.get(key)?.meta;
  }

  focus(key: string): Change {
    // A card that was replaced by a newer one of the same view: its link shows the newer one.
    let k = key;
    for (let i = 0; i < 50 && !this.slots.has(k) && this.replacedBy.has(k); i++) k = this.replacedBy.get(k)!;
    return this.apply(this.state.focus(k));
  }

  pin(key?: string | null): Change {
    return this.apply(this.state.pin(key ?? this.state.focused));
  }

  unpin(key?: string | null): Change {
    return this.apply(this.state.unpin(key ?? this.state.focused));
  }

  async dismiss(key?: string | null): Promise<Change> {
    const change = this.state.dismiss(key ?? this.state.focused);
    if (change.removed) change.evicted.push(change.removed);
    this.apply(change);
    return change;
  }

  async dismissAll(): Promise<void> {
    for (const key of [...this.slots.keys()]) await this.dismiss(key);
  }

  /** The agent changed something through `server`: open cards from that server may reload. */
  notifyServer(server: string, tool: string): void {
    for (const s of this.slots.values()) if (s.meta.server === server) s.mounted?.notifyChanged({ server, tool });
  }

  pushHostContext(): void {
    const ctx = this.env.hostContext();
    for (const s of this.slots.values()) s.mounted?.setHostContext(ctx);
  }

  private apply(change: Change): Change {
    for (const key of change.evicted) this.teardown(key);
    this.render();
    this.onChange();
    return change;
  }

  private teardown(key: string): void {
    const slot = this.slots.get(key);
    if (!slot) return;
    this.slots.delete(key);
    slot.closing = true;
    slot.wrapper.classList.add("closing");
    if (slot.mounted) void slot.mounted.teardown();
    else slot.wrapper.remove();
  }

  /** Rebuilds the focus header and tile strip, then places the frames. */
  render(): void {
    const focused = this.state.focused;
    const entry = focused ? this.state.get(focused) : undefined;
    this.empty.hidden = !!entry;
    this.area.classList.toggle("has-focus", !!entry);

    if (entry) {
      const pinBtn = h("button", { type: "button", class: "head-btn", "aria-pressed": String(entry.pinned) }, entry.pinned ? "Let go" : "Keep");
      pinBtn.addEventListener("click", () => (entry.pinned ? this.unpin(entry.key) : this.pin(entry.key)));
      const closeBtn = h("button", { type: "button", class: "head-btn" }, "Close", h("kbd", {}, "Esc"));
      closeBtn.addEventListener("click", () => void this.dismiss(entry.key));
      const meta = this.slots.get(entry.key)?.meta;
      this.focusHead.replaceChildren(
        h("div", { class: "focus-title" }, entry.pinned ? h("span", { class: "pin-mark", title: "Kept" }) : null, h("span", {}, entry.title)),
        h("div", { class: "focus-actions" }, pinBtn, closeBtn),
      );
    } else {
      this.focusHead.replaceChildren();
    }

    const tiles = this.state.tiles();
    this.tilePreviews.clear();
    this.tiles.hidden = tiles.length === 0;
    this.area.classList.toggle("has-tiles", tiles.length > 0);
    this.tiles.replaceChildren(
      ...tiles.map((t) => {
        const preview = h("div", { class: "tile-preview" });
        if (!t.focused) this.tilePreviews.set(t.entry.key, preview);
        const btn = h(
          "button",
          { type: "button", class: `tile${t.focused ? " is-focused" : ""}${t.entry.pinned ? " is-pinned" : ""}`, "aria-label": `Focus ${t.entry.title}` },
          preview,
          h(
            "div",
            { class: "tile-label" },
            t.index <= 9 ? h("kbd", {}, `${t.index}`) : null,
            h("span", { class: "tile-title" }, t.entry.title),
            t.entry.pinned ? h("span", { class: "pin-mark", title: "Kept" }) : null,
          ),
        );
        if (t.focused) preview.append(h("span", { class: "tile-here" }, "In front"));
        btn.addEventListener("click", () => this.focus(t.entry.key));
        return btn;
      }),
    );
    this.scheduleLayout();
  }

  scheduleLayout(): void {
    if (this.layoutFrame !== null) return;
    this.layoutFrame = requestAnimationFrame(() => {
      this.layoutFrame = null;
      this.layout();
    });
  }

  private layout(): void {
    const origin = this.frames.getBoundingClientRect();
    const box = this.focusBox.getBoundingClientRect();
    const W = Math.max(1, box.width);
    const H = Math.max(1, box.height);
    const focused = this.state.focused;
    // Tile previews show the top of the surface at the focus width, cropped
    // to a landscape box so a column of tiles stays compact.
    const tileH = Math.min(H, W * 0.62);
    for (const preview of this.tilePreviews.values()) preview.style.aspectRatio = `${W} / ${tileH}`;

    for (const slot of this.slots.values()) {
      const s = slot.wrapper.style;
      s.width = `${W}px`;
      // +2 for the wrapper's 1px border (box-sizing: border-box).
      const h = slot.reportedHeight ? Math.min(Math.max(slot.reportedHeight + 2, 160), H) : H;
      if (slot.key === focused) {
        s.height = `${h}px`;
        s.transform = `translate(${box.left - origin.left}px, ${box.top - origin.top}px)`;
        slot.wrapper.classList.add("is-focused");
        slot.wrapper.classList.remove("is-tile", "is-hidden");
        slot.wrapper.removeAttribute("inert");
        continue;
      }
      const preview = this.tilePreviews.get(slot.key);
      slot.wrapper.classList.remove("is-focused");
      slot.wrapper.setAttribute("inert", "");
      if (!preview) {
        slot.wrapper.classList.add("is-hidden");
        continue;
      }
      const p = preview.getBoundingClientRect();
      const scale = p.width / W;
      s.height = `${Math.min(H, p.height / scale)}px`;
      s.transform = `translate(${p.left - origin.left}px, ${p.top - origin.top}px) scale(${scale})`;
      slot.wrapper.classList.add("is-tile");
      slot.wrapper.classList.remove("is-hidden");
    }
  }
}
