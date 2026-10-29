# Foyelle architecture and contracts

This file is the contract between the parts of the repository. The canvas,
the harness patches, the connectors and the NixOS modules are built
separately and meet only at the interfaces below. Change an interface here
first, then in code.

The whole system:

```
cage (kiosk) ── canvas (Electron, MCP Apps host) ──ws──▶ harness (patched Hermes, policy plugin, audit log)
                                                              │ MCP over firewalled local ports
                                     shell · files · email · calendar · web · system (one isolated unit each)
                                                              │ commits to a branch
                                       config repo ──▶ apply helper (root) ──▶ switch, health check, rollback
```

The canvas, the harness and the connectors:

```
canvas (Electron, kiosk user)  ──ws://127.0.0.1:9119/api/ws?token=…──▶  harness (hermes serve, user foyelle-harness)
                                                                              │
                                     MCP streamable HTTP, http://127.0.0.1:73xx/mcp (nftables: harness uid only)
                                                                              ▼
                                   connectors (one systemd unit each, DynamicUser, egress proxy)
```

## 1. Canvas ↔ harness

- **Transport.** The harness runs `hermes serve --host 127.0.0.1 --port 9119
  --no-open --skip-build`. The canvas connects to
  `ws://127.0.0.1:9119/api/ws?token=<token>`. The token is read from
  `$FOYELLE_HARNESS_TOKEN_FILE` (default `/run/foyelle/harness-token`), a
  file readable by the `foyelle-canvas` group.
- **Dialect.** Hermes's legacy `tui_gateway` JSON-RPC 2.0, one frame per
  WebSocket text message.
  - Client request: `{"jsonrpc":"2.0","id":N,"method":M,"params":{…}}`.
  - Event: `{"jsonrpc":"2.0","method":"event","params":{"type":T,"session_id"?,"payload":P}}`.
  - The first event is `gateway.ready`. The client must then send
    `client.capabilities {"server_requests": true}`.
  - Server→client request: `{"jsonrpc":"2.0","id":"srq-…","method":"approval"|"clarify"|"secret"|"sudo","params":{…}}`.
    The client answers with `{"jsonrpc":"2.0","id":"srq-…","result":{…}}`.
    `approval` → `{"choice":"once"|"session"|"always"|"deny"}`.
    `clarify` → `{"answers":{qid: string|null}}`. `secret` → `{"value": string}`.
  - A withdrawn request arrives as event `request.cancel {id}`.
- **Methods the canvas uses (upstream).** `session.create {}` →
  `{session_id}`, `prompt.submit {session_id, text}`, `session.interrupt
  {session_id}`, `session.list`, `session.resume {session_id}`, `ping`.
- **Events the canvas renders (upstream).** `message.start`,
  `message.delta {text}`, `message.complete {text, status, error?}`,
  `tool.start {tool_id, name, args}`, `tool.complete {tool_id, name, summary,
  result_text}`, `status.update {kind, text}`, `error {message}`.

### Surface methods and events (added by the fork, patch `surfaces`)

- **Event `surface.open`.** Emitted after any MCP tool call whose tool
  declares `_meta.ui.resourceUri` (or the legacy `_meta["ui/resourceUri"]`).
  ```json
  {"surface_id": "s-…", "session_id": "…", "server": "files", "tool": "browse",
   "tool_call_id": "…", "uri": "ui://files/browser", "html": "<!doctype html>…",
   "csp": {"connectDomains": [], "resourceDomains": []},
   "tool_input": {…}, "tool_result": {"content": […], "structuredContent": {…}, "isError": false}}
  ```
- **RPC `surface.list {}`** returns
  `{"surfaces": [{"server", "uri", "name", "title", "pinned": bool}]}`. It
  lists every `ui://` resource the connected MCP servers expose, so the
  canvas can open system surfaces (status, memory, settings, audit) without a
  tool call.
