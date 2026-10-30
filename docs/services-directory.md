# Services directory

Words as in the [glossary](architecture.md#glossary): a **service** is an online MCP server, a
**helper** an MCP server on this computer, a **bridge** the helper that reaches one service, and a
**card** what a helper shows on the canvas.

Status: built and checked on the dev VM (October 2026): connecting and disconnecting a service
at runtime, a real conversation loading a service with `use_service`, and the sign-in filter. Where the build differs from the plan, the section says so under
"As built".

Claude, ChatGPT/Codex and Grok let people add a service in two taps: pick it from a directory,
sign in, done. They reach the same kind of remote MCP servers Foyelle bridges (Todoist, Notion,
Kiwi…), but from their own cloud, where the tokens live and where the model sees everything the
service returns. Foyelle keeps the connection on the machine: each service (an online MCP server)
runs behind a local bridge helper (an MCP server on the machine) with its own sandbox and egress
proxy, the token stays on the machine, and every tool
has a Foyelle tier. That model stays. What changes is how a service gets there.

Today a service is a hand-written Nix entry in `nixos/modules/bridges.nix` (URL, hosts, sign-in
page, a tier for every tool), and turning one on means rebuilding the system. That suits a few
curated services and a developer; it doesn't let a person connect Notion on their own.

The plan, in five parts:

1. **Directory entries are data**, reviewed in git and shipped read-only with Foyelle.
2. **A runtime bridge host** starts and stops a service's helper when the person connects or
   disconnects it, without a rebuild, and keeps today's per-service sandbox and proxy.
3. **Tiers from tool hints** cover tools the review didn't list.
4. **Services per conversation**: only the services a conversation needs are offered to the
   model.
5. **Add by URL is a system change**: a service outside the directory goes through Foyelle's
   reviewed proposal loop, because it opens a new outside host.

## 1. Directory entries as data

One JSON file per service under `services/directory/` (the Hermes catalogue, `optional-mcps/`, is
where most start). Kiwi and Todoist move there from `bridges.nix`:

```json
{
  "id": "todoist",
  "title": "Todoist",
  "category": "tasks",
  "summary": "Your tasks and reminders, in step with the Todoist apps.",
  "url": "https://ai.todoist.net/mcp",
  "hosts": ["ai.todoist.net", "todoist.com"],
  "auth": {
    "kind": "oauth",
    "signin_page": "https://todoist.com/oauth/authorize",
    "signin_hosts": ["todoist.com", "app.todoist.com", "todoist.b-cdn.net"]
  },
  "sensitivity": "private",
  "tools": {
    "find-tasks": { "tier": "read" },
    "add-tasks": { "tier": "reversible" },
    "delete-object": { "tier": "outbound" },
    "feedback-to-devs": { "hidden": true }
  },
  "views": { "find-tasks": "tasks" },
  "reviewed": "2026-10-18",
  "notes": "Deleting asks every time. Sign-in in the sign-in browser."
}
```

- **One schema, checked in CI**: https URL; `hosts` covers the URL's host; the sign-in page sits on a
  sign-in host; tiers from Foyelle's set (`read`, `reversible`, `outbound`, `system`); `hidden`
  tools never reach the model. A Nix check fails the build on a bad entry.
- **Shipped in the Nix store**, so no process on the machine can edit an entry. The policy plugin,
  the bridge host and the sign-in browser all read the same files. A directory update arrives with
  a Foyelle update, reviewed like code.
- **What review means**: who runs the server, what each tool does, which are hidden, the
  sensitivity (does it return the person's own data?), and the hosts. That's the same review
  `bridges.nix` gets today, as data.

As built: `connectors/src/foyelle_connectors/services/directory/*.json`, checked by
`python -m foyelle_connectors.services.directory` (its tests run when the connectors package
builds). Slots 0–19: port 7320 + slot, egress proxy 127.0.1.<20 + slot>.

## 2. A runtime bridge host

Starting a service has to stay as locked down as now: its own sandboxed helper, an egress proxy that
reaches only the entry's hosts, the token in that helper's private state. What changes is when
it's created: at connect time, not at build time.

- **Template units, built once**: `foyelle-service@<id>.service` runs the existing bridge
  (`connectors/…/bridge`) for the entry `<id>`, with the same hardening as other helpers
  (`IPAddressDeny=any`, loopback only, its own user). `foyelle-service-egress@<id>.service` runs
  tinyproxy with a default-deny filter generated from the entry's `hosts`. Addresses come from the
  entry's index (`127.0.1.<index>`), ports from a reserved range.
- **Enabling is a setting, not a rebuild**: the Services card writes the person's choice to
  `/var/lib/foyelle/services/enabled.json` (through the shell helper, a card-only tool). A
  root `systemd.path` unit watches that file and runs a small sync oneshot. That's the pattern
  `foyelle-canvas-settings` already uses. The sync starts and stops template instances only for
  ids that exist in the shipped directory; anything else in the file is ignored.
- **One door for the harness**: a "services" gateway helper is always configured in the harness.
  It forwards to the running instances over loopback and announces `tools/list_changed` when one
  starts or stops, so the harness's MCP configuration never changes at runtime. Tools appear as
  `<service>__<tool>`. The policy plugin maps that prefix back to the service, so tiers,
  sensitivity and taint apply per service, as with today's bridges.
- **Sign-in**: the sign-in browser's egress allowlist comes from the enabled services' `signin_hosts`
  (a runtime file the sync writes), not every entry in the directory.
- **The Services card** grows from today's Accounts card: what's connected, a browsable
  directory by category, Connect (sign in if needed), Disconnect (stops the instance and deletes its
  token). Mail and calendar accounts stay where they are.

As built, simpler than planned:

- Instead of template units, every directory service's bridge is an ordinary Foyelle helper (a
  connector unit), built at build time but not started at boot (`autoStart = false` in connectors.nix). Same hardening,
  same proxy, nothing new to audit.
- `foyelle-services-sync` (bridges.nix) watches the shell helper's `services.json` and starts or
  stops `foyelle-connector-<id>` and `foyelle-egress-<id>` for directory ids only. It also runs at
  boot, before the harness, waiting for each connected service's port.
- No services gateway: the harness lists every directory service from the start (a stopped one
  just doesn't connect), and the Accounts card asks it to `reload.mcp` after a connect or
  disconnect. Each service stays its own MCP server with its own policy.
- The sign-in browser's egress proxy reads its host filter from `/run/foyelle/signin.filter`,
  which the sync writes: Google's hosts and the sign-in hosts of the connected (and always-on)
  services, restarting the proxy when the list changes. Firefox's own allowed pages name every
  directory service's sign-in page, because its policies are fixed when it is built; the proxy is
  what keeps a service that isn't connected closed.
- Tools whose results hold nothing about the person (`use_service`, the theme tools) are marked
  `public` in the policy, so a private helper doesn't ask to share "settings and memories"
  for them.

Alternatives considered:

- **A system change for every connect** (a Nix entry, then a rebuild): safest on paper, but it
  takes minutes, needs free disk, and turns "connect Todoist" into a technical review. No.
- **One shared process and one proxy for every service**: simpler, but one service could reach
  another's hosts or tokens. No.
- **Connecting from a cloud**, as the big assistants do: against Foyelle's whole point. No.

## 3. Tiers from tool hints

MCP tools can carry annotations, and Claude's connector directory requires them: every tool
declares `readOnlyHint` or `destructiveHint` (and `openWorldHint` when it reaches outside). Foyelle
uses them where its review is silent: a tool a service adds after review, and services added by URL.

| Hint | Tier when not reviewed | Why |
| --- | --- | --- |
| `destructiveHint: true` | `outbound`: asks every time | It deletes or overwrites something. |
| `readOnlyHint: true` | `read`, with a once-per-conversation "Use <service> to look things up?" | The hint is the server's own claim; the first use is the person's check. |
| neither | `reversible`, asked once per conversation | Unknown effects: the person decides. |

- Every tool of a remote service is `egress` (what's asked goes to the service) and `untrusted`
  (what it returns taints the session). Hints never change that.
- A hint can make a reviewed tier stricter, never looser: a reviewed `read` tool that starts
  claiming `destructiveHint` asks every time until the entry is reviewed again.
- **Hidden stays hidden**: a reviewed entry may list tools as hidden (Kiwi's `feedback-to-devs`).
  New tools on a reviewed service appear only after the next review, unless the entry says
  `"new_tools": "by_hint"`.

As built: harness patch 0013 keeps each MCP tool's boolean hints; the policy plugin
(`decide(..., hints=)`) applies the table above to services marked `by_hint`, and the approval
card has words for the "unreviewed" kind.

## 4. Services per conversation

Every tool's description goes to the model on every turn. With mail, calendar, files, web, shell
and five services, that's a lot of text, and it's part of why the model feels slow. Claude lets a
person switch connectors (Claude's word for services) per conversation; Foyelle should, and should
do most of it for them.

- **A short default**: Foyelle's own helpers plus the services the person used recently or
  mentioned. The others are listed by name only, in one line of the system prompt ("also
  connected: Notion, Strava").
- **Chips above the message box** show what this conversation uses, each one a toggle; adding one
  is one tap or "use Notion".
- **Loading on demand**: a small built-in tool, `use_service(name)`, adds a connected service's
  tools to the conversation when the model needs them, so "what's in my Notion?" works without a
  tap.
- **Harness side**: a gateway method sets a session's enabled servers, and the agent's tool list
  is filtered by it (a patch, like `voice.*`). The policy also refuses calls to a server not
  enabled for the session, so a stale tool name can't slip through.

As built, with no harness patch: the policy plugin registers an `llm_request` middleware (a
Hermes hook that may rewrite each model request) that removes the tools of services this
conversation doesn't use and adds the one-line note (`harness/plugin/foyelle_policy/conversation.py`).
A service is on when its chip is pressed, when the person names it, or after `shell.use_service`;
the next request in the same turn already has its tools. The state is one JSON file per
conversation, keyed by the harness's stored session id (the canvas learns it from
`session.info`), written by the plugin and by the shell helper for the chips.

## 5. Add by URL as a system change

A person (or the agent, when asked) can add a service that isn't in the directory by its MCP URL.
That opens a new outside host, so it goes through the system-change loop instead of the Services
card:

1. The system helper proposes an entry: the URL, the hosts it needs (the URL's host, plus sign-in
   hosts found by MCP OAuth discovery), and `"reviewed": null`.
2. The approval card says what this means in plain words: "Foyelle will be able to reach
   mcp.example.com. What you ask this service goes there; what it returns is treated as untrusted."
3. Once approved, the entry joins a machine-local directory (the machine's config repo, so it
   survives updates and can be rolled back) and is then connected like any directory entry.
4. Its tools get tiers from their hints (part 3), and the Services card marks it "added by you,
   not reviewed".

As built: `system.propose_service` writes the entry into the config repo's `services.json`, which
belongs to the person like `configuration.nix`; the release-owned flake imports it when present.
These services use their own slots (port 7340 + slot, proxy 127.0.1.<110 + slot>), clear of the
directory's and of helpers written for the machine (7400+, .40+). The proposal can't probe the
service (the system helper has no network), so the agent finds the hosts and sign-in page in the
service's documentation, and the person sees them in the proposal.

## Order of work

1. Entries as data: move Kiwi and Todoist to `services/directory/`, generate today's Nix units
   from them, add the schema check. No change for the person.
2. Template units, the sync oneshot and the Services card: connecting without a rebuild.
3. The services gateway and the policy plugin's per-service mapping.
4. Tiers from hints, with the once-per-conversation question.
5. Services per conversation: chips, the short default, `use_service`.
6. Add by URL through the system loop.
7. A published Foyelle OAuth client document (Client ID Metadata Document), so services that
   support it need no per-machine registration. Dynamic registration stays the fallback. Built:
   set `foyelle.oauthClientMetadataUrl` to the https address where
   `docs/oauth-client-metadata.json` (with that address as its `client_id`) is served. Nothing
   is published yet.

## Deferred (October 2026)

- **Publishing the OAuth client document.** Support is built (step 7); publishing waits for a
  public https address to choose. Until then every service uses dynamic registration, as Todoist
  does today.
- **A full sign-in through the new sign-in proxy.** Checked up to the sign-in page: the sign-in
  Firefox loads Todoist's page through the runtime filter, and hosts that aren't connected are
  refused. Typing the password is the person's step, next time Todoist is reconnected.

## Open questions

- **Who reviews the directory**, and how often entries are re-checked when a service adds or changes
  tools.
- **The first directory**: from Hermes's catalogue, the everyday ones (Todoist, Notion, Dropbox,
  Kiwi, trivago, Strava, AllTrails, Wolfram). Which of them have tool annotations already?
- **Cards**: services return data; their bridge shows Foyelle's cards for it, which the card
  gallery lists under "Online services" with the service's name. Should a service's own MCP App be
  shown when Foyelle has no card for it (sandboxed as now, with its CSP), or only Foyelle's?
- **Tokens on disconnect**: delete them, or keep them so reconnecting skips sign-in?
