/**
 * Canvas themes: the shell helper keeps them (twelve presets and the user's own, see
 * connectors/.../shell/themes.py) and hands the one in use to the canvas with its settings. This
 * file checks it again (it becomes CSS) and turns it into the stylesheet's variables, and into the
 * colours and fonts the cards get through the host context.
 *
 * With no theme (settings not loaded yet, or one that doesn't parse) the stylesheet's own values
 * show: they are Ember, the default.
 */
import type { Scheme } from "./prefs";

export type FontKey = "rounded" | "sans" | "serif" | "mono" | "hand" | "code" | "condensed";

export const COLOR_KEYS = [
  "background", "sidebar", "surface", "surface_alt", "text", "text_muted", "text_faint", "line",
  "edge", "accent", "primary", "on_primary", "highlight", "on_highlight", "alert", "on_alert",
] as const;
export type ThemeColors = Record<(typeof COLOR_KEYS)[number], string>;

export interface ThemeDef {
  id: string;
  name: string;
  fonts: { body: FontKey; display: FontKey };
  shape: {
    radius: number; edge_width: number; lift: number;
    drawn: boolean; grain: boolean; tilt: boolean; bevel: boolean; gloss: boolean;
  };
  light?: ThemeColors;
  dark?: ThemeColors;
}

/** The fonts a theme may name. Nunito, Gochi Hand, JetBrains Mono and DejaVu Sans Condensed ship with the canvas; the others are system
 * fonts on Foyelle (Inter, Noto), with the Mac's own as stand-ins in development. */
export const FONT_STACKS: Record<FontKey, string> = {
  rounded: '"Nunito", "Avenir Next", "Noto Sans", "Noto Sans CJK SC", system-ui, sans-serif',
  sans: '"Inter", "Helvetica Neue", "Noto Sans", "Noto Sans CJK SC", system-ui, sans-serif',
  serif: '"Noto Serif", "Iowan Old Style", Georgia, "Noto Serif CJK SC", "Noto Sans CJK SC", serif',
  mono: '"Noto Sans Mono", "SF Mono", Menlo, "Noto Sans CJK SC", monospace',
  hand: '"Gochi Hand", "Noteworthy", "Bradley Hand", "Nunito", "Noto Sans CJK SC", cursive',
  code: '"JetBrains Mono", "SF Mono", Menlo, "Noto Sans Mono CJK SC", "Noto Sans CJK SC", monospace',
  condensed: '"DejaVu Sans Condensed", Tahoma, Verdana, "Noto Sans", "Noto Sans CJK SC", system-ui, sans-serif',
};
/** How each font is drawn as a title: Gochi Hand is light and small, the others heavier and smaller. */
const DISPLAY: Record<FontKey, { weight: string; scale: string }> = {
  hand: { weight: "400", scale: "1" },
  rounded: { weight: "800", scale: ".8" },
  sans: { weight: "750", scale: ".78" },
  serif: { weight: "600", scale: ".82" },
  mono: { weight: "600", scale: ".7" },
  code: { weight: "700", scale: ".7" },
  condensed: { weight: "700", scale: ".78" },
};
const FONT_KEYS = Object.keys(FONT_STACKS) as FontKey[];
const HEX = /^#[0-9a-f]{6}$/;

function int(v: unknown, lo: number, hi: number): number | null {
  return typeof v === "number" && Number.isInteger(v) && v >= lo && v <= hi ? v : null;
}

function colors(v: unknown): ThemeColors | undefined | null {
  if (v === undefined || v === null) return undefined;
  if (typeof v !== "object") return null;
  const out = {} as ThemeColors;
  for (const k of COLOR_KEYS) {
    const c = (v as Record<string, unknown>)[k];
    if (typeof c !== "string" || !HEX.test(c.toLowerCase())) return null;
    out[k] = c.toLowerCase();
  }
  return out;
}

