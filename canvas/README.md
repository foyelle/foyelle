# Foyelle canvas

The only program on screen: a full-screen Electron app that cage starts on
NixOS. It is a client of the harness gateway (`docs/architecture.md` §1) and
the host for MCP App surfaces.

## Architecture

```
src/main/       main process: kiosk window, foyelle-app:// and foyelle-surface:// protocols, IPC
src/preload/    sandboxed preload: window.foyelle = { harness, registerSurface, unregisterSurface, onShortcut }
src/shared/     CSP builders and the bridge types shared by main, preload, renderer and dev server
src/renderer/   the canvas UI (plain TypeScript + DOM)
  gateway.ts        JSON-RPC 2.0 over WebSocket (tui_gateway dialect): requests, events,
                    server requests, request.cancel, heartbeat, reconnect with backoff
  surfaces.ts       one surface = sandboxed iframe + ext-apps AppBridge + PostMessageTransport
  surface-state.ts  focus / pin / dismiss rules (no DOM)
  stage.ts          the surface area: focused surface + tile strip (iframes move by transform only)
  system-prompts.ts approvals, clarify, secret, sudo and link cards as built-in MCP App views
  builtin-views/    the built-in views (HTML + script, inlined at build time) and a small
                    View-side MCP Apps runtime they share
  chat.ts, markdown.ts, commands.ts, theme.ts, app.ts
dev/            dev:web server, mock harness, demo surfaces
demo/           the web demo at foyelle.org/demo (static, no server, no model)
test/           vitest (offline)
```

- **Renderer** is served from `foyelle-app://canvas/` with a CSP that allows
  only its own script and style, `connect-src` to the harness origin and
  `frame-src foyelle-surface:`.
- **Surfaces.** On `surface.open` (or a `surface.open_resource` result) the
  renderer registers the HTML with main over IPC (`surface:register
  {surfaceId, html, csp}`), creates `<iframe sandbox="allow-scripts">`
  (no `allow-same-origin`, so the document has an opaque origin), connects an
  `AppBridge` to the frame's window, then sets `src =
  foyelle-surface://<surfaceId>/`. Main serves that one URL with
  `Content-Security-Policy` built from `csp` and `X-Content-Type-Options:
  nosniff`. `<surfaceId>` is a canvas-local key (the harness `surface_id` is
  kept separately and sent back with `surface.call_tool`). Teardown sends
  `ui/resource-teardown`, removes the frame and unregisters the HTML.
- **Bridge handlers.** `tools/call` → `surface.call_tool`, `resources/read` →
  `surface.read_resource`, `ui/message` → `prompt.submit` in the current
  session, `ui/open-link` → a link card (no browser), `ui/request-display-mode`
  → always `inline` (a fullscreen request focuses the surface),
  `size-changed` sets the frame height within the focus area. After
  `initialized` the host sends `tool-input` then `tool-result` for surfaces
  that came from a tool call. Host context carries theme, display mode,
  container size and style variables.
- **System UI through the same path.** Approval, clarify, secret and sudo
  server requests become built-in views in the same sandboxed iframe +
  AppBridge path. The view calls a host-local tool (`approval_respond
  {choice}`, `clarify_respond {answers}`, `secret_respond {value}`,
  `secret_cancel`, `link_close`), and the host settles the pending server
  request. `request.cancel` removes the card.
- **Layout.** Chat rail and command bar on the left; one focused surface plus
  up to 4 pinned and 3 recent tiles on the right. The user can focus, pin or
  dismiss; nothing is dragged or resized.

## Keyboard and commands

| | |
|---|---|
| `Ctrl+K` | focus the command bar (works even while a surface has focus) |
| `Ctrl+1`…`9` | focus tile N |
| `Esc` | dismiss the focused surface (clears the command bar first if it has text) |
| `/focus <n\|name>`, `/pin`, `/unpin`, `/dismiss` | surface commands (name or tile number optional for the last three) |
| `/surfaces [n\|name]` | list system surfaces (`surface.list`), or open one (`surface.open_resource`) |
| `/new`, `/stop`, `/help` | new session, `session.interrupt`, help |
| `//text` | send `/text` as a prompt |

## Environment

| Variable | Default | |
|---|---|---|
| `FOYELLE_HARNESS_URL` | `ws://127.0.0.1:9119/api/ws` | gateway WebSocket (ws/wss) |
| `FOYELLE_HARNESS_TOKEN_FILE` | `/run/foyelle/harness-token` | read again before every (re)connect; sent as `?token=` |
| `FOYELLE_CANVAS_DEV` | unset | `1` enables DevTools (detached) |

