/**
 * Layout state for agent surfaces, with no DOM in it.
 *
 * Rules:
 *  - One surface is focused at a time. A newly opened surface takes focus.
 *  - The user can pin up to `maxPinned` surfaces; pinned surfaces stay as
 *    tiles beside the focused one, in the order they were pinned.
 *  - Unpinned surfaces that lose focus stay as "recent" tiles, most recent
 *    first, up to `maxRecent`; older ones are evicted (torn down).
 *  - Dismissing the focused surface focuses the most recently focused one
 *    left, if any.
 *  - Nothing is ever dragged or resized: the layout follows from this state.
 */

export interface SurfaceEntry {
  key: string;
  title: string;
  pinned: boolean;
  pinnedAt: number;
  lastFocusedAt: number;
  openedAt: number;
}

export interface TileView {
  /** 1-based number used by Ctrl+N and `/focus N`. */
  index: number;
  entry: SurfaceEntry;
  focused: boolean;
}

export interface Change {
  ok: boolean;
  error?: string;
  /** Keys whose surfaces must be torn down. */
  evicted: string[];
}

export interface SurfaceStateOptions {
  maxPinned?: number;
  maxRecent?: number;
}

export class SurfaceState {
  readonly maxPinned: number;
  readonly maxRecent: number;
  private readonly entries = new Map<string, SurfaceEntry>();
  private _focused: string | null = null;
  private clock = 0;

  constructor(opts: SurfaceStateOptions = {}) {
    this.maxPinned = opts.maxPinned ?? 4;
    this.maxRecent = opts.maxRecent ?? 3;
  }

  get focused(): string | null {
    return this._focused;
  }

  get size(): number {
    return this.entries.size;
  }

  get(key: string): SurfaceEntry | undefined {
    return this.entries.get(key);
  }

  has(key: string): boolean {
    return this.entries.has(key);
  }

  all(): SurfaceEntry[] {
    return [...this.entries.values()].sort((a, b) => a.openedAt - b.openedAt);
  }

  pinned(): SurfaceEntry[] {
    return [...this.entries.values()].filter((e) => e.pinned).sort((a, b) => a.pinnedAt - b.pinnedAt);
  }

  /** Unpinned, unfocused surfaces, most recently focused first. */
  recent(): SurfaceEntry[] {
    return [...this.entries.values()]
      .filter((e) => !e.pinned && e.key !== this._focused)
      .sort((a, b) => b.lastFocusedAt - a.lastFocusedAt);
  }

  /**
   * The tile strip: pinned surfaces in pin order (the focused one included,
   * marked as focused, so numbers stay stable), then recent surfaces.
   */
  tiles(): TileView[] {
    const list = [...this.pinned(), ...this.recent()];
    return list.map((entry, i) => ({ index: i + 1, entry, focused: entry.key === this._focused }));
  }

  open(key: string, title: string): Change {
    const existing = this.entries.get(key);
    if (existing) {
      existing.title = title || existing.title;
      return this.focus(key);
    }
    const t = ++this.clock;
    this.entries.set(key, { key, title: title || "Card", pinned: false, pinnedAt: 0, lastFocusedAt: t, openedAt: t });
    this._focused = key;
    return { ok: true, evicted: this.evict() };
  }

  focus(key: string): Change {
    const e = this.entries.get(key);
    if (!e) return { ok: false, error: "no such card", evicted: [] };
    e.lastFocusedAt = ++this.clock;
    this._focused = key;
    return { ok: true, evicted: this.evict() };
  }

  pin(key: string | null = this._focused): Change {
    const e = key ? this.entries.get(key) : undefined;
    if (!e) return { ok: false, error: "nothing to pin", evicted: [] };
    if (e.pinned) return { ok: true, evicted: [] };
    if (this.pinned().length >= this.maxPinned) {
      return { ok: false, error: `at most ${this.maxPinned} cards can be kept; let one go first`, evicted: [] };
    }
    e.pinned = true;
    e.pinnedAt = ++this.clock;
    return { ok: true, evicted: [] };
  }

  unpin(key: string | null = this._focused): Change {
    const e = key ? this.entries.get(key) : undefined;
    if (!e) return { ok: false, error: "nothing to unpin", evicted: [] };
    if (!e.pinned) return { ok: true, evicted: [] };
    e.pinned = false;
    e.pinnedAt = 0;
    return { ok: true, evicted: this.evict() };
  }

  /** Removes a surface (user dismissal or the surface asked to close). */
  dismiss(key: string | null = this._focused): Change & { removed?: string } {
    const e = key ? this.entries.get(key) : undefined;
    if (!e) return { ok: false, error: "nothing to dismiss", evicted: [] };
    this.entries.delete(e.key);
    if (this._focused === e.key) {
      const next = [...this.entries.values()].sort((a, b) => b.lastFocusedAt - a.lastFocusedAt)[0];
      this._focused = next ? next.key : null;
      if (next) next.lastFocusedAt = ++this.clock;
    }
    return { ok: true, removed: e.key, evicted: this.evict() };
  }

  /**
   * Resolves a user reference: a tile number (`2`), or a case-insensitive
   * match on the title (exact, then prefix, then substring) or key.
   */
  resolve(ref: string): string | null {
    const r = ref.trim().toLowerCase();
    if (!r) return null;
    if (/^\d+$/.test(r)) {
      const tile = this.tiles()[Number(r) - 1];
      return tile ? tile.entry.key : null;
    }
    const all = this.all();
    const byKey = all.find((e) => e.key.toLowerCase() === r);
    if (byKey) return byKey.key;
    const exact = all.find((e) => e.title.toLowerCase() === r);
    if (exact) return exact.key;
    const prefix = all.find((e) => e.title.toLowerCase().startsWith(r));
    if (prefix) return prefix.key;
    const sub = all.find((e) => e.title.toLowerCase().includes(r));
    return sub ? sub.key : null;
  }

  private evict(): string[] {
    const recent = this.recent();
    const out: string[] = [];
    for (const e of recent.slice(this.maxRecent)) {
      this.entries.delete(e.key);
      out.push(e.key);
    }
    return out;
  }
}
