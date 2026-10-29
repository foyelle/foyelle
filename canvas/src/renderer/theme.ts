/** Theme tokens shared with surfaces through the MCP Apps host context. */
import type { McpUiHostContext, McpUiStyles } from "@modelcontextprotocol/ext-apps/app-bridge";
import { cardStyles, type ThemeDef } from "./themes";

// Nunito ships with the canvas and is installed on the system, so cards (which can't load the
// canvas's files) find it by name.
const FONT_SANS = '"Nunito", "Avenir Next", "Noto Sans", "Noto Sans CJK SC", system-ui, sans-serif';
const FONT_MONO = 'ui-monospace, "SF Mono", "JetBrains Mono", "DejaVu Sans Mono", Menlo, Consolas, monospace';

export function styleVariables(dark: boolean): Partial<McpUiStyles> {
  const c = dark
    ? { bg1: "#2c231c", bg2: "#352a21", bg3: "#3d3027", fg1: "#f6ecdf", fg2: "#cbb9a4", fg3: "#95826f", line: "#44372c", info: "#ffa25f", infoBg: "#ff8a3d", danger: "#ff9c8a", success: "#9fd18e", warning: "#f0c96b" }
    : { bg1: "#ffffff", bg2: "#f8efe1", bg3: "#f1e6d5", fg1: "#2a1e16", fg2: "#6b5a4c", fg3: "#998776", line: "#eadcc8", info: "#c2410c", infoBg: "#ff8a3d", danger: "#b3261e", success: "#2f7a3a", warning: "#8a5a00" };
  return {
    "--color-background-primary": c.bg1,
    "--color-background-secondary": c.bg2,
    "--color-background-tertiary": c.bg3,
    "--color-background-info": c.infoBg,
    "--color-text-primary": c.fg1,
    "--color-text-secondary": c.fg2,
    "--color-text-tertiary": c.fg3,
    "--color-text-info": c.info,
    "--color-text-danger": c.danger,
    "--color-text-success": c.success,
    "--color-text-warning": c.warning,
    "--color-border-primary": c.line,
    "--color-border-secondary": c.line,
    "--font-sans": FONT_SANS,
    "--font-mono": FONT_MONO,
    "--font-text-md-size": "16px",
    "--border-radius-md": "14px",
  };
}

/**
 * The host context cards get. A theme's colours and fonts go in `styles` (MCP Apps' own keys);
 * Foyelle's extras (its display font, highlight and alert colours) go in `foyelle.styles`, a field
 * the spec lets hosts add and only Foyelle's card runtimes read.
 */
export function buildHostContext(opts: { dark: boolean; width: number; height: number; theme?: ThemeDef | null }): McpUiHostContext {
  const themed = opts.theme ? cardStyles(opts.theme, opts.dark ? "dark" : "light") : null;
  let timeZone: string | undefined;
  try {
    timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    timeZone = undefined;
  }
  return {
    theme: opts.dark ? "dark" : "light",
    displayMode: "inline",
    availableDisplayModes: ["inline"],
    platform: "desktop",
    locale: typeof navigator !== "undefined" ? navigator.language : "en",
    timeZone,
    deviceCapabilities: { touch: false, hover: true },
    containerDimensions: { width: Math.round(opts.width), maxHeight: Math.round(opts.height) },
    styles: { variables: { ...styleVariables(opts.dark), ...(themed?.variables ?? {}) } as McpUiStyles },
    ...(themed ? { foyelle: { styles: themed.foyelle } } : {}),
  } as McpUiHostContext;
}