/** A theme from the shell helper, or null when anything about it is off. */
export function parseTheme(v: unknown): ThemeDef | null {
  if (!v || typeof v !== "object") return null;
  const t = v as Record<string, any>;
  if (typeof t.id !== "string" || typeof t.name !== "string") return null;
  const body = t.fonts?.body, display = t.fonts?.display;
  if (!FONT_KEYS.includes(body) || body === "hand" || !FONT_KEYS.includes(display)) return null;
  const s = t.shape ?? {};
  const radius = int(s.radius, 0, 32), edge = int(s.edge_width, 0, 4), lift = int(s.lift, 0, 6);
  if (radius === null || edge === null || lift === null) return null;
  const light = colors(t.light), dark = colors(t.dark);
  if (light === null || dark === null || (!light && !dark)) return null;
  return {
    id: t.id, name: t.name, fonts: { body, display },
    shape: {
      radius, edge_width: edge, lift,
      drawn: s.drawn === true, grain: s.grain === true, tilt: s.tilt === true, bevel: s.bevel === true, gloss: s.gloss === true,
    },
    ...(light ? { light } : {}), ...(dark ? { dark } : {}),
  };
}

/** The scheme to show: the one wanted, unless the theme only has the other. */
export function themeScheme(theme: ThemeDef | null, wanted: Scheme): Scheme {
  if (!theme) return wanted;
  return theme[wanted] ? wanted : wanted === "dark" ? "light" : "dark";
}

const mix = (a: string, pct: number, b: string) => `color-mix(in srgb, ${a} ${pct}%, ${b})`;

function liftOf(c: ThemeColors, scheme: Scheme, px: number): string {
  if (px <= 0) return "none";
  const color = scheme === "dark" ? mix(c.edge, 40, c.background) : c.edge;
  return `0 ${px}px 0 ${color}`;
}

/** A bevelled theme's buttons: lit from the top, shaded at the bottom (laid over each button's own
 * colour, so it works on any of them), sunken when pressed, and a glow inside on hover. */
function bevelOf(c: ThemeColors): { shade: string; pressed: string; glow: string } {
  const dark = mix(c.text_faint, 30, "transparent");
  return {
    shade: `linear-gradient(to bottom, rgb(255 255 255 / .75), rgb(255 255 255 / .2) 45%, transparent 60%, ${dark})`,
    pressed: `linear-gradient(to bottom, ${dark}, transparent 45%, rgb(255 255 255 / .35))`,
    glow: `inset 0 0 0 2px ${mix(c.highlight, 75, "#fff")}`,
  };
}

/** A glossy theme's ember: an early-2000s desktop icon in the highlight colour, outlined in the edge
 * colour with a white sheen; and its words in a pale yellow balloon (darkened in a dark scheme). */
function glossOf(c: ThemeColors, scheme: Scheme): Record<string, string> {
  return {
    "--ember-hi": mix(c.highlight, 20, "#fff"),
    "--ember-mid": mix(c.highlight, 65, "#fff"),
    "--ember-lo": c.highlight,
    "--ember-ink": c.edge,
    "--ember-inner": "#ffffff",
    "--ember-gloss": "1",
    "--balloon": scheme === "dark" ? mix("#ffffe1", 12, c.surface) : "#ffffe1",
  };
}

/** The stylesheet's variables for a theme in a scheme (see app.css's :root). */
export function canvasVariables(theme: ThemeDef, scheme: Scheme): Record<string, string> {
  const c = theme[scheme] ?? theme.light ?? theme.dark!;
  const s = theme.shape;
  const display = DISPLAY[theme.fonts.display];
  return {
    "--bg": c.background,
    "--rail": c.sidebar,
    "--panel": c.surface,
    "--panel-2": c.surface_alt,
    "--fg": c.text,
    "--fg-2": c.text_muted,
    "--fg-3": c.text_faint,
    "--line": c.line,
    "--line-2": mix(c.line, 55, c.surface),
    "--dash": c.line,
    "--edge": c.edge,
    "--edge-w": `${s.edge_width}px`,
    "--lift": liftOf(c, scheme, s.lift),
    "--lift-sm": liftOf(c, scheme, Math.ceil((s.lift * 2) / 3)),
    "--card-lift": s.lift > 0 ? `0 ${s.lift + 2}px 0 ${mix(c.edge, 22, "transparent")}` : "none",
    "--accent": c.accent,
    "--accent-soft": mix(c.primary, scheme === "dark" ? 26 : 22, c.surface),
    "--flame": c.primary,
    "--flame-2": mix(c.primary, 84, scheme === "dark" ? "#000" : "#fff"),
    "--on-flame": c.on_primary,
    "--sun": c.highlight,
    "--on-sun": c.on_highlight,
    "--hot": c.alert,
    "--on-hot": c.on_alert,
    "--ember": c.primary,
    "--ember-glow": mix(c.primary, 30, c.surface),
    "--scrim": mix(c.background, 76, "transparent"),
    "--font": FONT_STACKS[theme.fonts.body],
    "--hand": FONT_STACKS[theme.fonts.display],
    "--hand-weight": display.weight,
    "--hand-scale": display.scale,
    "--radius": `${s.radius}px`,
    "--pill": s.radius < 10 ? `${Math.max(s.radius, 3)}px` : "999px",
    "--tilt": s.tilt ? "1deg" : "0deg",
    ...(s.grain ? {} : { "--grain": "none" }),
    ...(s.bevel ? (({ shade, pressed, glow }) => ({ "--shade": shade, "--shade-pressed": pressed, "--glow": glow }))(bevelOf(c)) : {}),
    ...(s.gloss ? glossOf(c, scheme) : {}),
  };
}

