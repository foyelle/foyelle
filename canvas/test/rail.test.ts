import { describe, expect, it } from "vitest";
import { clampRail, RAIL_MIN, railMax, STAGE_MIN } from "../src/renderer/rail";

describe("conversation width", () => {
  it("keeps the rail readable and the cards visible", () => {
    expect(clampRail(100, 1600)).toBe(RAIL_MIN);
    expect(clampRail(700.4, 1600)).toBe(700);
    expect(clampRail(1500, 1200)).toBe(1200 - STAGE_MIN);
    expect(railMax(4000)).toBe(960); // never wider than this, however large the screen
  });

  it("never goes below the minimum, even in a narrow window", () => {
    expect(railMax(600)).toBe(RAIL_MIN);
    expect(clampRail(900, 600)).toBe(RAIL_MIN);
  });

  it("leaves the bots sidebar its share of the window", () => {
    expect(railMax(1600, 248)).toBe(1600 - 248 - STAGE_MIN);
    expect(clampRail(1200, 1600, 248)).toBe(1600 - 248 - STAGE_MIN);
  });
});