On Wayland, run with `--ozone-platform=wayland` (or `NIXOS_OZONE_WL=1` with
the nixpkgs wrapper) unless the Electron build already auto-detects it.

## Scripts

```sh
ELECTRON_SKIP_BINARY_DOWNLOAD=1 npm ci   # electron is a devDependency for types only
npm run build       # dist/main.js, dist/preload.js, dist/renderer/{index.html,app.js,app.css}
npm test            # vitest, offline
npm run typecheck
npm run dev:web     # http://localhost:5174 with a mock harness (Ctrl+C to stop)
```

Production runs `electron dist/main.js` with nixpkgs Electron 41. `.npmrc`
sets `legacy-peer-deps=true` because npm 11.3 crashes resolving vitest's
optional peer graph; all required peers are listed explicitly.

`dev:web` serves the renderer to a normal browser. A dev shim stands in for
the preload: it fetches the URL and a random token from the dev server and
registers surfaces at `/__surface/<id>/`, served with the same CSP headers.
Try "show my files", "system status", "send the report to Sam", "schedule a
meeting", "I need a secret", "cancel test", "error", "long", `/surfaces`.

## The web demo (foyelle.org/demo)

The canvas in a visitor's browser with only static files: `scripts/deploy-site`
builds it into `out/site/demo/` (`node scripts/build.mjs --demo <dir>`).

- `demo/demo.ts` is loaded before `app.js` and stands in for the preload. It
  replaces `WebSocket` with one whose other end is the mock harness, run in
  the page in demo mode: only the scripted paths answer, the developer's test
  flows are off, and anything else gets a word about the demo.
- The helpers answer from `demo/recording.json` (`demo/recorded-tools.ts`).
  The settings (name, light or dark, theme) are kept in the page, so the
  welcome and the Themes card work; a call that wasn't recorded answers "not
  in this demo".
- Every card is a static file, `cards/<sha-256 of its page>`: the recorded
  cards (`demo/cards/`) and the built-in views. `registerSurface` returns the
  file for the page's hash and refuses any other page. `site/_headers` serves
  them with the cards' default policy (`buildSurfaceCsp`), the iframe stays
  sandboxed without `allow-same-origin`, and the page itself gets the canvas's
  policy. `test/demo.test.ts` checks both against `src/shared/csp.ts`.
- A banner says it is a demo, suggestions keep to the scripted paths, and
  below 760 px the conversation and the screen take turns.

`node demo/record.mjs` records again (after `node scripts/build.mjs --web`;
it needs `playwright-core` and Chromium, see the file): the real connectors on
fresh sample data, every card and path driven in Chromium, the sample data's
folder written as `/var/lib/foyelle`. Record again when a card's view or a
tool's answer changes. `node demo/test.mjs` drives the built site through the
README demo's steps in Chromium and WebKit at desktop and phone sizes (CI's
"Web demo" job); `node demo/serve.mjs` serves `out/site` with its `_headers`
at http://localhost:5180/demo/.

## Security posture

- One `BrowserWindow`: `kiosk`, `fullscreen`, `frame: false`, no menu, no
  tray, no deep links, no auto-update, single-instance lock.
- `contextIsolation`, `sandbox`, no Node integration (also in subframes and
  workers), no `<webview>`, DevTools only with `FOYELLE_CANVAS_DEV=1`.
- In-memory session partition; every permission request and check is
  denied, downloads are cancelled, `window.open` is denied.
- Navigation: the top frame may only show `foyelle-app://canvas/`; a surface
  frame may load its own `foyelle-surface://` URL and nothing else.
- Network: requests are cancelled unless they go to the app, a surface, the
  harness WebSocket origin, or https (which each surface's CSP narrows to its
  declared `connectDomains`/`resourceDomains`; only https origins are accepted).
- IPC handlers answer only the canvas top-level frame.
- Surface CSP default is the contract's `default-src 'none'; script-src
  'unsafe-inline'; style-src 'unsafe-inline'; img-src data:`, plus
  `base-uri 'none'; form-action 'none'`.
- Chat Markdown escapes all input before adding a fixed set of tags; links are
  shown as text.
- On `render-process-gone` the renderer is reloaded (with backoff if it keeps
  crashing); an unresponsive renderer is restarted after 10 s. A dead main
  process is systemd's job.