- **RPC `surface.open_resource {server, uri, session_id?}`** returns the same
  payload as the `surface.open` event, without `tool_*` fields.
- **RPC `surface.call_tool {session_id?, surface_id, server, name, arguments}`**
  returns an MCP `CallToolResult` (`{content, structuredContent?, isError?}`).
  The call goes through the same policy plugin and approval flow as a call
  from the model. The audit log records it with `origin: "surface"`.
- **RPC `surface.read_resource {server, uri}`** returns `{contents: [...]}`.

### Canvas rules for surfaces

- A surface renders in an `<iframe sandbox="allow-scripts">` with no
  `allow-same-origin`, so it has an opaque origin. Its `src` is
  `foyelle-surface://<surface_id>/`, a custom protocol the canvas serves
  with a per-surface Content-Security-Policy built from `csp`. The default
  is `default-src 'none'; script-src 'unsafe-inline'; style-src
  'unsafe-inline'; img-src data:`.
- The canvas speaks MCP Apps (SEP-1865) to the iframe with
  `@modelcontextprotocol/ext-apps` `AppBridge` + `PostMessageTransport`.
  `oncalltool` → `surface.call_tool`, `onreadresource` →
  `surface.read_resource`, `onmessage` → `prompt.submit`, `onopenlink` → an
  approval card (no browser in the alpha, so links are shown, not opened).

## 2. Harness ↔ connectors

- Each connector is an MCP server speaking streamable HTTP at
  `http://127.0.0.1:<port>/mcp`.

  | Connector | Port | Sensitivity | Egress hosts |
  |---|---|---|---|
  | `shell` (status, memory, settings, audit surfaces) | 7300 | private | none |
  | `files` | 7301 | private | none |
  | `email` | 7302 | private | the configured IMAP and SMTP hosts |
  | `calendar` | 7303 | private | the configured CalDAV host |
  | `web` | 7304 | public | the configured search provider; fetch via the proxy allowlist |
  | `system` | 7305 | private | none (talks to the Nix daemon and the apply helper) |

