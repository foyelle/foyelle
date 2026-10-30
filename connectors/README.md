# Foyelle connectors (the helpers)

Foyelle's helpers: first-party MCP servers that run on the machine (the code
calls them connectors; see the [glossary](../docs/architecture.md#glossary)).
Each one is a separate process (one systemd unit each) speaking MCP over
streamable HTTP at `http://127.0.0.1:<port>/mcp`, and each ships its own cards:
MCP Apps (SEP-1865) views as `ui://<connector>/<view>` resources
(`text/html;profile=mcp-app`, one self-contained HTML file, no network), each
with a title and a one-line description that the canvas's card gallery shows.
The bridge (`bridge/`) is the helper that reaches one online service. The contract is
[`docs/architecture.md`](../docs/architecture.md), sections 2, 3 and 5.

| Helper | Port | Tools (card in brackets) | Cards |
|---|---|---|---|
| `shell` | 7300 | `status` [status], `memory_read` [memory], `memory_write`, `memory_export`, `open_accounts`, `open_cards`, `user_name_set`, `settings_get` [settings], `settings_set`, `theme_list` [themes], `theme_get`, `theme_use`, `theme_create`, `theme_delete`, `audit_query` [audit], `privacy_today` [privacy] | `ui://shell/status`, `ui://shell/memory`, `ui://shell/settings`, `ui://shell/themes`, `ui://shell/audit`, `ui://shell/privacy` |
| `files` | 7301 | `browse` [browser], `list`, `search`, `read`, `write`, `move`, `trash`, `undo` | `ui://files/browser` |
| `email` | 7302 | `search` [inbox], `read`, `draft` [compose], `send`, `delete_draft`, `account_status`, `account_providers`, `account_connect`, `account_disconnect` | `ui://email/inbox`, `ui://email/compose` |
| `calendar` | 7303 | `list_events` [agenda], `get_event`, `create_event`, `update_event`, `respond_invite`, `delete_event`, `account_status`, `account_providers`, `account_connect`, `account_disconnect` | `ui://calendar/agenda` |
| `web` | 7304 | `search` [results], `fetch` | `ui://web/results` |
| `system` | 7305 | `status` [review], `propose`, `build`, `diff` [review], `apply`, `reject`, `rollback` | `ui://system/review` |

Tool annotations follow the MCP definitions: `readOnlyHint` for tools that
change nothing, `destructiveHint` for tools that replace or remove existing
data (even when a backup is kept), `openWorldHint` for tools that talk to a
host outside the machine. Foyelle's tiers (read, reversible, outbound,
system) come from `/etc/foyelle/policy.json`, not from these hints.

## Configuration

Every connector: `FOYELLE_CONNECTOR_PORT` (default: table above),
`FOYELLE_CONNECTOR_HOST` (default `127.0.0.1`), `FOYELLE_LOG_LEVEL`
(default `INFO`; logs are JSON lines on stderr). Secrets are read from
`$CREDENTIALS_DIRECTORY/<name>` (systemd `LoadCredential=`). Outbound HTTP
uses `HTTPS_PROXY`/`HTTP_PROXY`.

