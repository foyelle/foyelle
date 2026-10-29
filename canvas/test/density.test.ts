import { describe, expect, it } from "vitest";
import { densityFor, zoomFor } from "../src/main/density";

const screen = (width: number, height: number, scaleFactor = 1) => ({ size: { width, height }, scaleFactor });

describe("screen density", () => {
  it("doubles on a Retina-size screen the system calls 1×, like the VM in UTM's Retina mode", () => {
    expect(densityFor(screen(3456, 2168))).toBe(2);
    expect(densityFor(screen(3840, 2160))).toBe(2);
    expect(densityFor(screen(2160, 3840))).toBe(2); // turned
  });

  it("leaves normal screens and systems that scale by themselves alone", () => {
    expect(densityFor(screen(1728, 1084))).toBe(1);
    expect(densityFor(screen(2560, 1440))).toBe(1);
    expect(densityFor(screen(1728, 1117, 2))).toBe(1); // a Mac: Electron sees points, 2×
    expect(densityFor(null)).toBe(1);
  });

  it("multiplies the user's text size", () => {
    expect(zoomFor(1.25, screen(3456, 2168))).toBe(2.5);
    expect(zoomFor(1.25, screen(1920, 1080))).toBe(1.25);
  });
});
