from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from conftest import check_surface, hints, mcp_session, serving

from foyelle_connectors.shell import themes
from foyelle_connectors.shell.server import ShellConfig, build_server

CANVAS_THEMES = Path(__file__).resolve().parents[2] / "canvas" / "src" / "renderer" / "themes.ts"


@pytest.fixture
def config(tmp_path: Path) -> ShellConfig:
    (tmp_path / "memories").mkdir()
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"ui": {"theme": "system"}}))
    return ShellConfig(tmp_path / "memories", settings_file, tmp_path / "overrides.json", tmp_path / "audit.jsonl", {}, "none")


def test_twelve_presets_all_valid_and_readable() -> None:
    assert len(themes.PRESETS) == 12
    assert len(set(themes.PRESET_IDS)) == 12
    assert themes.PRESET_IDS[0] == themes.DEFAULT_ID == "ember"
    for preset in themes.PRESETS:
        assert themes.validate(preset)["id"] == preset["id"]
        for scheme in ("light", "dark"):
            if scheme in preset:
                assert themes.contrast_problems(preset[scheme]) == [], (preset["id"], scheme)
    # Some come in both schemes, some in one: the canvas follows whichever they have.
    assert {p["id"] for p in themes.PRESETS if "light" not in p} == {"midnight", "terminal", "tokyo-night"}
    assert {p["id"] for p in themes.PRESETS if "dark" not in p} == {"millennium"}


def test_tokyo_night_is_omarchys_look() -> None:
    t = themes.validate(next(p for p in themes.PRESETS if p["id"] == "tokyo-night"))
    # Omarchy's themes/tokyo-night/colors.toml: background, accent, foregrounds and red.
    dark = t["dark"]
    assert (dark["surface"], dark["primary"], dark["text"], dark["alert"]) == ("#1a1b26", "#7aa2f7", "#c0caf5", "#f7768e")
    # JetBrains Mono for everything, square corners and Hyprland's 2px borders, nothing playful.
    assert t["fonts"] == {"body": "code", "display": "code"}
    assert t["shape"] == {"radius": 0, "edge_width": 2, "lift": 0, "drawn": False, "grain": False, "tilt": False, "bevel": False,
                          "gloss": False}
    assert themes.build("Tokyo Day", based_on=t, fonts={"body": "sans"})["fonts"] == {"body": "sans", "display": "code"}


def test_millennium_is_an_early_2000s_desktop_in_colours_and_shapes() -> None:
    t = themes.validate(next(p for p in themes.PRESETS if p["id"] == "millennium"))
    light = t["light"]
    # Beige window faces, the green start button, Explorer's selection blue, navy button outlines.
    assert (light["background"], light["primary"], light["highlight"], light["edge"]) == ("#ece9d8", "#2b7d2b", "#316ac5", "#003c74")
    assert "dark" not in t  # XP had no dark mode
    # A free Tahoma look-alike, bevelled buttons with small corners and thin outlines, no hard shadow,
    # and the ember as a glossy icon that speaks in yellow balloons.
    assert t["fonts"] == {"body": "condensed", "display": "condensed"}
    assert t["shape"] == {"radius": 4, "edge_width": 1, "lift": 0, "drawn": False, "grain": False, "tilt": False,
                          "bevel": True, "gloss": True}
    assert [p["id"] for p in themes.PRESETS if p["shape"].get("bevel") or p["shape"].get("gloss")] == ["millennium"]
    with pytest.raises(ValueError, match="shape.bevel must be true or false"):
        themes.build("Half bevel", based_on=t, shape={"bevel": "yes"})
    with pytest.raises(ValueError, match="no dark colours"):
        themes.build("Millennium at night", based_on=t, schemes="both")


def test_contrast_is_wcag() -> None:
    assert themes.contrast("#000000", "#ffffff") == pytest.approx(21)
    assert themes.contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)


def test_build_changes_only_what_is_asked() -> None:
    ocean = next(p for p in themes.PRESETS if p["id"] == "ocean")
    t = themes.build("Pink Ocean", based_on=ocean, light={"primary": "#ffb3d1", "on_primary": "#3a0f22"})
    assert t["id"] == "pink-ocean" and t["light"]["primary"] == "#ffb3d1"
    assert t["light"]["background"] == ocean["light"]["background"]
    assert t["dark"] == ocean["dark"]
    assert t["description"] == "Made from Ocean."
    night = themes.build("Night only", based_on=ocean, schemes="dark")
    assert "light" not in night and "dark" in night
    with pytest.raises(ValueError, match=r"on_primary on primary is 1\.\d:1, needs 4\.5:1"):
        themes.build("Faint", based_on=ocean, light={"primary": "#ffffff", "on_primary": "#eeeeee"})
    with pytest.raises(ValueError, match="like #1a2b3c"):
        themes.build("Bad", based_on=ocean, light={"primary": "red; display:none"})
    with pytest.raises(ValueError, match="unknown colours: sparkle"):
        themes.build("Bad", based_on=ocean, light={"sparkle": "#ffffff"})
    with pytest.raises(ValueError, match="fonts.body"):
        themes.build("Bad", based_on=ocean, fonts={"body": "hand"})
    with pytest.raises(ValueError, match="shape.radius"):
        themes.build("Bad", based_on=ocean, shape={"radius": 100})
    midnight = next(p for p in themes.PRESETS if p["id"] == "midnight")
    with pytest.raises(ValueError, match="no light colours"):
        themes.build("Day", based_on=midnight, schemes="both")


