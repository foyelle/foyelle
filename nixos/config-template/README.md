# This machine's configuration

- `configuration.nix` holds your changes; `flake.nix` pins the Foyelle release.
- The System connector writes proposals to `proposal/<id>` branches and builds them without
  switching. You see the configuration diff, the package diff and a plain-language summary,
  then approve or reject.
- On approval the apply helper builds the same commit, switches to it, checks the harness and
  the canvas come back healthy, and rolls back automatically if they do not.
- Changes to the kernel, initrd or boot loader take effect at the next boot; if that boot fails,
  the boot menu falls back to the previous generation by itself.
