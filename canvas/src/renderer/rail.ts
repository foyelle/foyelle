/**
 * The conversation rail's width: drag its edge, or focus the edge and use the arrow keys (Home or
 * a double-click goes back to the default). The width is remembered on this computer and always
 * leaves the cards enough room.
 */

export const RAIL_MIN = 320;
/** The cards keep at least this much of the window. */
export const STAGE_MIN = 420;
const RAIL_MAX = 960;
const KEY = "foyelle.railWidth";

/** The widest the rail may be in a window this wide, beside ``reserved`` (the bots sidebar). */
export function railMax(windowWidth: number, reserved = 0): number {
  return Math.max(RAIL_MIN, Math.min(RAIL_MAX, windowWidth - reserved - STAGE_MIN));
}

export function clampRail(width: number, windowWidth: number, reserved = 0): number {
  return Math.round(Math.min(Math.max(width, RAIL_MIN), railMax(windowWidth, reserved)));
}

function stored(): number | null {
  try {
    const v = Number(localStorage.getItem(KEY));
    return Number.isFinite(v) && v > 0 ? v : null;
  } catch {
    return null;
  }
}

function store(width: number | null): void {
  try {
    if (width === null) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, String(width));
  } catch {
    // Not remembered; the rail still resizes.
  }
}

export function setupRailResize(handle: HTMLElement, root: HTMLElement = document.documentElement): void {
  /** The width the stylesheet gives the rail (it changes with the window). */
  const natural = () => {
    root.style.removeProperty("--rail-w");
    return handle.parentElement?.getBoundingClientRect().width ?? 400;
  };
  let chosen = stored();
  /** What sits left of the rail (the bots sidebar) takes its share of the window too. */
  const reserved = () => handle.parentElement?.previousElementSibling?.getBoundingClientRect().width ?? 0;

  const apply = () => {
    const width = chosen === null ? null : clampRail(chosen, window.innerWidth, reserved());
    if (width === null) root.style.removeProperty("--rail-w");
    else root.style.setProperty("--rail-w", `${width}px`);
    const now = width ?? natural();
    handle.setAttribute("aria-valuenow", String(Math.round(now)));
    handle.setAttribute("aria-valuemin", String(RAIL_MIN));
    handle.setAttribute("aria-valuemax", String(railMax(window.innerWidth, reserved())));
  };
  const choose = (width: number | null) => {
    chosen = width === null ? null : clampRail(width, window.innerWidth, reserved());
    apply();
  };

  handle.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    try {
      handle.setPointerCapture(e.pointerId); // keeps the drag when the pointer passes over a card
    } catch {
      // A pointer that is already gone cannot be captured; the drag then follows the handle only.
    }
    const left = handle.parentElement?.getBoundingClientRect().left ?? 0;
    document.body.classList.add("resizing-rail");
    const move = (ev: PointerEvent) => choose(ev.clientX - left);
    const up = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      handle.removeEventListener("pointercancel", up);
      document.body.classList.remove("resizing-rail");
      store(chosen);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
    handle.addEventListener("pointercancel", up);
  });
  handle.addEventListener("dblclick", () => {
    choose(null);
    store(null);
  });
  handle.addEventListener("keydown", (e) => {
    const now = chosen ?? natural();
    const step = e.shiftKey ? 80 : 24;
    if (e.key === "ArrowLeft") choose(now - step);
    else if (e.key === "ArrowRight") choose(now + step);
    else if (e.key === "Home") choose(null);
    else return;
    e.preventDefault();
    store(chosen);
  });
  window.addEventListener("resize", apply);
  apply();
}
