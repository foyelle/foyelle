"""shell connector (port 7300): status, memory, settings and audit surfaces."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from .. import sdk
from . import audit, bots, memory, model_key, privacy, routines, services, settings, status, themes

INSTRUCTIONS = (
    "Foyelle system surfaces: status (model, connectors, network), the agent's memory files, "
    "user settings, themes and the audit log. Use these tools to show the user these surfaces."
)


@dataclass
class ShellConfig:
    memory_dir: Path
    settings_file: Path
    overrides_file: Path
    audit_log: Path
    connectors: dict[str, int]
    health_method: str = "systemd"
    unit_template: str = "foyelle-connector-{name}.service"
    harness_env: Path = Path("/var/lib/foyelle/harness/.env")
    remote_key_names: tuple[str, ...] = model_key.DEFAULT_KEY_NAMES
    remote_label: str = "OpenCode Zen"
    outbound_tools: frozenset[str] = privacy.DEFAULT_OUTBOUND

    @classmethod
    def from_env(cls) -> ShellConfig:
        return cls(
            memory_dir=sdk.env_path("FOYELLE_MEMORY_DIR", "/var/lib/foyelle/memory"),
            settings_file=sdk.env_path("FOYELLE_SETTINGS_FILE", "/etc/foyelle/settings.json"),
            overrides_file=sdk.env_path("FOYELLE_SETTINGS_OVERRIDES", "/var/lib/foyelle/settings-overrides.json"),
            audit_log=sdk.env_path("FOYELLE_AUDIT_LOG", "/var/lib/foyelle/audit/audit.jsonl"),
            connectors=status.parse_connectors(sdk.env("FOYELLE_CONNECTORS")),
            health_method=sdk.env("FOYELLE_HEALTH_METHOD", "systemd") or "systemd",
            unit_template=sdk.env("FOYELLE_CONNECTOR_UNIT", "foyelle-connector-{name}.service")
            or "foyelle-connector-{name}.service",
            harness_env=sdk.env_path("FOYELLE_HARNESS_ENV", "/var/lib/foyelle/harness/.env"),
            remote_key_names=model_key.key_names(sdk.env("FOYELLE_REMOTE_KEY_ENV")),
            remote_label=sdk.env("FOYELLE_REMOTE_LABEL", "OpenCode Zen") or "OpenCode Zen",
            outbound_tools=privacy.parse_outbound(sdk.env("FOYELLE_OUTBOUND_TOOLS")),
        )


def build_server(config: ShellConfig, *, port: int | None = None) -> FastMCP:
    server = sdk.create_server("shell", INSTRUCTIONS, port=port)
    store = memory.MemoryStore(config.memory_dir)
    prefs = settings.Settings(config.settings_file, config.overrides_file)
    looks = themes.ThemeStore(config.overrides_file.parent / "themes.json")
    connected = services.ServicesState(config.overrides_file.parent / "services.json")
    conversations = services.ConversationServices(config.overrides_file.parent / "conversations")
    roster = bots.BotStore(config.overrides_file.parent / "bots.json")
    waiting = bots.WaitingStore(config.overrides_file.parent / "waiting")

    def can_give() -> list[dict[str, Any]]:
        return bots.givable(set(config.connectors), connected.listing()["services"])

    def connected_service(name: str) -> dict[str, Any]:
        """A connected service by id or title (as people say it), or ValueError saying what to do."""
        wanted = (name or "").strip().lower()
        for s in connected.listing()["services"]:
            if wanted in (s["id"], s["title"].lower(), s["title"].split()[0].split(".")[0].lower()):
                if not s["connected"]:
                    raise ValueError(f"{s['title']} isn't connected. The person can connect it on the Accounts card.")
                return s
        raise ValueError(f"no service called {name!r}; services_list shows them")

    def service_state(service_id: str) -> str:
        # Only systemd knows a service's helper; elsewhere (development) it runs in-process.
        if config.health_method != "systemd":
            return "unknown"
        return status.connector_health({service_id: 0}, config.health_method, config.unit_template)[0]["state"]

    def current_theme_id() -> str:
        ui = prefs.effective().get("ui")
        return looks.resolve(ui.get("style") if isinstance(ui, dict) else None)["id"]

    def theme_gallery() -> dict[str, Any]:
        current = current_theme_id()
        ui = prefs.effective().get("ui")
        return {
            "current": current,
            "scheme": (ui.get("theme") if isinstance(ui, dict) else None) or "system",
            "themes": [themes.summary(t, custom=c) for t, c in looks.all()],
        }

    sdk.register_page(server, "status", "ui://shell/status", "shell_status.html", title="Status",
                      description="Model in use, connector health and network status.")
    sdk.register_page(server, "memory", "ui://shell/memory", "shell_memory.html", title="Memory",
                      description="Browse, correct, delete and export what the agent remembers.")
    sdk.register_page(server, "settings", "ui://shell/settings", "shell_settings.html", title="Settings",
                      description="Key settings and the few the user can change here.")
    sdk.register_page(server, "themes", "ui://shell/themes", "shell_themes.html", title="Themes",
                      description="The canvas's themes: pick one, or see the ones you made.")
    sdk.register_page(server, "audit", "ui://shell/audit", "shell_audit.html", title="Audit log",
                      description="Every tool call and approval, newest first.")
    sdk.register_page(server, "privacy", "ui://shell/privacy", "shell_privacy.html", title="What left your home",
                      description="What left this computer today, and what stayed home.")

    @server.tool(
        name="status",
        title="System status",
        annotations=sdk.annotations(read_only=True),
        meta=sdk.ui_meta("ui://shell/status"),
    )
    def system_status() -> dict[str, Any]:
        """Show the model in use, the health of each connector and the network status."""
        return {
            "hostname": socket.gethostname(),
            "uptime_seconds": status.uptime_seconds(),
            "model": status.model_status(prefs.effective()),
            "connectors": status.connector_health(config.connectors, config.health_method, config.unit_template),
            "network": status.network_status(),
        }

    @server.tool(
        title="Read memory",
        annotations=sdk.annotations(read_only=True),
        meta=sdk.ui_meta("ui://shell/memory"),
    )
    def memory_read(file: Literal["MEMORY.md", "USER.md"] | None = None) -> dict[str, Any]:
        """Read the agent's memory files (MEMORY.md: notes; USER.md: what it knows about the user).

        Entries are separated by a line containing only "§".
        """
        return store.read(file)

    @server.tool(
        title="Write memory",
        annotations=sdk.annotations(read_only=False, destructive=True, idempotent=True),
    )
    def memory_write(
        file: Literal["MEMORY.md", "USER.md"], content: str, expected_sha256: str | None = None
    ) -> dict[str, Any]:
        """Replace a memory file's content (the previous version is kept as a backup).

        Pass the sha256 from memory_read as expected_sha256 to avoid overwriting a newer change.
        """
        return store.write(file, content, expected_sha256)

    @server.tool(
        title="Remember the user's name",
        annotations=sdk.annotations(read_only=False, destructive=False, idempotent=True),
    )
    def user_name_set(name: str) -> dict[str, Any]:
        """Remember what to call the user: shown on the home screen and kept in USER.md."""
        saved = prefs.set("user.name", name)
        store.set_name(saved["value"])
        return {"name": saved["value"]}

    @server.tool(title="Open Accounts", annotations=sdk.annotations(read_only=True))
    def open_accounts() -> dict[str, Any]:
        """Open the Accounts card on the screen, where the person connects or disconnects their mail,
        calendar and other services (such as Todoist) and signs in to them. Use it when they ask to
        connect, sign in to or set up one of these. Signing in happens on the card, never in chat."""
        # The canvas opens the card when this tool completes; nothing happens here.
        return {"opened": True}

    @server.tool(title="Export memory", annotations=sdk.annotations(read_only=True))
    def memory_export() -> dict[str, Any]:
        """Export both memory files as one Markdown document."""
        return store.export()

    @server.tool(
        title="Settings",
        annotations=sdk.annotations(read_only=True),
        meta=sdk.ui_meta("ui://shell/settings"),
    )
    def settings_get() -> dict[str, Any]:
        """Show the effective settings, the user's overrides and which keys can be changed."""
        ui = prefs.effective().get("ui")
        return {**prefs.get(), "remote_provider": config.remote_label,
                "remote_key_configured": model_key.configured(config.harness_env, config.remote_key_names),
                "theme": looks.resolve(ui.get("style") if isinstance(ui, dict) else None)}

    @server.tool(title="Services", annotations=sdk.annotations(read_only=True))
    def services_list() -> dict[str, Any]:
        """The services the person can connect (the reviewed Services directory), which are
        connected, and whether each one's helper is running."""
        return connected.listing(service_state)

    @server.tool(title="Use a service", annotations=sdk.annotations(read_only=True))
    def use_service(service: str) -> dict[str, Any]:
        """Load a connected service's tools into this conversation (Foyelle offers a service only when
        the conversation uses it). Call it with the service's name, then use its tools."""
        s = connected_service(service)
        return {"service": s["id"], "title": s["title"], "loaded": True,
                "note": f"{s['title']}'s tools are available now in this conversation."}

    @server.tool(title="Services in a conversation", annotations=sdk.annotations(read_only=True))
    def conversation_services(session_id: str) -> dict[str, Any]:
        """For the canvas: which services a conversation turned on or off."""
        return conversations.get(session_id)

    @server.tool(
        title="Turn a service on or off for a conversation",
        annotations=sdk.annotations(read_only=False, destructive=False, idempotent=True),
    )
    def conversation_service_set(session_id: str, service_id: str, on: bool) -> dict[str, Any]:
        """For the canvas's chips only: turn a connected service on or off for one conversation."""
        connected_service(service_id)
        return conversations.set(session_id, service_id, on)

    @server.tool(
        title="Connect a service",
        annotations=sdk.annotations(read_only=False, destructive=False, idempotent=True),
    )
    def service_connect(service_id: str) -> dict[str, Any]:
        """For the Services card only: connect a service from the directory (its helper starts)."""
        return connected.set(service_id, True)

    @server.tool(
        title="Disconnect a service",
        annotations=sdk.annotations(read_only=False, destructive=True, idempotent=True),
    )
    def service_disconnect(service_id: str) -> dict[str, Any]:
        """For the Services card only: disconnect a service (its helper stops)."""
        return connected.set(service_id, False)

    @server.tool(title="Bots", annotations=sdk.annotations(read_only=True))
    def bots_list() -> dict[str, Any]:
        """The person's bots (name, picture, job, the services each may use) and what a bot can be
        given. Foyelle itself is not in the list."""
        return roster.listing(can_give())

    @server.tool(
        title="Save a bot",
        annotations=sdk.annotations(read_only=False, destructive=False, idempotent=True),
    )
    def bot_save(
        name: str,
        job: str = "",
        picture: str = "",
        services: list[str] | None = None,
        share_online: list[str] | None = None,
        bot_id: str | None = None,
    ) -> dict[str, Any]:
        """For the canvas only: create a bot (no ``bot_id``) or change one. ``services`` are the ones
        it may use; ``share_online`` the private ones its routines may send to the online model."""
        return roster.save(can_give(), name=name, job=job, picture=picture, services=services,
                           share_online=share_online, bot_id=bot_id)

    @server.tool(
        title="Delete a bot",
        annotations=sdk.annotations(read_only=False, destructive=True, idempotent=True),
    )
    def bot_delete(bot_id: str) -> dict[str, Any]:
        """For the canvas only: take a bot off the list. It can use nothing from then on."""
        return roster.delete(bot_id)

    @server.tool(
        title="Foyelle's routines and the online model",
        annotations=sdk.annotations(read_only=False, destructive=False, idempotent=True),
    )
    def foyelle_share_set(share_online: list[str]) -> dict[str, Any]:
        """For the canvas only: which private services Foyelle's own routines may share online."""
        return roster.share_for_foyelle(can_give(), share_online)

    @server.tool(title="Waiting for you", annotations=sdk.annotations(read_only=True))
    def waiting_list() -> dict[str, Any]:
        """For the canvas: what routines left for the person to approve or drop."""
        return waiting.listing()

    @server.tool(
        title="Drop a waiting item",
        annotations=sdk.annotations(read_only=False, destructive=True, idempotent=True),
    )
    def waiting_drop(item_id: str) -> dict[str, Any]:
        """For the canvas only: drop what a routine left (after running it, or when the person says no)."""
        return waiting.drop(item_id)

    @server.tool(title="What the routines said", annotations=sdk.annotations(read_only=True))
    def routine_results(since: float = 0.0, limit: int = 40) -> dict[str, Any]:
        """For the canvas: each routine run newer than ``since`` (Unix seconds), from Foyelle and
        every bot, newest first: the bot, the routine's name, when it ran and what it said."""
        return routines.results(config.harness_env.parent, since=since, limit=limit)

    @server.tool(
        title="Themes",
        annotations=sdk.annotations(read_only=True),
        meta=sdk.ui_meta("ui://shell/themes"),
    )
    def theme_list() -> dict[str, Any]:
        """Show the canvas themes (twelve presets plus the user's own) on the Themes card, and which one is
        in use. Use it when the user wants to see, compare or change how Foyelle looks."""
        return theme_gallery()

    @server.tool(
        title="Get a theme",
        annotations=sdk.annotations(read_only=True),
    )
    def theme_get(theme_id: str) -> dict[str, Any]:
        """A theme's full definition (fonts, shape and every colour), to start a new theme from it."""
        theme = looks.get(theme_id)
        if theme is None:
            raise ValueError(f"no theme {theme_id!r}; theme_list shows them")
        return {"theme": theme, "custom": theme["id"] not in themes.PRESET_IDS}

    @server.tool(
        title="Use a theme",
        annotations=sdk.annotations(read_only=False, destructive=False, idempotent=True),
    )
    def theme_use(theme_id: str) -> dict[str, Any]:
        """Switch the canvas to a theme (a preset or one the user made). The screen changes at once."""
        theme = looks.get(theme_id)
        if theme is None:
            raise ValueError(f"no theme {theme_id!r}; theme_list shows them")
        prefs.set("ui.style", theme["id"])
        return {"current": theme["id"], "name": theme["name"]}

    @server.tool(
        title="Make a theme",
        annotations=sdk.annotations(read_only=False, destructive=False, idempotent=True),
    )
    def theme_create(
        name: str,
        based_on: str = themes.DEFAULT_ID,
        description: str | None = None,
        fonts: dict[str, str] | None = None,
        shape: dict[str, int | bool] | None = None,
        light: dict[str, str] | None = None,
        dark: dict[str, str] | None = None,
        schemes: Literal["both", "light", "dark"] | None = None,
        use: bool = True,
    ) -> dict[str, Any]:
        """Make a new canvas theme for the user and (by default) switch to it. Start from an existing
        theme (based_on) and change only what the user asked for; theme_get shows a theme's parts.

        fonts: {"body": rounded|sans|serif|mono|code|condensed, "display": rounded|sans|serif|mono|hand|code|condensed}
          (rounded = Nunito, sans = Inter, serif = Noto Serif, mono = Noto Sans Mono, hand = Gochi Hand,
          code = JetBrains Mono, condensed = DejaVu Sans Condensed).
        shape: {"radius": 0-32, "edge_width": 0-4, "lift": 0-6, "drawn": bool, "grain": bool, "tilt": bool, "bevel": bool,
          "gloss": bool}
          (radius: corners; edge_width: outlines; lift: hard shadow under buttons; drawn: wobbly hand-drawn
          input outline; grain: paper texture; tilt: chips and stickers slightly askew; bevel: buttons shaded
          light to dark like an early-2000s desktop, glowing on hover; gloss: Foyelle's ember as a glossy
          icon in the highlight colour, its words in pale yellow balloons).
        light / dark: colours as #rrggbb, any of: background, sidebar, surface, surface_alt, text,
          text_muted, text_faint, line, edge, accent, primary, on_primary, highlight, on_highlight,
          alert, on_alert. Give both schemes unless the theme is meant to be only light or only dark
          (then set schemes). Text must stay readable: if a colour pair is too faint the error says
          which, with its contrast ratio, so adjust and try again.
        Making a theme with the same name again replaces it."""
        base = looks.get(based_on)
        if base is None:
            raise ValueError(f"no theme {based_on!r} to start from; theme_list shows them")
        theme = themes.build(name, based_on=base, description=description, fonts=fonts, shape=shape,
                             light=light, dark=dark, schemes=schemes)
        saved = looks.save(theme)
        if use:
            prefs.set("ui.style", saved["id"])
        return {"theme": saved, "in_use": use}

    @server.tool(
        title="Delete a theme",
        annotations=sdk.annotations(read_only=False, destructive=True, idempotent=True),
    )
    def theme_delete(theme_id: str) -> dict[str, Any]:
        """Delete one of the user's own themes (presets stay). If it was in use, Ember comes back."""
        was_current = current_theme_id() == theme_id
        looks.delete(theme_id)
        if was_current:
            prefs.set("ui.style", None)
        return {"deleted": theme_id, "current": current_theme_id()}

    @server.tool(
        title="Save the remote model key",
        annotations=sdk.annotations(read_only=False, destructive=False, idempotent=True),
    )
    def model_key_save(key: str) -> dict[str, Any]:
        """Save the remote provider's API key the user typed on the Settings surface and switch to the
        remote model route. The harness restarts to apply it. Only the Settings surface may call
        this; the key is never returned."""
        model_key.save(config.harness_env, key, config.remote_key_names)
        prefs.set("model.route", "remote")
        return {"saved": True, "route": "remote", "restarting": True}

    @server.tool(
        title="Remove the remote model key",
        annotations=sdk.annotations(read_only=False, destructive=True, idempotent=True),
    )
    def model_key_clear() -> dict[str, Any]:
        """Remove the saved remote API key and switch to the local model route."""
        model_key.clear(config.harness_env, config.remote_key_names)
        prefs.set("model.route", "local")
        return {"cleared": True, "route": "local", "restarting": True}

    @server.tool(
        title="Change a setting",
        annotations=sdk.annotations(read_only=False, destructive=True, idempotent=True),
    )
    def settings_set(key: str, value: str | int | float | bool | None = None) -> dict[str, Any]:
        """Change one user setting. Only these keys are allowed: ui.theme, ui.text_scale,
        ui.locale, ui.natural_scroll, ui.welcomed, user.name, model.route, model.main, model.auxiliary,
        model.local. A null value restores the default. To change the user's name use user_name_set."""
        return prefs.set(key, value)

    @server.tool(
        title="Audit log",
        annotations=sdk.annotations(read_only=True),
        meta=sdk.ui_meta("ui://shell/audit"),
    )
    def audit_query(
        session_id: str | None = None,
        tool: str | None = None,
        connector: str | None = None,
        decision: Literal["allow", "ask", "deny"] | None = None,
        origin: Literal["model", "surface"] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Query the audit log, newest first. ``tool`` matches as a substring."""
        return audit.query(
            config.audit_log, session_id=session_id, tool=tool, connector=connector,
            decision=decision, origin=origin, limit=limit, offset=offset,
        )

    @server.tool(
        name="privacy_today",
        title="What left your home today",
        annotations=sdk.annotations(read_only=True),
        meta=sdk.ui_meta("ui://shell/privacy"),
    )
    def privacy_today() -> dict[str, Any]:
        """Show what left this computer today (sent, searched, shared with the online model) and what
        was held back, from the audit log. Use it when the user asks what was shared or sent."""
        model = prefs.effective().get("model")
        model = dict(model) if isinstance(model, dict) else {}
        model["provider_label"] = config.remote_label
        model["remote_key_configured"] = model_key.configured(config.harness_env, config.remote_key_names)
        return privacy.today(config.audit_log, outbound=config.outbound_tools, model=model)

    return server


def main() -> None:
    sdk.run(build_server(ShellConfig.from_env()))