| Connector | Environment | Credentials |
|---|---|---|
| `shell` | `FOYELLE_MEMORY_DIR` (`/var/lib/foyelle/memory`, holds `MEMORY.md`, `USER.md`), `FOYELLE_SETTINGS_FILE` (`/etc/foyelle/settings.json`), `FOYELLE_SETTINGS_OVERRIDES` (`/var/lib/foyelle/settings-overrides.json`), `FOYELLE_AUDIT_LOG` (`/var/lib/foyelle/audit/audit.jsonl`), `FOYELLE_CONNECTORS` (`shell,files,…` or `name=port,…`), `FOYELLE_HEALTH_METHOD` (`systemd`, `tcp` or `none`), `FOYELLE_CONNECTOR_UNIT` (`foyelle-connector-{name}.service`), `FOYELLE_OUTBOUND_TOOLS` (`connector.tool,…` that send data off the machine; defaults to the mail, calendar and web ones) | none |
| `files` | `FOYELLE_FILES_ROOTS` (see below), `FOYELLE_FILES_MAX_READ` (1 MiB), `FOYELLE_FILES_MAX_WRITE` (5 MiB) | none |
| `email` | Optional: without `FOYELLE_EMAIL_ADDRESS` the account comes from the Accounts card and is kept in `FOYELLE_ACCOUNT_DIR` (default `$HOME/account`). With it: `FOYELLE_EMAIL_ADDRESS`, `FOYELLE_IMAP_HOST`, `FOYELLE_IMAP_PORT` (993), `FOYELLE_IMAP_SECURITY` (`ssl`/`starttls`), `FOYELLE_SMTP_HOST`, `FOYELLE_SMTP_PORT` (465), `FOYELLE_SMTP_SECURITY`, `FOYELLE_EMAIL_USERNAME` (address), `FOYELLE_EMAIL_NAME`, `FOYELLE_EMAIL_DRAFTS_FOLDER` (`Drafts`), `FOYELLE_EMAIL_SENT_FOLDER` (`Sent`; `none` to skip saving a copy), `FOYELLE_EMAIL_MAX_FETCH` (20 MiB) | `email-password` |
| `calendar` | Optional: without `FOYELLE_CALDAV_URL` the account comes from the Accounts card (`FOYELLE_ACCOUNT_DIR`, default `$HOME/account`). With it: `FOYELLE_CALDAV_URL`, `FOYELLE_CALDAV_USER`, `FOYELLE_CALDAV_CALENDAR` (display name; default the first calendar), `FOYELLE_CALENDAR_EMAIL` (the user's attendee address; default the user name if it has an `@`), `FOYELLE_TIMEZONE` (`UTC`) | `caldav-password` |
| `web` | `FOYELLE_SEARCH_PROVIDER` (`searxng` or `brave`), `FOYELLE_SEARCH_URL` (SearXNG base URL), `FOYELLE_WEB_MAX_BYTES` (2 MiB) | `search-api-key` (Brave) |
| `system` | `FOYELLE_CONFIG_REPO` (`/var/lib/foyelle/config`), `FOYELLE_HOST` (nixosConfiguration name, required), `FOYELLE_APPLY_SOCKET` (`/run/foyelle/apply.sock`), `FOYELLE_CONFIG_BRANCH` (`main`), `FOYELLE_CURRENT_SYSTEM` (`/run/current-system`), `FOYELLE_PROFILES_DIR` (`/nix/var/nix/profiles`), `FOYELLE_NIX_BUILD_COMMAND`, `FOYELLE_NVD_COMMAND` (JSON arrays, see below), `FOYELLE_FLAKE_TEMPLATE`, `FOYELLE_GIT`, `FOYELLE_BUILD_TIMEOUT` (3600 s), `FOYELLE_BUILD_WAIT` (100 s), `FOYELLE_APPLY_TIMEOUT` (600 s) | none |

**Files roots.** `FOYELLE_FILES_ROOTS` is a comma-separated list of
`name=/absolute/path`, each optionally suffixed `:ro` (read-only) or `:rw`:
`documents=/home/u/Documents,photos=/home/u/Pictures:ro`. Tools address
files as `<name>/<relative path>`. Symlinks are resolved and anything that
leaves its root is refused. Nothing is deleted: `trash` and overwrites move
the old content to `<root>/.foyelle-trash/<operation>/`, recorded in
`<root>/.foyelle-trash/journal.jsonl`, and `undo` reverts the latest change.

**System commands.** The build command defaults to
`["nix","build","--no-link","--print-out-paths","{flake}"]` with
`{flake}` = `git+file://{repo}?ref={ref}&rev={rev}#nixosConfigurations.{host}.config.system.build.toplevel`
(so the exact proposal commit is built); placeholders `{repo}`, `{ref}`,
`{rev}`, `{host}`, `{flake}`. nvd defaults to
`["nvd","diff","{current}","{out}"]`. All commands run without a shell,
with timeouts.

## Run one locally

```sh
cd connectors
uv sync                      # Python >= 3.13; production is nixpkgs python3 (3.14)
FOYELLE_FILES_ROOTS="docs=$HOME/Documents:ro" uv run foyelle-connector-files
# MCP endpoint: http://127.0.0.1:7301/mcp
```

Then point any MCP client (or the harness, `mcp_servers.files.url`) at the
URL. For the shell connector during development, `FOYELLE_HEALTH_METHOD=tcp`
probes the other connectors' ports instead of asking systemd.

## Tests

```sh
uv run pytest -q
```

Fully offline: IMAP/SMTP, CalDAV and HTTP are replaced by fakes, the system
connector uses a temporary git repository with fake `nix` and `nvd`
commands and a fake apply-helper socket. Each connector also has an
end-to-end test that serves it on an ephemeral port and drives it with the
MCP streamable-HTTP client. If `node` is installed, the view scripts are
syntax-checked and the view runtime's postMessage protocol is exercised.
