import { describe, expect, it } from "vitest";
import { applyTheme, canvasVariables, cardStyles, parseTheme, themeScheme, type ThemeDef } from "../src/renderer/themes";
import { buildHostContext } from "../src/renderer/theme";
import { prefsFromSettings } from "../src/renderer/prefs";

const colors = (bg: string) => ({
  background: bg, sidebar: "#f6eddd", surface: "#ffffff", surface_alt: "#f8efe1", text: "#2a1e16",
  text_muted: "#65544a", text_faint: "#8f7c6c", line: "#eadcc8", edge: "#2a1e16", accent: "#b23a0a",
  primary: "#ff8a3d", on_primary: "#2a1e16", highlight: "#ffd23f", on_highlight: "#2a1e16", alert: "#d6331f", on_alert: "#ffffff",
});
const theme = (over: Record<string, unknown> = {}) => ({
  id: "ember", name: "Ember", description: "",
  fonts: { body: "rounded", display: "hand" },
  shape: { radius: 22, edge_width: 2, lift: 3, drawn: true, grain: true, tilt: true },
  light: colors("#fbf6ec"), dark: { ...colors("#1d1712"), text: "#f6ecdf" },
  ...over,
});

describe("themes", () => {
  it("accepts a well-formed theme and keeps only what it knows", () => {
    const t = parseTheme({ ...theme(), extra: "ignored" })!;
    expect(t.id).toBe("ember");
    expect(t.shape).toEqual({ radius: 22, edge_width: 2, lift: 3, drawn: true, grain: true, tilt: true, bevel: false, gloss: false });
    expect("extra" in t).toBe(false);
  });

  it("refuses anything that could slip into CSS or is out of range", () => {
    expect(parseTheme(theme({ light: { ...colors("#fbf6ec"), primary: "red;}body{display:none" } }))).toBeNull();
    expect(parseTheme(theme({ light: { ...colors("#fbf6ec"), primary: "url(x)" } }))).toBeNull();
    expect(parseTheme(theme({ fonts: { body: "Comic Sans", display: "hand" } }))).toBeNull();
    expect(parseTheme(theme({ fonts: { body: "hand", display: "hand" } }))).toBeNull(); // hand is for titles only
    expect(parseTheme(theme({ shape: { radius: 99, edge_width: 2, lift: 3 } }))).toBeNull();
    expect(parseTheme(theme({ light: undefined, dark: undefined }))).toBeNull();
    expect(parseTheme("ember")).toBeNull();
  });

  it("uses the scheme a one-scheme theme has, whatever the user's light/dark setting", () => {
    const night = parseTheme(theme({ light: undefined }))!;
    expect(themeScheme(night, "light")).toBe("dark");
    expect(themeScheme(night, "dark")).toBe("dark");
    expect(themeScheme(parseTheme(theme())!, "light")).toBe("light");
    expect(themeScheme(null, "light")).toBe("light");
  });

  it("turns a theme into the stylesheet's variables", () => {
    const t = parseTheme(theme())!;
    const v = canvasVariables(t, "light");
    expect(v["--bg"]).toBe("#fbf6ec");
    expect(v["--on-flame"]).toBe("#2a1e16");
    expect(v["--font"]).toContain("Nunito");
    expect(v["--hand"]).toContain("Gochi Hand");
    expect(v["--lift"]).toBe("0 3px 0 #2a1e16");
    expect(v["--pill"]).toBe("999px");
    expect(v["--tilt"]).toBe("1deg");
    expect("--grain" in v).toBe(false); // grain on: the stylesheet's own
    expect(canvasVariables(t, "dark")["--fg"]).toBe("#f6ecdf");
    const flat = parseTheme(theme({ shape: { radius: 6, edge_width: 3, lift: 0, drawn: false, grain: false, tilt: false } }))!;
    const f = canvasVariables(flat, "light");
    expect(f["--lift"]).toBe("none");
    expect(f["--pill"]).toBe("6px");
    expect(f["--grain"]).toBe("none");
    expect(f["--tilt"]).toBe("0deg");
  });

  it("knows JetBrains Mono, the font Tokyo Night uses for everything", () => {
    const t = parseTheme(theme({ fonts: { body: "code", display: "code" }, light: undefined }))!;
    const v = canvasVariables(t, "light");
    expect(v["--font"]).toContain("JetBrains Mono");
    expect(v["--hand"]).toBe(v["--font"]);
    expect(v["--bg"]).toBe("#1d1712"); // its only scheme, whatever is asked for
  });

  it("knows DejaVu Sans Condensed, Millennium's stand-in for an early-2000s desktop font", () => {
    const t = parseTheme(theme({ fonts: { body: "condensed", display: "condensed" }, dark: undefined }))!;
    const v = canvasVariables(t, "dark");
    expect(v["--font"]).toContain("DejaVu Sans Condensed");
    expect(v["--hand-weight"]).toBe("700");
    expect(v["--bg"]).toBe("#fbf6ec"); // a light-only theme stays light in dark mode
    expect(themeScheme(t, "dark")).toBe("light");
  });

  it("shades a bevelled theme's buttons, in the canvas and in cards, and leaves the others alone", () => {
    const flat = parseTheme(theme())!;
    expect("--shade" in canvasVariables(flat, "light")).toBe(false);
    expect("--foyelle-bevel" in cardStyles(flat, "light").foyelle).toBe(false);
    const root = { dataset: {}, style: { setProperty() {}, removeProperty() {} } } as unknown as HTMLElement;
    applyTheme(flat, "light", root);
    expect(root.dataset.bevel).toBe("false");

    const t = parseTheme(theme({ shape: { radius: 4, edge_width: 1, lift: 0, bevel: true } }))!;
    expect(t.shape.bevel).toBe(true);
    const v = canvasVariables(t, "light");
    expect(v["--shade"]).toMatch(/^linear-gradient\(to bottom, rgb\(255 255 255/);
    expect(v["--shade-pressed"]).toContain("linear-gradient");
    expect(v["--glow"]).toBe("inset 0 0 0 2px color-mix(in srgb, #ffd23f 75%, #fff)"); // from the highlight
    expect(cardStyles(t, "light").foyelle).toMatchObject({ "--foyelle-bevel": "on", "--foyelle-glow": v["--glow"] });
    applyTheme(t, "light", root);
    expect(root.dataset.bevel).toBe("true");
  });

  it("repaints the ember and puts its words in balloons for a glossy theme, and only then", () => {
    const root = { dataset: {}, style: { setProperty() {}, removeProperty() {} } } as unknown as HTMLElement;
    const flame = parseTheme(theme())!;
    expect(Object.keys(canvasVariables(flame, "light")).filter((k) => k.startsWith("--ember-") || k === "--balloon")).toEqual(["--ember-glow"]);
    applyTheme(flame, "light", root);
    expect(root.dataset.gloss).toBe("false");

    const t = parseTheme(theme({ shape: { radius: 4, edge_width: 1, lift: 0, gloss: true } }))!;
    const v = canvasVariables(t, "light");
    expect(v["--ember-lo"]).toBe("#ffd23f"); // the highlight
    expect(v["--ember-ink"]).toBe("#2a1e16"); // the edge
    expect(v["--ember-gloss"]).toBe("1");
    expect(v["--balloon"]).toBe("#ffffe1");
    expect(canvasVariables(t, "dark")["--balloon"]).toBe("color-mix(in srgb, #ffffe1 12%, #ffffff)");
    applyTheme(t, "light", root);
    expect(root.dataset.gloss).toBe("true");
  });

  it("gives cards the theme through the spec's keys and Foyelle's own extras", () => {
    const t = parseTheme(theme({ fonts: { body: "serif", display: "mono" } }))!;
    const ctx = buildHostContext({ dark: false, width: 800, height: 600, theme: t }) as Record<string, any>;
    expect(ctx.styles.variables["--font-sans"]).toContain("Noto Serif");
    expect(ctx.styles.variables["--color-background-primary"]).toBe("#ffffff");
    expect(ctx.styles.variables["--color-border-secondary"]).toBe("#2a1e16");
    expect(Object.keys(ctx.styles.variables).every((k: string) => !k.startsWith("--foyelle"))).toBe(true);
    expect(ctx.foyelle.styles["--foyelle-display"]).toContain("Noto Sans Mono");
    expect(cardStyles(t, "light").foyelle["--foyelle-highlight"]).toBe("#ffd23f");
    // Without a theme: the defaults, and no extras.
    expect((buildHostContext({ dark: true, width: 1, height: 1 }) as Record<string, any>).foyelle).toBeUndefined();
  });

  it("reads the theme in use from the settings", () => {
    expect(prefsFromSettings({ effective: { ui: {} }, theme: theme() }).style?.name).toBe("Ember");
    expect(prefsFromSettings({ effective: { ui: {} }, theme: { id: "x" } }).style).toBeNull();
  });
});
