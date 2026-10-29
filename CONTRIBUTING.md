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

## Layout

| Path | What it is |
|---|---|
| `harness/patches/` | The Hermes fork as a patch series against the commit in `harness/upstream.json` |
| `harness/plugin/` | Policy engine and audit log (a Hermes `pre_tool_call`/`post_tool_call` plugin) |
| `canvas/` | The full-screen Electron canvas and its MCP Apps host |
| `connectors/` | First-party MCP connectors with their surfaces: shell, files, email, calendar, web, system |
| `system/apply-helper/` | The only privileged step of an agent-driven system change |
| `nixos/` | Flake modules, the alpha VM host, the on-device config template, VM tests |
| `scripts/` | `harness-patches`, `egress-guard`, `nix-docker`, `utm-create`, `deploy-vm`, `host-proxy` |
| `docs/` | The architecture contract, how to run Foyelle, and how the main features work |
| `site/` | foyelle.org: the landing page and the OAuth client document, deployed with `scripts/deploy-site` |

To build the image and run it in a VM, see [docs/running.md](docs/running.md).

## Working on the canvas and surfaces

For the look and feel there is no need for the VM. `cd canvas && npm run dev` opens the real
canvas in an Electron window on your laptop, against a mock harness with canned conversations
and approvals, backed by the real connectors (Files, Shell, System, Workshop) on sample data in
`canvas/.dev-state/` (`npm run dev -- --reset` starts it fresh). Edits to `canvas/src` reload the
window in about a second; edits to `canvas/dev` or `connectors/` restart the mock or the tools
host. No model and no network are involved. Needs Node 22+ and `uvx`.

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
