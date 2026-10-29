# Writing a connector for this machine

A connector gives you new tools (and, if it has one, a surface the user sees). It is a system
change: you write it into the config repo with `propose`, `build` it, and the user reviews the
code and its permissions on the System surface before approving. Once applied it runs isolated
like the built-in connectors: its own user and storage, no access to other connectors' data, and
network only to the hosts it declares.

Only write one when the user asks for a new capability that no existing tool covers. If the
service already has its own remote MCP server (an https address), don't write a connector: propose
it with `propose_service` instead (its URL, the hosts it needs and how the person signs in, from
the service's documentation), and the person connects it on the Accounts card once applied. For a view
over tools that already exist (a dashboard, a list, a form), make an instant surface instead
(`howto("surface")`): no review build is needed.

## Files to propose (one `propose` call)

1. `connectors/<name>/server.py`: the MCP server. Python standard library, the `httpx` HTTP client
   and Foyelle's SDK (`foyelle_connectors.sdk`) are available; nothing else can be installed.
2. `connectors/<name>/view.html` (optional): the surface, body markup plus a `<script>` using
   `FoyelleView` (see below).
3. `configuration.nix`: the full new content, with the existing settings kept and a
   `foyelle.connectors.<name>` block added.

`<name>` is lowercase letters and digits. Pick a free `port` (7400-7499) and `index` (40-99);
the ones in use are listed under "Taken" below.

## Example: a weather connector with a surface

`connectors/weather/server.py`:

```python
from typing import Any

import httpx

from foyelle_connectors import sdk

server = sdk.create_server("weather", "Current weather for a city, from open-meteo.com.")
sdk.register_html_page(server, "forecast", "ui://weather/forecast", "view.html", title="Weather")


@server.tool(title="Current weather", annotations=sdk.annotations(read_only=True, open_world=True),
             meta=sdk.ui_meta("ui://weather/forecast"))
def current(latitude: float, longitude: float) -> dict[str, Any]:
    """Current temperature and wind at a place (latitude, longitude in degrees)."""
    r = httpx.get("https://api.open-meteo.com/v1/forecast", timeout=20,
                  params={"latitude": latitude, "longitude": longitude, "current_weather": "true"})
    r.raise_for_status()
    return r.json()["current_weather"]


if __name__ == "__main__":
    sdk.run(server)
```

`connectors/weather/view.html`:

```html
<main>
  <h1>Weather</h1>
  <p id="out" class="muted">Ask the agent for the weather somewhere.</p>
</main>
<script>
(function () {
  var V = FoyelleView;
  V.start({ name: "weather", render: function (d) {
    if (d) V.$("#out").textContent = d.temperature + " °C, wind " + d.windspeed + " km/h";
  } });
})();
</script>
```

Added to `configuration.nix`, inside the top-level `{ ... }`:

```nix
  foyelle.connectors.weather = {
    enable = true;
    source = ./connectors/weather;
    port = 7400;
    index = 40;
    sensitivity = "public";          # "private" if it handles the user's personal data
    egressHosts = [ "api.open-meteo.com" ];
    tools.current = { tier = "read"; untrusted = true; };
  };
```

## Rules

- **Tiers**: declare every tool under `tools.<tool>`. `read` (no side effects), `reversible`
  (changes the user can undo), `outbound` (sends or publishes anything; asks the user each time),
  `system` (never use for your own connectors). Undeclared tools are treated as `outbound`.
  Mark tools that return outside content (web pages, emails, API data) `untrusted = true`.
- **Network**: list exact host names in `egressHosts`; everything else is blocked. No IP addresses,
  no wildcards unless the user asked for general web access.
- **Secrets**: never put keys or passwords in the repo or in chat. If a service needs a key, tell
  the user; a key is added by the user on the machine, not by you.
- **Storage**: the connector may write only to its home directory (`$HOME`).
- **Surfaces**: `FoyelleView.start({name, render, load})` renders the tool result that opened the
  surface (`render(data)`); `FoyelleView.run(tool, args, busyText)` calls one of this connector's
  own tools and resolves with its data. `V.$(selector)`, `V.h(tag, attrs, ...children)`,
  `V.status(text)`, `V.fmtDate`, `V.fmtBytes` help. No network from the surface.
- Then `build` the proposal once. If the build fails, read the log in the result, fix the files
  in a new proposal, and tell the user what changed. Do not retry blindly.
