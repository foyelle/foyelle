/**
 * The card gallery's previews: a small drawing of each kind of card (its layout, not its data).
 *
 * Drawn rather than screenshotted: cards show the person's own mail, files and memory, so a
 * screenshot taken at build time would show sample data as if it were theirs, and one taken on the
 * machine would have to open every card (running its tool, through the policy and the audit log)
 * just to browse. The set of cards also changes after the build (a service connected, a card
 * Foyelle made), and the look changes with the theme. These drawings take the theme's colours
 * from the stylesheet (view.css, `.art`), work for any card, and stay legible at tile size.
 *
 * Every drawing is a fixed string of SVG made here: nothing from a helper goes into it.
 */
import type { CardArt } from "../card-catalog";

const W = 160;
const H = 100;

/** A rounded bar: a line of text, a field, a button. */
const bar = (x: number, y: number, w: number, h = 6, cls = "l") => `<rect class="${cls}" x="${x}" y="${y}" width="${w}" height="${h}" rx="${h / 2}"/>`;
const box = (x: number, y: number, w: number, h: number, r = 4, cls = "s") => `<rect class="${cls}" x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}"/>`;
const dot = (x: number, y: number, r: number, cls = "l") => `<circle class="${cls}" cx="${x}" cy="${y}" r="${r}"/>`;
const ink = (d: string, extra = "") => `<path class="i" d="${d}"${extra}/>`;

/** Rows of a list: a mark, then a long and a short line. */
function rows(n: number, top: number, mark: (i: number, y: number) => string, { gap = 17, x = 40, width = 70 } = {}): string {
  let out = "";
  for (let i = 0; i < n; i++) {
    const y = top + i * gap;
    out += mark(i, y) + bar(x, y - 6, width - (i % 2) * 18, 5, i === 0 ? "f" : "l") + bar(x, y + 2, width * 0.6 + (i % 3) * 8, 4, "l m");
  }
  return out;
}

