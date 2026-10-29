/**
 * Sharp text on a high-density screen the system doesn't scale. Foyelle's VM gets the host
 * window's real pixels (UTM's Retina mode) but reports a scale factor of 1, so the canvas would
 * draw at half size, or blurred if the host stretched it. On such a screen the canvas zooms by 2:
 * Chromium then draws text and drawings at the screen's own pixels. The user's text size
 * multiplies on top.
 */

/** The narrowest screen treated as high density when the system says 1×: wider than any common
 * normal-density monitor (2560 px), as wide as a 15" Retina panel (2880 px). */
export const HIGH_DENSITY_WIDTH = 2880;

export interface DisplayLike {
  size: { width: number; height: number };
  scaleFactor: number;
}

export function densityFor(d: DisplayLike | null | undefined): number {
  if (!d || d.scaleFactor > 1) return 1; // the system scales already (a Mac, a HiDPI desktop)
  const longest = Math.max(d.size.width, d.size.height);
  return longest >= HIGH_DENSITY_WIDTH ? 2 : 1;
}

/** The zoom for a text size on a display. Electron accepts 0.25–5. */
export function zoomFor(textScale: number, d: DisplayLike | null | undefined): number {
  return Math.min(5, Math.max(0.25, textScale * densityFor(d)));
}
