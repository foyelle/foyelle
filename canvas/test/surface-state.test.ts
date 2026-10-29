import { describe, expect, it } from "vitest";
import { SurfaceState } from "../src/renderer/surface-state";

const titles = (s: SurfaceState) => s.tiles().map((t) => `${t.index}:${t.entry.title}${t.focused ? "*" : ""}${t.entry.pinned ? "^" : ""}`);

describe("SurfaceState", () => {
  it("focuses a newly opened surface", () => {
    const s = new SurfaceState();
    s.open("a", "Files");
    expect(s.focused).toBe("a");
    s.open("b", "Mail");
    expect(s.focused).toBe("b");
    expect(titles(s)).toEqual(["1:Files"]);
  });

  it("re-opening an existing key focuses it and updates the title", () => {
    const s = new SurfaceState();
    s.open("a", "Files");
    s.open("b", "Mail");
    s.open("a", "Files 2");
    expect(s.focused).toBe("a");
    expect(s.get("a")!.title).toBe("Files 2");
    expect(s.size).toBe(2);
  });

  it("keeps pinned tiles in pin order with stable numbers, focused one included", () => {
    const s = new SurfaceState();
    s.open("a", "A");
    s.pin();
    s.open("b", "B");
    s.pin();
    s.open("c", "C");
    expect(titles(s)).toEqual(["1:A^", "2:B^"]);
    s.focus("a");
    expect(titles(s)).toEqual(["1:A*^", "2:B^", "3:C"]);
  });

  it("enforces the pin limit", () => {
    const s = new SurfaceState({ maxPinned: 2 });
    s.open("a", "A");
    expect(s.pin().ok).toBe(true);
    s.open("b", "B");
    expect(s.pin().ok).toBe(true);
    s.open("c", "C");
    const r = s.pin();
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/at most 2/);
    expect(s.get("c")!.pinned).toBe(false);
  });

  it("pinning twice is a no-op", () => {
    const s = new SurfaceState();
    s.open("a", "A");
    s.pin();
    expect(s.pin()).toEqual({ ok: true, evicted: [] });
  });

  it("evicts the oldest recent surfaces beyond maxRecent, never pinned ones", () => {
    const s = new SurfaceState({ maxRecent: 2 });
    s.open("p", "Pinned");
    s.pin();
    s.open("a", "A");
    s.open("b", "B");
    s.open("c", "C");
    const r = s.open("d", "D"); // recent: c, b, a -> a goes
    expect(r.evicted).toEqual(["a"]);
    expect(s.has("a")).toBe(false);
    expect(s.has("p")).toBe(true);
    expect(titles(s)).toEqual(["1:Pinned^", "2:C", "3:B"]);
  });

  it("unpinning a background surface can evict the oldest recent one", () => {
    const s = new SurfaceState({ maxRecent: 1 });
    s.open("a", "A");
    s.pin();
    s.open("b", "B");
    s.open("c", "C"); // recent: b
    const r = s.unpin("a"); // recent: b, a (a focused long ago) -> a is oldest
    expect(r.evicted).toEqual(["a"]);
  });

  it("dismissing the focused surface focuses the most recently focused one left", () => {
    const s = new SurfaceState();
    s.open("a", "A");
    s.open("b", "B");
    s.open("c", "C");
    s.focus("a");
    s.focus("c");
    const r = s.dismiss();
    expect(r.removed).toBe("c");
    expect(s.focused).toBe("a");
    s.dismiss();
    expect(s.focused).toBe("b");
    s.dismiss();
    expect(s.focused).toBeNull();
    expect(s.dismiss().ok).toBe(false);
  });

  it("dismissing a background surface keeps focus", () => {
    const s = new SurfaceState();
    s.open("a", "A");
    s.open("b", "B");
    s.dismiss("a");
    expect(s.focused).toBe("b");
    expect(s.size).toBe(1);
  });

  it("errors on unknown keys", () => {
    const s = new SurfaceState();
    expect(s.focus("x").ok).toBe(false);
    expect(s.pin("x").ok).toBe(false);
    expect(s.unpin().ok).toBe(false);
    expect(s.dismiss("x").ok).toBe(false);
  });

  it("resolves references by tile number, key and title", () => {
    const s = new SurfaceState();
    s.open("k1", "System status");
    s.pin();
    s.open("k2", "Files · Documents");
    s.open("k3", "Mail");
    expect(s.resolve("1")).toBe("k1");
    expect(s.resolve("2")).toBe("k2");
    expect(s.resolve("9")).toBeNull();
    expect(s.resolve("mail")).toBe("k3");
    expect(s.resolve("FILES")).toBe("k2");
    expect(s.resolve("docu")).toBe("k2");
    expect(s.resolve("k1")).toBe("k1");
    expect(s.resolve("status")).toBe("k1");
    expect(s.resolve("nothing")).toBeNull();
    expect(s.resolve("  ")).toBeNull();
  });
});
