/**
 * The ember's drawing: a hand-drawn flame with a face. Every mood has its own face (and, large,
 * its own little extras: sound waves while listening, a question while thinking, a "!" when it
 * needs you, stars when a task is done, "z z" asleep); the stylesheet shows the one for the mood
 * in `data-ember` on <html> (see ember.ts), so nothing here changes after the drawing is made.
 *
 * Large embers get the full drawing (a wobbly ink line, a light inside, cheeks). Small ones get a
 * cleaner flame with a thicker line and only eyes, because the details blur below ~40 px.
 *
 * Its colours are the stylesheet's (--ember-hi/-mid/-lo, --ember-ink, --ember-inner, --ember-gloss),
 * with the flame's own as fallbacks, so a theme can repaint it (Millennium's glossy blue one).
 */

export type EmberSize = "xs" | "sm" | "md" | "xl";

const FLAME = "M50 6 C 60 26, 90 44, 88 78 C 86 104, 68 116, 50 116 C 30 116, 12 102, 12 80 C 12 60, 26 52, 32 34 C 38 46, 44 48, 50 6 Z";
const INNER = "M50 52 C 56 64, 68 72, 66 88 C 64 100, 56 104, 50 104 C 42 104, 34 98, 34 88 C 34 78, 44 72, 50 52 Z";
const INK = "#2a1e16";

const paint = (prop: string, name: string, fallback: string) => `style="${prop}: var(--ember-${name}, ${fallback})"`;
/** A white sheen down the flame's left side and a glint near its tip, shown when --ember-gloss is set. */
const GLOSS = `<g class="gloss" style="opacity: var(--ember-gloss, 0)" fill="#fff"><ellipse cx="24" cy="84" rx="4" ry="13" transform="rotate(14 24 84)" opacity=".7"/><ellipse cx="55" cy="32" rx="2.6" ry="7" transform="rotate(-24 55 32)" opacity=".85"/></g>`;

const arcUp = (x: number, y: number, w: number) => `<path d="M${x - 5} ${y + 1} q5 -7 10 0" stroke="${INK}" stroke-width="${w}" fill="none" stroke-linecap="round"/>`;
const arcDown = (x: number, y: number, w: number) => `<path d="M${x - 5} ${y} q5 4 10 0" stroke="${INK}" stroke-width="${w}" fill="none" stroke-linecap="round"/>`;
const oval = (x: number, y: number, rx: number, ry: number) => `<ellipse class="eye" cx="${x}" cy="${y}" rx="${rx}" ry="${ry}" fill="${INK}"/>`;

/** Faces for the full drawing (ember mood -> eyes and mouth). */
function largeFaces(): Record<string, string> {
  const L = 40, R = 60, y = 74;
  const smile = `<path d="M43 86 q7 7 14 0" stroke="${INK}" stroke-width="3" fill="none" stroke-linecap="round"/>`;
  return {
    idle: oval(L, y, 4.2, 6) + oval(R, y, 4.2, 6) + smile,
    listening: `<circle cx="${L}" cy="73" r="5.5" fill="${INK}"/><circle cx="${R}" cy="73" r="5.5" fill="${INK}"/><circle cx="41.5" cy="71.5" r="1.8" fill="#fff"/><circle cx="61.5" cy="71.5" r="1.8" fill="#fff"/>`
      + `<ellipse cx="50" cy="88" rx="4" ry="4.5" fill="${INK}"/>`,
    thinking: oval(43, 70, 3.6, 5) + oval(62, 70, 3.6, 5) + `<path d="M45 89 h10" stroke="${INK}" stroke-width="3" stroke-linecap="round"/>`,
    speaking: oval(L, y, 4.2, 6) + oval(R, y, 4.2, 6) + `<ellipse class="talk" cx="50" cy="88" rx="5" ry="4" fill="${INK}"/>`,
    waiting: oval(L, y, 4.4, 6.4) + oval(R, y, 4.4, 6.4) + `<path d="M44 87 q6 4 12 0" stroke="${INK}" stroke-width="3" fill="none" stroke-linecap="round"/>`,
    settling: arcUp(L, y, 3) + arcUp(R, y, 3) + `<path d="M41 84 q9 11 18 0 z" fill="${INK}"/>`,
    asleep: arcDown(L, 75, 3) + arcDown(R, 75, 3) + `<path d="M46 89 q4 2 8 0" stroke="${INK}" stroke-width="2.5" fill="none" stroke-linecap="round"/>`,
  };
}

/** Faces for the small drawing: eyes only, drawn bolder. */
function smallFaces(): Record<string, string> {
  const L = 38, R = 62, y = 78;
  return {
    idle: oval(L, y, 6, 8.5) + oval(R, y, 6, 8.5),
    listening: oval(L, y - 1, 7, 9.5) + oval(R, y - 1, 7, 9.5),
    thinking: oval(L + 3, y - 5, 5.5, 8) + oval(R + 3, y - 5, 5.5, 8),
    speaking: oval(L, y, 6, 8.5) + oval(R, y, 6, 8.5),
    waiting: oval(L, y, 6.5, 9) + oval(R, y, 6.5, 9),
    settling: arcUp(L, y, 6) + arcUp(R, y, 6),
    asleep: arcDown(L, y, 6) + arcDown(R, y, 6),
  };
}

