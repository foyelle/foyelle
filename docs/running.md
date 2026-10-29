# Running Foyelle

Foyelle runs as a NixOS virtual machine on Apple Silicon Macs, in UTM. To work on the canvas
without a VM, see [CONTRIBUTING.md](../CONTRIBUTING.md).

## Build and run

```bash
nix build .#vm-image          # qcow2 (UEFI) for UTM on Apple Silicon, from an aarch64-linux builder
nix flake check               # unit tests, egress guard, VM tests (x86_64-linux with KVM)
```

On a Mac without Nix, `scripts/nix-docker` runs the same commands in a container. To use the
image in UTM: create a QEMU (not Apple Virtualization) VM, UEFI boot, the qcow2 as its disk, a
virtio-gpu display with GPU acceleration, and "Shared Network" so the VM reaches a local model
server on the Mac at `192.168.64.1:11434` (any OpenAI-compatible server; Ollama by default).
`scripts/utm-create` does this for you; it quits and reopens UTM once, so stop your other VMs
first.

Dictation (the microphone button) needs a sound device with a microphone. `scripts/utm-create` adds
one (Intel HDA, which UTM pairs with an input-capable codec); in a VM made by hand, add it under
Sound in the VM's settings. Keep UTM's audio backend at its default (SPICE): the CoreAudio backend
cannot capture the microphone. macOS also has to let UTM use the microphone. It asks the first time
a VM records; if you said no, turn UTM on in System Settings → Privacy & Security → Microphone and
restart the VM.

The disk is encrypted (LUKS2). At every start the VM asks for the disk passphrase on its screen;
the image's initial passphrase is `foyelle`, which is public, so change it on first boot with
"Change disk passphrase…" on the System surface.

Settings that are yours, not the image's (the remote model key file, email and calendar
accounts, extra folders for Files), go in the machine's config repo, which the System surface
edits by proposals you approve.

## The local model

The local model runs on the Mac, not in the VM (see [architecture.md](architecture.md#6-local-model)).
Serve Ollama on the bridge address only (not your LAN) while the VM runs:

```bash
OLLAMA_HOST=192.168.64.1:11434 OLLAMA_CONTEXT_LENGTH=32768 ollama serve
```

The context length matters: Hermes's prompt with Foyelle's tools is about 10K tokens, and Ollama's
default (4K on most Macs) silently cuts it from the start, so the model never sees its tools. The
dev configuration uses `qwen3:4b-instruct`.

## Developing against a running VM

Rebuilding the image for every change is slow. Instead, build the developer image once; it adds
SSH for root with your public key (from `$FOYELLE_DEV_SSH_KEYS` or `~/.ssh/id_ed25519.pub`, read
at build time, never committed) at the fixed address `192.168.64.250`:

```bash
scripts/deploy-vm image                                        # out/foyelle-alpha-dev.qcow2
scripts/utm-create out/foyelle-alpha-dev.qcow2 "Foyelle dev"
```

Then, after each change, `scripts/deploy-vm` builds the system, copies only the store paths the
VM lacks and switches to it in place (`scripts/deploy-vm boot` activates it at the next reboot);
`scripts/deploy-vm ssh journalctl -u foyelle-harness -f` reads logs. This bypasses the reviewed
system-change loop, so it is for developing Foyelle only; release images have no SSH.

The dev VM reaches the internet through `scripts/host-proxy` on the Mac (`scripts/host-proxy
install` keeps it running at login; `uninstall` removes it), because a VPN that tunnels all of the
Mac's traffic silently drops what UTM's shared network NATs for the VM. The VM's egress proxies
still filter by host first; the Mac proxy only forwards HTTPS CONNECTs from `192.168.64.0/24`.
