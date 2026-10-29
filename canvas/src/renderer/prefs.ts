/** The user's own settings that shape the canvas: the look, text size, their name, first start. */
import { parseTheme, type ThemeDef } from "./themes";

export type Theme = "system" | "light" | "dark" | "time";
export type Scheme = "light" | "dark";

export interface CanvasPrefs {
  /** Light or dark (or by the system, or by the time of day). */
  theme: Theme;
  /** The theme in use (colours, fonts, shape), or null for the stylesheet's own (Ember). */
  style: ThemeDef | null;
  textScale: number;
  /** What Foyelle calls the user, or null when it has not asked yet. */
  name: string | null;
  /** The first-start welcome is done; null while the settings are not known yet. */
  welcomed: boolean | null;
}

export const DEFAULT_PREFS: CanvasPrefs = { theme: "system", style: null, textScale: 1, name: null, welcomed: null };

const THEMES: readonly Theme[] = ["system", "light", "dark", "time"];

/** Day hours for the "time" look: light from 07:00, evening colours from 19:00. */
export const DAY_START = 7;
export const DAY_END = 19;

/** The canvas's part of a `shell.settings_get` result (its `effective` settings and its theme). */
export function prefsFromSettings(result: unknown): CanvasPrefs {
  const r = (result && typeof result === "object" ? result : {}) as Record<string, any>;
  const eff = (r.effective && typeof r.effective === "object" ? r.effective : {}) as Record<string, any>;
  const ui = (eff.ui && typeof eff.ui === "object" ? eff.ui : {}) as Record<string, unknown>;
  const user = (eff.user && typeof eff.user === "object" ? eff.user : {}) as Record<string, unknown>;
  const theme = THEMES.includes(ui.theme as Theme) ? (ui.theme as Theme) : "system";
  const scale = typeof ui.text_scale === "number" && ui.text_scale >= 0.75 && ui.text_scale <= 2 ? ui.text_scale : 1;
  const name = typeof user.name === "string" && user.name.trim() ? user.name.trim() : null;
  return { theme, style: parseTheme(r.theme), textScale: scale, name, welcomed: ui.welcomed === true };
}

export function schemeFor(theme: Theme, now: Date, systemDark: boolean): Scheme {
  switch (theme) {
    case "light":
      return "light";
    case "dark":
      return "dark";
    case "time": {
      const h = now.getHours();
      return h >= DAY_START && h < DAY_END ? "light" : "dark";
    }
    default:
      return systemDark ? "dark" : "light";
  }
}

/** Sets the colour scheme on the page; the stylesheet keys its colours off `data-scheme`. */
export function applyScheme(scheme: Scheme, root: HTMLElement = document.documentElement): boolean {
  if (root.dataset.scheme === scheme) return false;
  root.dataset.scheme = scheme;
  root.style.colorScheme = scheme;
  return true;
}