/** What floats around the large ember in some moods. */
const EXTRAS: Record<string, string> = {
  listening: `<g stroke="#ff8a3d" stroke-width="3" fill="none" stroke-linecap="round"><path class="pulse" d="M2 60 q-5 12 0 24"/><path class="pulse" d="M98 60 q5 12 0 24"/><path class="pulse late" d="M-5 54 q-7 18 0 36"/><path class="pulse late" d="M105 54 q7 18 0 36"/></g>`,
  thinking: `<g transform="translate(84 16)"><circle r="9" fill="#fff" stroke="${INK}" stroke-width="2.5"/><path d="M-4 -1 a4 4 0 1 1 4 4 v2" stroke="${INK}" stroke-width="2.5" fill="none" stroke-linecap="round"/><circle cx="0" cy="8.5" r="1.5" fill="${INK}"/></g><circle cx="76" cy="32" r="3" fill="#fff" stroke="${INK}" stroke-width="2"/>`,
  waiting: `<g class="pulse" transform="translate(88 30)"><circle r="9" fill="#ff4d3a"/><path d="M0 -4.5 v5" stroke="#fff" stroke-width="3" stroke-linecap="round"/><circle cx="0" cy="4.5" r="1.6" fill="#fff"/></g>`,
  settling: `<g fill="#ffd23f" stroke="${INK}" stroke-width="1.5"><path d="M10 28 l3 6 6 1 -4.5 4 1 6 -5.5 -3 -5.5 3 1 -6 -4.5 -4 6 -1z"/><path d="M90 12 l2 4 4.5 .7 -3.3 3 .8 4.4 -4 -2.2 -4 2.2 .8 -4.4 -3.3 -3 4.5 -.7z"/></g>`,
  asleep: `<g fill="#7a6a9a" font-family="Noteworthy, 'Bradley Hand', cursive" font-weight="700"><text class="zz" x="76" y="30" font-size="13">z</text><text class="zz late" x="85" y="18" font-size="17">z</text></g>`,
};

let counter = 0;

/** The ember's SVG markup at a size (a fresh id for its gradient and filter each time). */
export function emberMarkup(size: EmberSize): string {
  const id = `ember-${++counter}`;
  const large = size === "md" || size === "xl";
  const faces = large ? largeFaces() : smallFaces();
  const face = Object.entries(faces).map(([mood, art]) => `<g class="face f-${mood}">${art}</g>`).join("");
  const extras = size === "xl" ? Object.entries(EXTRAS).map(([mood, art]) => `<g class="extra x-${mood}">${art}</g>`).join("") : "";
  const defs = `<defs><radialGradient id="${id}-fill" cx="50%" cy="72%" r="65%"><stop offset="0" ${paint("stop-color", "hi", "#fff1b8")}/><stop offset=".45" ${paint("stop-color", "mid", "#ffb347")}/><stop offset="1" ${paint("stop-color", "lo", "#ff6a2a")}/></radialGradient>`
    + (large ? `<filter id="${id}-rough"><feTurbulence type="fractalNoise" baseFrequency="0.05" numOctaves="2" seed="2"/><feDisplacementMap in="SourceGraphic" scale="3"/></filter>` : "")
    + `</defs>`;
  const body = large
    ? `<path${size === "xl" ? ` filter="url(#${id}-rough)"` : ""} d="${FLAME}" fill="url(#${id}-fill)" ${paint("stroke", "ink", INK)} stroke-width="3.2" stroke-linejoin="round"/>`
      + `<path d="${INNER}" ${paint("fill", "inner", "#fff6d6")} opacity=".55"/>` + GLOSS
      + `<g class="cheeks"><ellipse cx="31" cy="86" rx="5" ry="3" fill="#ff8a6b" opacity=".55"/><ellipse cx="69" cy="86" rx="5" ry="3" fill="#ff8a6b" opacity=".55"/></g>`
    : `<path d="${FLAME}" fill="url(#${id}-fill)" ${paint("stroke", "ink", INK)} stroke-width="8" stroke-linejoin="round"/>` + GLOSS;
  return `<svg xmlns="http://www.w3.org/2000/svg" class="ember-art" viewBox="-12 -8 124 132" aria-hidden="true" focusable="false">${defs}${extras}`
    + `<g class="body"><g class="flame">${body}${face}</g></g></svg>`;
}

/** A new ember element at a size, drawn. */
export function emberElement(size: EmberSize): HTMLElement {
  const el = document.createElement("span");
  el.className = `ember ember-${size}`;
  el.setAttribute("aria-hidden", "true");
  mountEmber(el);
  return el;
}

/** Fills an ember placeholder (`<span class="ember ember-xl">`) with its drawing. */
export function mountEmber(el: HTMLElement): void {
  const size = (["xs", "sm", "md", "xl"] as const).find((s) => el.classList.contains(`ember-${s}`)) ?? "md";
  const doc = new DOMParser().parseFromString(emberMarkup(size), "image/svg+xml");
  el.replaceChildren(document.importNode(doc.documentElement, true));
}