let applied: string[] = [];

/** Puts a theme on the page (or, with null, goes back to the stylesheet's own). True if it changed. */
export function applyTheme(theme: ThemeDef | null, scheme: Scheme, root: HTMLElement = document.documentElement): boolean {
  const vars = theme ? canvasVariables(theme, scheme) : {};
  const key = theme ? JSON.stringify([theme, scheme]) : "";
  if (root.dataset.themeKey === key) return false;
  for (const name of applied) root.style.removeProperty(name);
  for (const [name, value] of Object.entries(vars)) root.style.setProperty(name, value);
  applied = Object.keys(vars);
  root.dataset.themeKey = key;
  root.dataset.drawn = String(theme ? theme.shape.drawn : true);
  root.dataset.bevel = String(theme ? theme.shape.bevel : false);
  root.dataset.gloss = String(theme ? theme.shape.gloss : false);
  if (theme) root.dataset.themeId = theme.id;
  else delete root.dataset.themeId;
  return true;
}

/** What cards get: the MCP Apps style variables, plus Foyelle's own extras for its cards. */
export function cardStyles(theme: ThemeDef, scheme: Scheme): { variables: Record<string, string>; foyelle: Record<string, string> } {
  const c = theme[scheme] ?? theme.light ?? theme.dark!;
  const s = theme.shape;
  const display = DISPLAY[theme.fonts.display];
  return {
    variables: {
      "--color-background-primary": c.surface,
      "--color-background-secondary": c.surface_alt,
      "--color-background-tertiary": mix(c.surface_alt, 50, c.line),
      "--color-background-info": c.primary,
      "--color-text-primary": c.text,
      "--color-text-secondary": c.text_muted,
      "--color-text-tertiary": c.text_faint,
      "--color-text-info": c.accent,
      "--color-border-primary": c.line,
      "--color-border-secondary": c.edge,
      "--color-ring-primary": c.primary,
      "--font-sans": FONT_STACKS[theme.fonts.body],
      "--border-radius-md": `${Math.min(s.radius, 16)}px`,
      "--border-radius-full": s.radius < 10 ? `${Math.max(s.radius, 3)}px` : "999px",
      "--border-width-regular": `${s.edge_width}px`,
      "--shadow-sm": liftOf(c, scheme, Math.ceil((s.lift * 2) / 3)),
    },
    foyelle: {
      "--foyelle-display": FONT_STACKS[theme.fonts.display],
      "--foyelle-display-weight": display.weight,
      "--foyelle-display-scale": display.scale,
      "--foyelle-on-primary": c.on_primary,
      "--foyelle-highlight": c.highlight,
      "--foyelle-on-highlight": c.on_highlight,
      "--foyelle-alert": c.alert,
      "--foyelle-on-alert": c.on_alert,
      "--foyelle-tilt": s.tilt ? "1deg" : "0deg",
      ...(s.bevel ? (({ shade, pressed, glow }) => ({
        "--foyelle-bevel": "on", "--foyelle-shade": shade, "--foyelle-shade-pressed": pressed, "--foyelle-glow": glow,
      }))(bevelOf(c)) : {}),
    },
  };
}
