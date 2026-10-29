# Security

Foyelle is at an early stage and still experimental. It hasn't had a security review, so don't
use it with data or accounts you depend on.

## Reporting a vulnerability

Report it privately through GitHub:
[open a private security advisory](https://github.com/foyelle/foyelle/security/advisories/new).
Please don't open a public issue for it. Include what you found, how to reproduce it, and what an
attacker could do with it. We'll answer in the advisory, agree on a fix, and credit you when it's
published, unless you'd rather not be named.

Only the `main` branch is supported; there are no releases yet.

## What counts

Anything that breaks one of Foyelle's promises, for example:

- a tool call that runs without going through the policy plugin, or without the approval its tier
  requires (`docs/architecture.md`, §3);
- private content reaching an online model without the person's say;
- a connector reaching another connector's data, a host outside its egress allowlist, or the
  harness's own state;
- a card (an MCP Apps surface) escaping its sandbox or calling tools it shouldn't;
- a system change applied without an approval token, or not rolled back after a failed health
  check;
- a connection to a Nous Research host while Nous services are off.

## Known, by design

- The disk image's initial disk passphrase is `foyelle`, which is public. Change it on first boot
  with "Change disk passphrase…" on the System card.
- Release images have no SSH. The developer image (`scripts/deploy-vm image`) adds SSH for root
  with your own public key, for development only.
