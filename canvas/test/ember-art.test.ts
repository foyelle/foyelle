import { describe, expect, it } from "vitest";
import { emberMarkup } from "../src/renderer/ember-art";
import type { EmberMood } from "../src/renderer/ember";

const MOODS: EmberMood[] = ["asleep", "idle", "listening", "thinking", "speaking", "waiting", "settling"];

describe("the ember's drawing", () => {
  it("has a face for every mood, at every size", () => {
    for (const size of ["xs", "sm", "md", "xl"] as const) {
      const svg = emberMarkup(size);
      for (const mood of MOODS) expect(svg, `${size} ${mood}`).toContain(`class="face f-${mood}"`);
    }
  });

  it("keeps the details for large embers and leaves them out of small ones", () => {
    const xl = emberMarkup("xl"), xs = emberMarkup("xs");
    expect(xl).toContain('class="extra x-listening"');
    expect(xl).toContain("feDisplacementMap"); // the hand-drawn line
    expect(xl).toContain('class="cheeks"');
    expect(xs).not.toContain("extra");
    expect(xs).not.toContain("feDisplacementMap");
    expect(xs).toContain('stroke-width="8"'); // a bolder line that survives 16 px
  });

  it("is SVG, so the browser draws it once parsed (without the namespace it parses as plain XML)", () => {
    expect(emberMarkup("md")).toMatch(/^<svg xmlns="http:\/\/www\.w3\.org\/2000\/svg"/);
  });

  it("takes its colours from the stylesheet when a theme sets them, the flame's own otherwise", () => {
    for (const size of ["xs", "xl"] as const) {
      const svg = emberMarkup(size);
      expect(svg).toContain('style="stop-color: var(--ember-lo, #ff6a2a)"');
      expect(svg).toContain('style="stroke: var(--ember-ink, #2a1e16)"');
      expect(svg).toContain('class="gloss" style="opacity: var(--ember-gloss, 0)"'); // hidden unless a theme asks
    }
  });

  it("gives each drawing its own gradient id, so several on a page don't collide", () => {
    const ids = [emberMarkup("sm"), emberMarkup("sm")].map((s) => /id="(ember-\d+)-fill"/.exec(s)![1]);
    expect(ids[0]).not.toBe(ids[1]);
  });
});
