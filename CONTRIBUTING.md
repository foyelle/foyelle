# Contributing to Foyelle

Thanks for helping. Everyone here follows the [code of conduct](CODE_OF_CONDUCT.md); report security
problems privately, as [SECURITY.md](SECURITY.md) says. Questions and ideas go to Discussions, bugs
to Issues.

Two rules keep the project user-owned:

1. **Sign off every commit (DCO).** By adding a `Signed-off-by:` line
   (`git commit -s`) you certify the [Developer Certificate of
   Origin](https://developercertificate.org/): you wrote the change or have
   the right to submit it under the project's licence (MIT).
2. **No new outbound hosts.** CI runs the egress guard
   (`scripts/egress-guard`). A change that adds a Nous Research host, or any
   host that is not on an allowlist, fails the build.

On a pull request, CI runs the unit tests, checks the harness patches and runs the egress guard. The OS
image and the VM tests (about 25 minutes) run once it is ready to merge, in the merge queue, and on
main; run them yourself before then with `nix flake check` (docs/running.md).

## Layout

| Path | What it is |
|---|---|
| `harness/patches/` | The Hermes fork as a patch series against the commit in `harness/upstream.json` |
| `harness/plugin/` | Policy engine and audit log (a Hermes `pre_tool_call`/`post_tool_call` plugin) |
| `canvas/` | The full-screen Electron canvas and its MCP Apps host |
| `connectors/` | Foyelle's helpers (MCP servers on the machine) with their cards: shell, files, email, calendar, web, system, workshop, and the bridge to online services |
| `system/apply-helper/` | The only privileged step of an agent-driven system change |
| `nixos/` | Flake modules, the alpha VM host, the on-device config template, VM tests |
| `scripts/` | `harness-patches`, `egress-guard`, `nix-docker`, `utm-create`, `deploy-vm`, `host-proxy` |
| `docs/` | The architecture contract, how to run Foyelle, and how the main features work |
| `site/` | foyelle.org: the landing page and the OAuth client document, deployed with `scripts/deploy-site` |

To build the image and run it in a VM, see [docs/running.md](docs/running.md).

## Working on the canvas and cards

The words: a **helper** is an MCP server on the machine (a *connector* in the code, under
`connectors/`); a **service** is an online MCP server, reached only through a local **bridge**
helper; a **card** is what a helper or service shows (an MCP App view, a *surface* in the code).
The [glossary](docs/architecture.md#glossary) has the rest. Use these words in anything people
read (the canvas, cards' titles and descriptions, the docs); identifiers keep their names.

For the look and feel there is no need for the VM. `cd canvas && npm run dev` opens the real
canvas in an Electron window on your laptop, against a mock harness with canned conversations
and approvals, backed by the real helpers (Files, Shell, System, Workshop, mail and calendar, a
Todoist bridge) on sample data in
`canvas/.dev-state/` (`npm run dev -- --reset` starts it fresh). Edits to `canvas/src` reload the
window in about a second; edits to `canvas/dev` or `connectors/` restart the mock or the tools
host. No model and no network are involved. Needs Node 22+ and `uvx`. Type `/cards` to see every
card; each one's title and one-line description come from its helper (`sdk.register_page(...,
title=, description=)`), so a new card says what it shows there.

The README's demo (`docs/media/foyelle-demo.gif`) is recorded from that same setup by
`canvas/dev/record-demo.mjs`; its header says how to run it again after the canvas changes.

## Working on the harness patches

```bash
scripts/harness-patches checkout   # upstream at the pinned commit + patches, in .upstream/hermes-agent
# edit .upstream/hermes-agent, commit there with git commit -s
scripts/harness-patches export     # rewrite harness/patches/ from those commits
```

The weekly upstream merge is `scripts/harness-patches bump <rev>`. It
rebases the patch series onto the new upstream commit and stops on
conflicts.

## Tests

```bash
(cd connectors && uv run pytest)
(cd harness/plugin && uv run pytest)
(cd canvas && npm test)
nix flake check            # or scripts/nix-docker flake check
```
