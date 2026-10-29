"""Canvas themes: twelve presets and the ones the user makes with the agent.

A theme is plain data. It has colours for a light and/or a dark scheme, a body and a display font
from a short list the canvas ships or installs, and a shape (corner radius, edge width, the hard
shadow under buttons, a hand-drawn wobble, paper grain, a playful tilt, bevelled buttons, a glossy
ember). A theme with only one
scheme always uses it; otherwise the user's light/dark setting (``ui.theme``) picks.

Every theme is validated the same way, presets included: colours are #rrggbb, and the pairs people
read have enough contrast (WCAG: 4.5:1 for text, 3:1 for faint hints), so a theme the agent makes
can't leave words unreadable. The canvas validates again before it applies anything.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..sdk.fsutil import atomic_write

FONTS = ("rounded", "sans", "serif", "mono", "hand", "code", "condensed")
BODY_FONTS = ("rounded", "sans", "serif", "mono", "code", "condensed")
FONT_NOTES = {
    "rounded": "Nunito, soft and friendly",
    "sans": "Inter, plain and crisp",
    "serif": "Noto Serif, bookish",
    "mono": "Noto Sans Mono, like a terminal",
    "hand": "Gochi Hand, handwritten (titles only)",
    "code": "JetBrains Mono, a programmer's monospace",
    "condensed": "DejaVu Sans Condensed, narrow and clear like an early-2000s desktop",
}
COLORS = (
    "background", "sidebar", "surface", "surface_alt", "text", "text_muted", "text_faint", "line",
    "edge", "accent", "primary", "on_primary", "highlight", "on_highlight", "alert", "on_alert",
)
COLOR_NOTES = {
    "background": "the page behind everything",
    "sidebar": "the conversation column",
    "surface": "cards, answers, buttons and inputs",
    "surface_alt": "quiet areas inside cards (tables' heads, code, fields)",
    "text": "main text",
    "text_muted": "secondary text",
    "text_faint": "hints and timestamps",
    "line": "thin dividers",
    "edge": "the outline of buttons and cards",
    "accent": "links and emphasised words",
    "primary": "the main button and the microphone",
    "on_primary": "text on the main button",
    "highlight": "selected things and counts",
    "on_highlight": "text on highlight",
    "alert": "warnings and approvals waiting",
    "on_alert": "text on alert",
}
SHAPE = {
    "radius": (0, 32, "corner radius of cards in px (buttons follow; under 10 makes them square-ish)"),
    "edge_width": (0, 4, "outline width of buttons and cards in px (0: no outline)"),
    "lift": (0, 6, "hard shadow under buttons and cards in px (0: flat)"),
}
FLAGS = {
    "drawn": "a hand-drawn wobble on the input's outline",
    "grain": "a light paper grain on the background",
    "tilt": "stickers and suggestion chips sit slightly askew",
    "bevel": "buttons shaded light to dark like an early-2000s desktop, glowing on hover",
    "gloss": "Foyelle's ember as a glossy desktop icon in the highlight colour, its words in pale yellow balloons",
}
DEFAULT_ID = "ember"
MAX_CUSTOM = 40

# (foreground, background, minimum ratio) pairs every scheme must meet.
CONTRAST = (
    ("text", "background", 4.5), ("text", "sidebar", 4.5), ("text", "surface", 4.5), ("text", "surface_alt", 4.5),
    ("text_muted", "surface", 4.5), ("text_muted", "background", 4.5), ("text_muted", "sidebar", 4.5),
    ("text_faint", "surface", 3.0), ("text_faint", "background", 3.0),
    ("accent", "surface", 4.5), ("accent", "background", 4.5),
    ("on_primary", "primary", 4.5), ("on_highlight", "highlight", 4.5), ("on_alert", "alert", 4.5),
)

_HEX = re.compile(r"^#[0-9a-f]{6}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def _scheme(**c: str) -> dict[str, str]:
    return dict(c)


PRESETS: list[dict[str, Any]] = [
    {
        "id": "ember", "name": "Ember",
        "description": "Warm paper, ink outlines and the drawn flame. Friendly and handmade.",
        "fonts": {"body": "rounded", "display": "hand"},
        "shape": {"radius": 22, "edge_width": 2, "lift": 3, "drawn": True, "grain": True, "tilt": True},
        "light": _scheme(background="#fbf6ec", sidebar="#f6eddd", surface="#ffffff", surface_alt="#f8efe1",
                         text="#2a1e16", text_muted="#65544a", text_faint="#8f7c6c", line="#eadcc8", edge="#2a1e16",
                         accent="#b23a0a", primary="#ff8a3d", on_primary="#2a1e16", highlight="#ffd23f",
                         on_highlight="#2a1e16", alert="#d6331f", on_alert="#ffffff"),
        "dark": _scheme(background="#1d1712", sidebar="#231c16", surface="#2c231c", surface_alt="#352a21",
                        text="#f6ecdf", text_muted="#cbb9a4", text_faint="#9a8774", line="#44372c", edge="#e8d6c0",
                        accent="#ffa25f", primary="#ff8a3d", on_primary="#2a1e16", highlight="#ffd23f",
                        on_highlight="#2a1e16", alert="#ff6b52", on_alert="#1d1712"),
    },
    {
        "id": "paper", "name": "Paper",
        "description": "Quiet linen and thin lines, with bookish titles. Calm and classic.",
        "fonts": {"body": "sans", "display": "serif"},
        "shape": {"radius": 14, "edge_width": 1, "lift": 0, "drawn": False, "grain": False, "tilt": False},
        "light": _scheme(background="#f6f0e6", sidebar="#fbf7f0", surface="#fffdf9", surface_alt="#f3eadc",
                         text="#2e2621", text_muted="#64554a", text_faint="#8c7d70", line="#e5d8c5", edge="#d3c2ab",
                         accent="#a3441a", primary="#b0501e", on_primary="#ffffff", highlight="#f4d3b8",
                         on_highlight="#2e2621", alert="#b3361e", on_alert="#ffffff"),
        "dark": _scheme(background="#1e1915", sidebar="#231d18", surface="#2a231d", surface_alt="#342a22",
                        text="#f1e8dc", text_muted="#c2b29f", text_faint="#978674", line="#45392f", edge="#5c4b3d",
                        accent="#f0955a", primary="#f0955a", on_primary="#1e1915", highlight="#5a3420",
                        on_highlight="#f1e8dc", alert="#ff9c8a", on_alert="#1e1915"),
    },
    {
        "id": "midnight", "name": "Midnight",
        "description": "Deep night blue with a warm amber lamp. Always dark.",
        "fonts": {"body": "sans", "display": "sans"},
        "shape": {"radius": 16, "edge_width": 1, "lift": 0, "drawn": False, "grain": False, "tilt": False},
        "dark": _scheme(background="#0f1420", sidebar="#131a29", surface="#1a2233", surface_alt="#222c40",
                        text="#e8edf7", text_muted="#a8b3c7", text_faint="#7c889e", line="#2a3550", edge="#3e4b6b",
                        accent="#ffc46b", primary="#ffb547", on_primary="#1a1206", highlight="#33456b",
                        on_highlight="#e8edf7", alert="#ff7a6b", on_alert="#1a0d0b"),
    },
    {
        "id": "meadow", "name": "Meadow",
        "description": "Fresh greens and sunshine, soft rounded letters.",
        "fonts": {"body": "rounded", "display": "rounded"},
        "shape": {"radius": 20, "edge_width": 2, "lift": 2, "drawn": False, "grain": True, "tilt": False},
        "light": _scheme(background="#f3f8ec", sidebar="#e9f2df", surface="#ffffff", surface_alt="#eef6e6",
                         text="#1f2a1a", text_muted="#4a5a43", text_faint="#73826a", line="#d6e4c8", edge="#1f2a1a",
                         accent="#2b6a1c", primary="#7cc35a", on_primary="#13240c", highlight="#ffe066",
                         on_highlight="#1f2a1a", alert="#c8402a", on_alert="#ffffff"),
        "dark": _scheme(background="#141c12", sidebar="#182216", surface="#1f2b1c", surface_alt="#263524",
                        text="#eaf3e1", text_muted="#b5c6a8", text_faint="#8a9c7f", line="#33452e", edge="#cfe3bf",
                        accent="#9fdc7c", primary="#7cc35a", on_primary="#13240c", highlight="#ffe066",
                        on_highlight="#1f2a1a", alert="#ff8a73", on_alert="#1a0b07"),
    },
    {
        "id": "ocean", "name": "Ocean",
        "description": "Cool sea blues and crisp letters. Clear and fresh.",
        "fonts": {"body": "sans", "display": "sans"},
        "shape": {"radius": 18, "edge_width": 1, "lift": 2, "drawn": False, "grain": False, "tilt": False},
        "light": _scheme(background="#eef5fa", sidebar="#e4eef6", surface="#ffffff", surface_alt="#edf4fa",
                         text="#0f2433", text_muted="#405a69", text_faint="#6a8293", line="#d2e2ee", edge="#9fbbd0",
                         accent="#0b5f8a", primary="#0f6fa8", on_primary="#ffffff", highlight="#bfe6ff",
                         on_highlight="#0f2433", alert="#c43a3a", on_alert="#ffffff"),
        "dark": _scheme(background="#0b1822", sidebar="#0e1d29", surface="#132634", surface_alt="#183042",
                        text="#e6f1f8", text_muted="#a3bccb", text_faint="#7590a2", line="#24394a", edge="#3b5a70",
                        accent="#6cc4f5", primary="#4fb3ea", on_primary="#06151f", highlight="#1f4a66",
                        on_highlight="#e6f1f8", alert="#ff8080", on_alert="#1a0505"),
    },
    {
        "id": "pop", "name": "Pop",
        "description": "Bold black outlines, hard shadows, hot pink and lime. Loud and fun.",
        "fonts": {"body": "sans", "display": "rounded"},
        "shape": {"radius": 8, "edge_width": 3, "lift": 5, "drawn": False, "grain": False, "tilt": True},
        "light": _scheme(background="#fff8f0", sidebar="#ffeede", surface="#ffffff", surface_alt="#fff1e2",
                         text="#111111", text_muted="#444444", text_faint="#707070", line="#e8dccd", edge="#111111",
                         accent="#b0124f", primary="#ff4fa3", on_primary="#111111", highlight="#c6f432",
                         on_highlight="#111111", alert="#ff3b30", on_alert="#111111"),
        "dark": _scheme(background="#141414", sidebar="#1b1b1b", surface="#222222", surface_alt="#2b2b2b",
                        text="#fafafa", text_muted="#c4c4c4", text_faint="#8f8f8f", line="#3a3a3a", edge="#fafafa",
                        accent="#ff7ab8", primary="#ff4fa3", on_primary="#111111", highlight="#c6f432",
                        on_highlight="#111111", alert="#ff5a4f", on_alert="#111111"),
    },
    {
        "id": "terminal", "name": "Terminal",
        "description": "Amber letters on black, like an old screen. Always dark.",
        "fonts": {"body": "mono", "display": "mono"},
        "shape": {"radius": 4, "edge_width": 1, "lift": 0, "drawn": False, "grain": False, "tilt": False},
        "dark": _scheme(background="#0b0a08", sidebar="#100e0b", surface="#14120e", surface_alt="#1c1913",
                        text="#f5d29a", text_muted="#c9a46a", text_faint="#8f7856", line="#2a251c", edge="#5c4b2e",
                        accent="#ffb347", primary="#ffb347", on_primary="#0b0a08", highlight="#3a2d14",
                        on_highlight="#f5d29a", alert="#ff6b4a", on_alert="#0b0a08"),
    },
    {
        "id": "blossom", "name": "Blossom",
        "description": "Soft pinks and plum with elegant titles. Gentle and warm.",
        "fonts": {"body": "rounded", "display": "serif"},
        "shape": {"radius": 20, "edge_width": 1, "lift": 2, "drawn": False, "grain": True, "tilt": False},
        "light": _scheme(background="#fdf3f5", sidebar="#f9e8ec", surface="#ffffff", surface_alt="#fbeef1",
                         text="#3a1f2b", text_muted="#6e4b58", text_faint="#97767f", line="#f0d6de", edge="#d9a9b9",
                         accent="#9c2b60", primary="#e2668f", on_primary="#2b1520", highlight="#ffd6e3",
                         on_highlight="#3a1f2b", alert="#c62f3c", on_alert="#ffffff"),
        "dark": _scheme(background="#1f1418", sidebar="#25181d", surface="#2e1e24", surface_alt="#38252d",
                        text="#fbe9ef", text_muted="#d6b3c0", text_faint="#a68792", line="#4a3039", edge="#7a5160",
                        accent="#ff9ec0", primary="#f080a8", on_primary="#2b1520", highlight="#5a2e40",
                        on_highlight="#fbe9ef", alert="#ff8f8f", on_alert="#2b0d0d"),
    },
    {
        "id": "contrast", "name": "High contrast",
        "description": "The strongest readable colours and thick outlines, for tired or weaker eyes.",
        "fonts": {"body": "sans", "display": "sans"},
        "shape": {"radius": 10, "edge_width": 3, "lift": 0, "drawn": False, "grain": False, "tilt": False},
        "light": _scheme(background="#ffffff", sidebar="#ffffff", surface="#ffffff", surface_alt="#f0f0f0",
                         text="#000000", text_muted="#262626", text_faint="#4d4d4d", line="#767676", edge="#000000",
                         accent="#0033cc", primary="#000000", on_primary="#ffffff", highlight="#ffeb00",
                         on_highlight="#000000", alert="#b00020", on_alert="#ffffff"),
        "dark": _scheme(background="#000000", sidebar="#000000", surface="#000000", surface_alt="#1a1a1a",
                        text="#ffffff", text_muted="#e6e6e6", text_faint="#bfbfbf", line="#8c8c8c", edge="#ffffff",
                        accent="#8ab4ff", primary="#ffeb00", on_primary="#000000", highlight="#ffeb00",
                        on_highlight="#000000", alert="#ff6b6b", on_alert="#000000"),
    },
    {
        "id": "dusk", "name": "Dusk",
        "description": "Lavender evening with a peach glow and graceful titles.",
        "fonts": {"body": "sans", "display": "serif"},
        "shape": {"radius": 18, "edge_width": 1, "lift": 1, "drawn": False, "grain": True, "tilt": False},
        "light": _scheme(background="#f5f2fb", sidebar="#ece7f7", surface="#ffffff", surface_alt="#f1edfa",
                         text="#241d38", text_muted="#544b72", text_faint="#7e769b", line="#e0d9f0", edge="#b9add6",
                         accent="#5f35b8", primary="#6c45cc", on_primary="#ffffff", highlight="#ffd9c2",
                         on_highlight="#241d38", alert="#c93644", on_alert="#ffffff"),
        "dark": _scheme(background="#16121f", sidebar="#1b1626", surface="#231c31", surface_alt="#2b2340",
                        text="#eee9fb", text_muted="#bdb3dc", text_faint="#8d83ae", line="#3a3054", edge="#5e4f8a",
                        accent="#c3a8ff", primary="#a98bff", on_primary="#16121f", highlight="#4a3570",
                        on_highlight="#eee9fb", alert="#ff8a95", on_alert="#1f0a0d"),
    },
    {
        # Omarchy's default look (https://github.com/basecamp/omarchy, MIT): the Tokyo Night colours
        # (enkia's Tokyo Night theme, https://github.com/enkia/tokyo-night-vscode-theme, MIT) of
        # themes/tokyo-night/colors.toml, JetBrains Mono everywhere, and Hyprland's square windows
        # with 2px borders that turn accent blue on the one in focus. Selected things get Tokyo Night's
        # selection blue rather than a loud fill.
        "id": "tokyo-night", "name": "Tokyo Night",
        "description": "Omarchy's default look: Tokyo Night blues, square tiles and a coder's font. Always dark.",
        "fonts": {"body": "code", "display": "code"},
        "shape": {"radius": 0, "edge_width": 2, "lift": 0, "drawn": False, "grain": False, "tilt": False},
        "dark": _scheme(background="#13141c", sidebar="#1a1b26", surface="#1a1b26", surface_alt="#24283b",
                        text="#c0caf5", text_muted="#a9b1d6", text_faint="#737aa2", line="#292e42", edge="#414868",
                        accent="#7aa2f7", primary="#7aa2f7", on_primary="#13141c", highlight="#33467c",
                        on_highlight="#c0caf5", alert="#f7768e", on_alert="#13141c"),
    },
    {
        # After Windows XP's default blue look, in colours and shapes only: beige window
        # faces, bevelled white-to-beige buttons with navy outlines and small corners that glow on
        # hover, the green Start button as the main action, Explorer's selection blue for selected
        # things and its pale blue task pane as the sidebar. The ember becomes a glossy blue icon and
        # speaks in notification-style yellow balloons; no Microsoft artwork or characters. DejaVu
        # Sans Condensed stands in for Tahoma, which can't be redistributed.
        "id": "millennium", "name": "Millennium",
        "description": "An early-2000s desktop: beige windows, bevelled buttons, a glossy blue ember. Always light.",
        "fonts": {"body": "condensed", "display": "condensed"},
        "shape": {"radius": 4, "edge_width": 1, "lift": 0, "drawn": False, "grain": False, "tilt": False, "bevel": True,
                  "gloss": True},
        "light": _scheme(background="#ece9d8", sidebar="#d6dff7", surface="#ffffff", surface_alt="#f4f3ee",
                         text="#000000", text_muted="#3f3d36", text_faint="#716f64", line="#d0d0bf", edge="#003c74",
                         accent="#0046d5", primary="#2b7d2b", on_primary="#ffffff", highlight="#316ac5",
                         on_highlight="#ffffff", alert="#c4381d", on_alert="#ffffff"),
    },
]
PRESET_IDS = tuple(p["id"] for p in PRESETS)


# ----- contrast ---------------------------------------------------------------------------

def _luminance(hex_color: str) -> float:
    def channel(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def contrast_problems(colors: dict[str, str]) -> list[str]:
    out = []
    for fg, bg, need in CONTRAST:
        ratio = contrast(colors[fg], colors[bg])
        if ratio < need:
            out.append(f"{fg} on {bg} is {ratio:.1f}:1, needs {need}:1")
    return out


# ----- validation -------------------------------------------------------------------------

def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")
    return s or "theme"


def _colors(scheme: str, value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"{scheme} must be an object of colours: {', '.join(COLORS)}")
    unknown = set(value) - set(COLORS)
    if unknown:
        raise ValueError(f"{scheme} has unknown colours: {', '.join(sorted(unknown))}")
    missing = [k for k in COLORS if k not in value]
    if missing:
        raise ValueError(f"{scheme} is missing colours: {', '.join(missing)}")
    out = {}
    for k in COLORS:
        v = value[k]
        if not isinstance(v, str) or not _HEX.match(v.strip().lower()):
            raise ValueError(f"{scheme}.{k} must be a colour like #1a2b3c, got {v!r}")
        out[k] = v.strip().lower()
    problems = contrast_problems(out)
    if problems:
        raise ValueError(f"{scheme} colours are hard to read: " + "; ".join(problems))
    return out


def validate(theme: Any) -> dict[str, Any]:
    """A clean copy of ``theme``, or ValueError saying what to fix."""
    if not isinstance(theme, dict):
        raise ValueError("a theme must be an object")
    tid = theme.get("id")
    if not isinstance(tid, str) or not _ID.match(tid):
        raise ValueError("id must be lowercase letters, digits and dashes (at most 40)")
    name = theme.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 40 or any(ord(c) < 32 for c in name):
        raise ValueError("name must be 1 to 40 characters")
    desc = theme.get("description", "")
    if not isinstance(desc, str) or len(desc) > 200 or any(ord(c) < 32 for c in desc):
        raise ValueError("description must be at most 200 characters")
    fonts = theme.get("fonts")
    if not isinstance(fonts, dict) or fonts.get("body") not in BODY_FONTS or fonts.get("display") not in FONTS:
        raise ValueError(f"fonts.body must be one of {', '.join(BODY_FONTS)}; fonts.display one of {', '.join(FONTS)}")
    shape_in = theme.get("shape")
    if not isinstance(shape_in, dict):
        raise ValueError("shape must be an object")
    shape: dict[str, Any] = {}
    for k, (lo, hi, _) in SHAPE.items():
        v = shape_in.get(k)
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise ValueError(f"shape.{k} must be a whole number from {lo} to {hi}")
        shape[k] = v
    for k in FLAGS:
        v = shape_in.get(k, False)
        if not isinstance(v, bool):
            raise ValueError(f"shape.{k} must be true or false")
        shape[k] = v
    out: dict[str, Any] = {"id": tid, "name": name.strip(), "description": desc.strip(),
                           "fonts": {"body": fonts["body"], "display": fonts["display"]}, "shape": shape}
    for scheme in ("light", "dark"):
        if theme.get(scheme) is not None:
            out[scheme] = _colors(scheme, theme[scheme])
    if "light" not in out and "dark" not in out:
        raise ValueError("a theme needs light colours, dark colours, or both")
    return out


def summary(theme: dict[str, Any], *, custom: bool) -> dict[str, Any]:
    schemes = [s for s in ("light", "dark") if s in theme]
    swatch = theme.get("light") or theme["dark"]
    return {"id": theme["id"], "name": theme["name"], "description": theme["description"],
            "schemes": schemes, "custom": custom, "fonts": theme["fonts"],
            "swatch": [swatch["background"], swatch["surface"], swatch["primary"], swatch["highlight"], swatch["text"]]}


# ----- the user's themes ------------------------------------------------------------------

class ThemeStore:
    """Presets plus the user's own themes, kept in one JSON file next to the settings overrides."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def custom(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            return []
        out = []
        for t in data.get("themes", []) if isinstance(data, dict) else []:
            try:
                out.append(validate(t))
            except ValueError:
                continue  # a theme that no longer validates is skipped, not fatal
        return out

    def all(self) -> list[tuple[dict[str, Any], bool]]:
        return [(p, False) for p in PRESETS] + [(t, True) for t in self.custom()]

    def get(self, theme_id: str) -> dict[str, Any] | None:
        for t, _ in self.all():
            if t["id"] == theme_id:
                return t
        return None

    def resolve(self, theme_id: Any) -> dict[str, Any]:
        """The theme to show for a setting value: the named one, or Ember."""
        return (self.get(theme_id) if isinstance(theme_id, str) else None) or PRESETS[0]

    def _write(self, themes: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, (json.dumps({"themes": themes}, indent=2) + "\n").encode())

    def save(self, theme: dict[str, Any]) -> dict[str, Any]:
        clean = validate(theme)
        if clean["id"] in PRESET_IDS:
            raise ValueError(f"{clean['id']!r} is a preset; give your theme another name")
        themes = [t for t in self.custom() if t["id"] != clean["id"]]
        if len(themes) >= MAX_CUSTOM:
            raise ValueError(f"there are already {MAX_CUSTOM} themes; delete one first")
        self._write(themes + [clean])
        return clean

    def delete(self, theme_id: str) -> bool:
        if theme_id in PRESET_IDS:
            raise ValueError("presets can't be deleted")
        themes = self.custom()
        kept = [t for t in themes if t["id"] != theme_id]
        if len(kept) == len(themes):
            raise ValueError(f"there is no theme {theme_id!r} of yours")
        self._write(kept)
        return True