def test_store_keeps_custom_themes_apart_from_presets(tmp_path: Path) -> None:
    store = themes.ThemeStore(tmp_path / "themes.json")
    assert store.resolve(None)["id"] == "ember"
    assert store.resolve("gone")["id"] == "ember"
    t = themes.build("Forest", based_on=themes.PRESETS[3])
    store.save(t)
    assert store.get("forest")["name"] == "Forest"
    assert [x["id"] for x, custom in store.all() if custom] == ["forest"]
    store.save(themes.build("Forest", based_on=themes.PRESETS[4]))  # same name replaces
    assert len(store.custom()) == 1 and store.get("forest")["fonts"] == themes.PRESETS[4]["fonts"]
    with pytest.raises(ValueError, match="is a preset"):
        store.save(themes.build("Ember", based_on=themes.PRESETS[1]))
    with pytest.raises(ValueError, match="can't be deleted"):
        store.delete("ember")
    store.delete("forest")
    assert store.custom() == []
    # A hand-edited file with a broken theme doesn't take the others down.
    (tmp_path / "themes.json").write_text(json.dumps({"themes": [{"id": "x"}, t]}))
    assert [x["id"] for x in store.custom()] == ["forest"]


def test_canvas_knows_the_same_colours_and_fonts() -> None:
    if not CANVAS_THEMES.exists():
        pytest.skip("canvas not in this checkout")
    ts = CANVAS_THEMES.read_text()
    keys = re.search(r"COLOR_KEYS = \[(.*?)\] as const", ts, re.S).group(1)
    assert tuple(re.findall(r'"([a-z_]+)"', keys)) == themes.COLORS
    fonts = re.search(r"FONT_STACKS: Record<FontKey, string> = \{(.*?)\};", ts, re.S).group(1)
    assert tuple(re.findall(r"^\s*([a-z]+):", fonts, re.M)) == themes.FONTS


async def test_theme_tools(config: ShellConfig) -> None:
    with serving(build_server(config)) as url:
        async with mcp_session(url) as session:
            tools = await check_surface(session, "theme_list", "ui://shell/themes")
            assert hints(tools["theme_list"]) == (True, False, False)
            assert hints(tools["theme_use"]) == (False, False, False)  # read-only, destructive, open-world
            assert hints(tools["theme_delete"]) == (False, True, False)
            listed = (await session.call_tool("theme_list", {})).structuredContent
            assert listed["current"] == "ember" and len(listed["themes"]) == 12
            assert all(len(t["swatch"]) == 5 for t in listed["themes"])

            settings = (await session.call_tool("settings_get", {})).structuredContent
            assert settings["theme"]["id"] == "ember"

            used = await session.call_tool("theme_use", {"theme_id": "terminal"})
            assert used.structuredContent == {"current": "terminal", "name": "Terminal"}
            assert (await session.call_tool("settings_get", {})).structuredContent["theme"]["fonts"]["body"] == "mono"
            assert (await session.call_tool("theme_use", {"theme_id": "nope"})).isError

            made = await session.call_tool("theme_create", {
                "name": "Forest at night", "based_on": "meadow", "schemes": "dark",
                "dark": {"background": "#0c140e", "primary": "#9fdc7c"}, "shape": {"lift": 0, "tilt": True},
            })
            assert not made.isError, made.content
            theme = made.structuredContent["theme"]
            assert theme["id"] == "forest-at-night" and "light" not in theme and theme["shape"]["tilt"] is True
            assert (await session.call_tool("theme_list", {})).structuredContent["current"] == "forest-at-night"

            faint = await session.call_tool("theme_create", {"name": "Faint", "light": {"text": "#eeeeee"}})
            assert faint.isError and "text on background" in faint.content[0].text

            got = (await session.call_tool("theme_get", {"theme_id": "forest-at-night"})).structuredContent
            assert got["custom"] is True and got["theme"]["dark"]["background"] == "#0c140e"

            gone = (await session.call_tool("theme_delete", {"theme_id": "forest-at-night"})).structuredContent
            assert gone == {"deleted": "forest-at-night", "current": "ember"}
            assert (await session.call_tool("theme_delete", {"theme_id": "ember"})).isError