const DRAWINGS: Record<CardArt, () => string> = {
  mail: () => rows(4, 26, (i, y) => dot(26, y, 6, i === 0 ? "a" : "s") + (i === 0 ? dot(132, y, 3, "a") : "")),
  compose: () =>
    bar(18, 20, 18, 5, "l m") + bar(40, 20, 60) + bar(18, 32, 18, 5, "l m") + bar(40, 32, 84) +
    ink("M18 44 H142") + bar(18, 52, 110, 4) + bar(18, 60, 120, 4) + bar(18, 68, 76, 4) + box(104, 76, 38, 13, 6.5, "a"),
  calendar: () => {
    let out = bar(18, 18, 54, 6, "f");
    for (let r = 0; r < 3; r++) for (let c = 0; c < 7; c++) {
      const today = r === 1 && c === 2;
      out += box(18 + c * 18, 32 + r * 18, 14, 14, 3, today ? "a" : "s");
    }
    return out + box(72, 50, 32, 6, 3, "h");
  },
  files: () => {
    let out = "";
    for (let i = 0; i < 6; i++) {
      const x = 22 + (i % 3) * 42, y = 20 + Math.floor(i / 3) * 38;
      out += i % 3 === 2
        ? box(x + 6, y, 22, 28, 3, "p") + ink(`M${x + 6} ${y + 3} a3 3 0 0 1 3 -3 h12 l7 7 v18 a3 3 0 0 1 -3 3 h-16 a3 3 0 0 1 -3 -3 z`) + bar(x + 10, y + 13, 14, 3) + bar(x + 10, y + 19, 10, 3)
        : `<path class="${i === 0 ? "h" : "s"}" d="M${x} ${y + 4} a3 3 0 0 1 3 -3 h10 l4 4 h14 a3 3 0 0 1 3 3 v18 a3 3 0 0 1 -3 3 h-28 a3 3 0 0 1 -3 -3 z"/>` +
          ink(`M${x} ${y + 4} a3 3 0 0 1 3 -3 h10 l4 4 h14 a3 3 0 0 1 3 3 v18 a3 3 0 0 1 -3 3 h-28 a3 3 0 0 1 -3 -3 z`);
      out += bar(x + 2, y + 32, 30, 3, "l m");
    }
    return out;
  },
  status: () =>
    `<path class="s" d="M24 70 a28 28 0 0 1 56 0 z"/>` + ink("M24 70 a28 28 0 0 1 56 0") +
    `<path class="a" d="M24 70 a28 28 0 0 1 44 -22 l-16 22 z"/>` + ink("M52 70 L66 50") + dot(52, 70, 3, "f") +
    rows(3, 34, (i, y) => dot(98, y - 2, 4, i === 2 ? "h" : "a"), { gap: 18, x: 108, width: 36 }),
  memory: () =>
    `<g transform="rotate(-4 50 50)">${box(18, 22, 54, 58, 4, "h")}${bar(26, 32, 36, 4, "f")}${bar(26, 42, 30, 4)}${bar(26, 50, 38, 4)}</g>` +
    `<g transform="rotate(3 110 50)">${box(84, 18, 58, 64, 4, "p")}${ink("M88 18 h50 a4 4 0 0 1 4 4 v56 a4 4 0 0 1 -4 4 h-50 a4 4 0 0 1 -4 -4 v-56 a4 4 0 0 1 4 -4 z")}${bar(92, 30, 40, 4, "f")}${bar(92, 40, 34, 4)}${bar(92, 48, 42, 4)}${bar(92, 56, 28, 4)}</g>`,
  settings: () => {
    let out = "";
    for (let i = 0; i < 4; i++) {
      const y = 24 + i * 17, on = i === 0 || i === 2;
      out += bar(18, y - 3, 60 - (i % 2) * 16, 5) + box(112, y - 6, 26, 12, 6, on ? "a" : "s") + ink(`M118 ${y - 6} h14 a6 6 0 0 1 0 12 h-14 a6 6 0 0 1 0 -12 z`) + dot(on ? 132 : 118, y, 4, "p") + ink(`M${on ? 136 : 122} ${y} a4 4 0 1 1 -8 0 a4 4 0 1 1 8 0`);
    }
    return out;
  },
  themes: () => {
    const swatch = (x: number, cls: string) => dot(x, 30, 10, cls) + ink(`M${x + 10} 30 a10 10 0 1 1 -20 0 a10 10 0 1 1 20 0`);
    return swatch(32, "a") + swatch(60, "h") + swatch(88, "s") + swatch(116, "f") +
      box(24, 50, 112, 34, 6, "s") + bar(32, 58, 50, 5, "f") + bar(32, 68, 70, 4) + box(104, 70, 24, 9, 4.5, "a");
  },
  audit: () => {
    let out = ink("M28 18 V86");
    for (let i = 0; i < 4; i++) {
      const y = 24 + i * 18;
      out += dot(28, y, 5, i === 1 ? "h" : "a") + bar(42, y - 5, 62 - (i % 2) * 14, 5) + bar(42, y + 2, 38 + (i % 3) * 10, 4, "l m");
    }
    return out;
  },
  privacy: () =>
    `<path class="s" d="M30 52 L62 26 L94 52 V84 H30 Z"/>` + ink("M24 56 L62 24 L100 56 M30 52 V84 H94 V52") +
    box(54, 62, 16, 22, 3, "h") + ink("M54 84 V62 h16 v22") +
    ink("M104 46 C 118 40, 126 34, 134 24", ' stroke-dasharray="4 4"') + ink("M126 24 h8 v8") + dot(134, 70, 4, "a") + bar(110, 78, 34, 4, "l m"),
  changes: () =>
    box(16, 18, 60, 66, 5, "s") + box(84, 18, 60, 66, 5, "s") +
    bar(22, 26, 40, 4) + bar(22, 36, 46, 5, "l m") + bar(22, 46, 32, 4) +
    bar(90, 26, 40, 4) + bar(90, 36, 46, 5, "a") + bar(90, 46, 36, 5, "a") + bar(90, 56, 30, 4) +
    box(90, 70, 36, 9, 4.5, "f"),
  web: () =>
    box(18, 16, 124, 14, 7, "p") + ink("M25 16 h110 a7 7 0 0 1 0 14 h-110 a7 7 0 0 1 0 -14 z") + dot(130, 23, 3.5, "a") + bar(26, 21, 50, 4) +
    rows(3, 46, (_i, y) => box(18, y - 8, 16, 14, 3, "h"), { gap: 18, x: 42, width: 90 }),
  flights: () =>
    dot(30, 64, 5, "a") + dot(130, 34, 5, "a") + ink("M30 64 C 60 20, 100 18, 130 34", ' stroke-dasharray="5 5"') +
    `<path class="f" d="M78 30 l12 -3 l-3 -9 l4 -1 l7 9 l9 -2 a3 3 0 0 1 1 5 l-9 3 l1 11 l-4 1 l-5 -9 l-11 3 z"/>` +
    box(18, 74, 58, 14, 7, "h") + bar(26, 79, 30, 4, "f") + box(84, 74, 58, 14, 7, "s") + bar(92, 79, 36, 4),
  tasks: () => {
    let out = "";
    for (let i = 0; i < 4; i++) {
      const y = 24 + i * 17, done = i === 0;
      out += box(18, y - 6, 12, 12, 3, done ? "a" : "p") + ink(`M21 ${y - 6} h6 a3 3 0 0 1 3 3 v6 a3 3 0 0 1 -3 3 h-6 a3 3 0 0 1 -3 -3 v-6 a3 3 0 0 1 3 -3 z`) +
        (done ? ink(`M21 ${y} l3 3 l5 -6`) : "") + bar(38, y - 3, 72 - (i % 2) * 20, 5, done ? "l m" : "l") + (i === 1 ? box(122, y - 4, 20, 8, 4, "h") : "");
    }
    return out;
  },
  accounts: () => {
    let out = "";
    for (let i = 0; i < 3; i++) {
      const y = 28 + i * 22, on = i < 2;
      out += box(18, y - 9, 124, 18, 6, "s") + dot(30, y, 4, on ? "a" : "l") + bar(40, y - 3, 50 - i * 6, 5, "f") + (on ? "" : box(110, y - 5, 26, 10, 5, "a"));
    }
    return out;
  },
  made: () =>
    box(18, 18, 124, 66, 6, "p") + ink("M24 18 h112 a6 6 0 0 1 6 6 v54 a6 6 0 0 1 -6 6 h-112 a6 6 0 0 1 -6 -6 v-54 a6 6 0 0 1 6 -6 z", ' stroke-dasharray="5 4"') +
    bar(28, 28, 56, 6, "f") + bar(28, 40, 84, 4) + bar(28, 48, 70, 4) + box(28, 60, 40, 16, 4, "s") + box(74, 60, 40, 16, 4, "s") +
    `<path class="h" d="M128 26 l3 7 l7 3 l-7 3 l-3 7 l-3 -7 l-7 -3 l7 -3 z"/>`,
  card: () => bar(18, 20, 64, 7, "f") + bar(18, 34, 110, 4) + bar(18, 42, 90, 4) + box(18, 54, 58, 30, 5, "s") + box(84, 54, 58, 30, 5, "s"),
};

/** The drawing for a kind of card, as an SVG string (fixed markup; no data from the card). */
export function cardArt(kind: CardArt): string {
  const draw = DRAWINGS[kind] ?? DRAWINGS.card;
  return `<svg xmlns="http://www.w3.org/2000/svg" class="art" viewBox="0 0 ${W} ${H}" aria-hidden="true" focusable="false">` +
    `<rect class="frame" x="4" y="4" width="${W - 8}" height="${H - 8}" rx="12"/>${draw()}</svg>`;
}

export const ART_KINDS = Object.keys(DRAWINGS) as CardArt[];