def build(name: str, *, based_on: dict[str, Any], description: str | None = None,
          fonts: dict[str, Any] | None = None, shape: dict[str, Any] | None = None,
          light: dict[str, Any] | None = None, dark: dict[str, Any] | None = None,
          schemes: str | None = None) -> dict[str, Any]:
    """A new theme: ``based_on`` with the given parts changed (colours can be partial)."""
    theme: dict[str, Any] = json.loads(json.dumps(based_on))
    theme["id"] = slug(name)
    theme["name"] = name
    theme["description"] = description if description is not None else f"Made from {based_on['name']}."
    if fonts:
        theme["fonts"] = {**theme["fonts"], **fonts}
    if shape:
        theme["shape"] = {**theme["shape"], **shape}
    for scheme, given in (("light", light), ("dark", dark)):
        if given:
            if not isinstance(given, dict):
                raise ValueError(f"{scheme} must be an object of colours")
            theme[scheme] = {**(theme.get(scheme) or {}), **given}
    if schemes is not None:
        if schemes not in ("both", "light", "dark"):
            raise ValueError("schemes must be both, light or dark")
        for s in ("light", "dark"):
            if schemes not in ("both", s):
                theme.pop(s, None)
            elif s not in theme:
                raise ValueError(f"the theme has no {s} colours yet; give {s} colours or base it on a theme that has them")
    return validate(theme)