- The harness registers them as `mcp_servers.<name>.url`, so the tools reach
  the model as `mcp__<name>__<tool>` (Hermes's MCP registry names).
- A tool that has a surface sets `_meta.ui.resourceUri = "ui://<connector>/<view>"`.
  The resource has MIME type `text/html;profile=mcp-app` and is a single
  self-contained HTML file. Surface HTML uses the MCP Apps `App` API from an
  inlined script (no network).
- Tools set the standard annotations (`readOnlyHint`, `destructiveHint`,
  `openWorldHint`). The connectors' tiers are **not** trusted from the
  connector itself: the policy lives in `/etc/foyelle/policy.json`, generated
  by the NixOS module (§3).
- Configuration reaches a connector through environment variables and
  credentials through `$CREDENTIALS_DIRECTORY/<name>` (systemd
  `LoadCredential=`). Outbound HTTP goes through `$HTTPS_PROXY`/`$HTTP_PROXY`.
  IMAP and SMTP connect directly to the hosts the connector unit's
  `IPAddressAllow=` permits (resolved at unit start).
- **Bridges** (`nixos/modules/bridges.nix`, `connectors/…/bridge`): a service's
  own MCP server (an entry of Hermes's `optional-mcps/` catalogue) is reached
  only through a local bridge connector on ports 7320–7329, egress proxy
  `127.0.1.20`–`29`. The bridge passes through only the tools the module names
  (renamed `search-flight` → `search_flight`), every one marked untrusted, and
  hides the rest. A service with an account signs in from the Accounts card
  (MCP OAuth: discovery, dynamic registration, PKCE, the resource parameter)
  in the sign-in browser, on the sign-in page pinned in the module; the bridge
  keeps the tokens in its own state directory and tells the harness to re-read
  its tools (`tools/list_changed`). Account tools are surface-only, and the
  one-time code is a secret argument. The bridge keeps one MCP session per
  service open (closed after fifteen idle minutes), and keeps the service's tool
  list in its state directory so the harness's listing at start is answered
  at once; a service's exact JSON copy of its structured result is dropped so
  cards get the data.

## 3. Policy file (`/etc/foyelle/policy.json`)

```json
{
  "local_model_base_urls": ["http://10.0.2.2:11434/v1"],
  "connectors": {
    "email": {
      "sensitivity": "private",
      "tools": {
        "search":     {"tier": "read", "untrusted": true},
        "read":       {"tier": "read", "untrusted": true},
        "draft":      {"tier": "reversible", "egress": true},
        "send":       {"tier": "outbound"}
      }
    }
  },
  "default_tier": "outbound"
}
```

- **Tiers.** `read` → allow, logged. `reversible` → allow, logged.
  `outbound` (irreversible or outbound) → ask every time. `system` → ask,
  and the System surface shows the review.
- **Untrusted content.** After a tool marked `untrusted` returns in a
  session, every tool marked `egress` or tier `outbound` in that session is
  asked about, whatever its tier. A card's own call (origin `surface`, the
  person clicking in Foyelle's card) is not asked about taint; a card the
  agent made (origin `workshop`) is, like the model.
- **Private routing.** A call to a tool of a `private` connector is allowed
  without asking only while the session's model endpoint is one of
  `local_model_base_urls`. Otherwise it is asked about once per session (the
  approval's `session` choice).
- **Unknown tools** (not in the file) get `default_tier`.
- **Built-in Hermes tools** are not connectors. The harness disables every
  built-in toolset that reaches files, network or processes (terminal, file,
  web, browser, code execution, delegation); memory, skills, todo and
  clarify stay.

## 4. Audit log

- `/var/lib/foyelle/audit/audit.jsonl`, append-only (`chattr +a` where the
  filesystem supports it). One JSON object per line:
  `{"ts", "session_id", "tool", "connector", "args_digest", "decision":
  "allow"|"ask"|"deny", "approval": "once"|"session"|"always"|"deny"|null,
  "status", "duration_ms", "origin": "model"|"surface", "tainted": bool}`.
- The `shell` connector serves it read-only as the Audit surface.

## 5. System changes (M4)

- **Config repo:** `/var/lib/foyelle/config` (a git flake). The `system`
  connector owns it.
- **Propose:** commit to branch `proposal/<id>`.
- **Build:** `nix build <repo>#nixosConfigurations.<host>.config.system.build.toplevel`
  without switching.
- **Review:** `nvd diff /run/current-system <result>`.
- **Apply:** the connector writes the approved commit hash to the apply
  helper's socket `/run/foyelle/apply.sock`. The helper (root, separate unit)
  accepts only `{"commit": "<40 hex>", "approval_token": "…"}` for a commit
  that `system` built and the user approved. The approval token is created
  by the policy plugin when the user approves the `system` tier call.
- **Health check, then rollback:** `systemctl is-active foyelle-harness
  foyelle-canvas` within 60 s, else `switch-to-configuration` of the previous
  generation.
- **Boot-level changes** (kernel, initrd, bootloader in the closure diff):
  `switch-to-configuration boot` plus systemd-boot boot counting.

## 6. Local model

- **Interface.** The harness knows the local model only as an OpenAI-compatible
  endpoint: `foyelle.models.local.baseUrl` (`/v1`), `.host` and `.model`
  (`nixos/modules/harness.nix`). The same URL becomes the policy's
  `local_model_base_urls` (§3), and the harness unit's `IPAddressAllow=`
  admits `.host`. Moving the model changes these options, nothing else.
- **In the VM (today).** The model server runs on the Mac, not in the VM: a
  QEMU guest has no access to Apple's GPU (Metal), so a model inside it would
  run on the CPU only. The harness reaches Ollama at the UTM shared-network
  gateway, `http://192.168.64.1:11434/v1` (`10.0.2.2` in the NixOS VM tests).
  Prompts and private content leave the VM but stay on the Mac.
- **On bare metal (not built).** The model server runs in the OS:
  - A module `nixos/modules/model.nix`, built like `voice.nix`: llama.cpp's
    `llama-server` (OpenAI-compatible, tool calls), one systemd unit with
    `DynamicUser`, listening on a loopback address only, `IPAddressDeny=any`,
    and `DeviceAllow=` only for the GPU.
  - The weights (GGUF) are part of the image, pinned by SHA-256 like the
    speech model, so nothing is downloaded at runtime and no outbound host is
    added. A 9B model adds about 5–7 GB to the image. Ollama is not used
    because it expects to pull from its online registry.
  - A host `nixos/hosts/metal.nix` enables the module and points `baseUrl`
    and `host` at that loopback address; nftables lets only the harness's uid
    connect to it, as for connectors (§2). `alpha-vm.nix` keeps the Mac.
  - The GPU backend depends on the hardware: Vulkan on x86 (AMD, Intel, NVIDIA
    with open drivers) is the portable default; CUDA is faster on NVIDIA but
    needs the unfree driver and a larger image; CPU only is the fallback, for
    small models. Linux on Apple Silicon (Asahi) has a Vulkan driver, not
    Metal, and does not run on M3 or M4 machines yet.
  - The model shares memory with the OS, the canvas and the connectors: about
    a 4B model on 16 GB, `qwen3.5:9b` on 32 GB.

## 7. Nous Research services

- **Today: off in three layers.**
  - Code: patches 0001–0004, 0006 and 0007 turn the Nous paths off whatever
    the config says: the guest identity and free-tier trial, hosted
    connectors and the managed tool gateway, the remote model, plugin and
    skills catalogues, shared metrics, and the update and asset origins.
  - Config: the harness module enforces `nous.guest = false`,
    `tools.connectors.enabled = false`, `model_catalog.enabled = false`,
    telemetry off and update checks off (`nixos/modules/harness.nix`).
  - Network: the harness reaches only the hosts on its egress allowlist (§6
    for the model). In CI, `scripts/egress-guard` fails on a Nous URL in the
    patched code, and the egress VM test fails on any unexpected packet.
- **An opt-in switch (not built).** One NixOS option, `foyelle.nous.enable`,
  default `false`. With it on, the harness behaves towards Nous as standard
  Hermes does:
  - The patches change from "always off" to "off unless `FOYELLE_NOUS=1`",
    which the harness unit sets only when the option is on. One build serves
    both modes; with the variable set, upstream's own code paths run.
  - The enforced config keys above flip for the guest identity, hosted
    connectors, the managed gateway and the model catalogue.
  - The Nous hosts those paths use (portal, inference, the tool gateways, the
    catalogue host) join the harness's egress allowlist.
  - Still off: telemetry keeps its own consent, as upstream; Hermes's
    self-update stays off (NixOS updates the system); borrowed Codex and
    Claude Code logins (patch 0005) stay off, as they are not a Nous path.
  - The policy is unchanged: tools reached through hosted connectors or the
    gateway are unreviewed, so they get `default_tier` and ask; Nous
    inference is a remote model, so private content asks once per session
    (§3).
- **Turning it on.** The option lives in the machine's config repo, so the
  Settings card's toggle ("Use Nous Research services, as in standard
  Hermes") is a system change: reviewed, applied by the apply helper, and
  rolled back like any other (§5). For development, `scripts/deploy-vm`
  takes `FOYELLE_DEV_NOUS=1`, like `FOYELLE_DEV_BRIDGES`.
- **CI.** The egress guard allows Nous URLs only inside code gated by
  `FOYELLE_NOUS`, and the egress VM test runs twice: with the option off (no
  packet to a Nous host) and on (only the allowlisted Nous hosts).
