# Disk encryption: the root filesystem lives in LUKS2 on the partition labelled foyelle-root, and the
# initrd asks for the passphrase on the console at boot. The image ships with a public initial
# passphrase (nixos/image/luks-image.nix); the System surface shows a warning while it still works
# and offers to change it.
#
# The passphrase never passes through Foyelle's software: changing it opens a root-only prompt on
# its own console (tty8), where cryptsetup reads the old and new passphrases from the keyboard.
# The harness, the model, surfaces and the audit log only ever see "a prompt was opened". The apply
# helper starts that prompt, and only with a one-time approval token (tool system.disk_passphrase).
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.foyelle.disk;
  vt = toString cfg.passphraseConsole;
  prompt = pkgs.writeShellScript "foyelle-disk-passphrase" ''
    set -u
    export PATH=${
      lib.makeBinPath [
        pkgs.cryptsetup
        pkgs.kbd
        pkgs.coreutils
        pkgs.ncurses
      ]
    }
    chvt ${vt}
    clear
    cat <<'EOF'
    Foyelle: change the disk passphrase

    The disk passphrase unlocks this machine's disk at every start. Type the current passphrase,
    then the new one twice. Nothing you type here reaches Foyelle, the agent or the audit log.
    Use a long passphrase (several words) and keep a copy somewhere safe: without it the data on
    this disk cannot be recovered.

    EOF
    # Interactive luksChangeKey asks twice for the new passphrase on its own; --verify-passphrase
    # would also make it ask twice for the current one.
    if cryptsetup luksChangeKey --pbkdf argon2id --iter-time 2000 \
        --timeout 300 ${lib.escapeShellArg cfg.device}; then
      result="The disk passphrase was changed."
      status=0
    else
      result="The disk passphrase was NOT changed."
      status=1
    fi
    printf '\n%s Press Enter to return to Foyelle.\n' "$result"
    read -r -t 120 _ || true
    exit "$status"
  '';
in
{
  options.foyelle.disk = {
    encrypted = lib.mkEnableOption "a LUKS2-encrypted root filesystem";
    device = lib.mkOption {
      type = lib.types.str;
      default = "/dev/disk/by-partlabel/foyelle-root";
      description = "The LUKS partition holding the root filesystem.";
    };
    initialPassphrase = lib.mkOption {
      type = lib.types.str;
      default = "foyelle";
      description = ''
        The passphrase the image is built with. It is public (it is in this repository and in the
        image), so it protects nothing until the user changes it on the System surface.
      '';
    };
    passphraseConsole = lib.mkOption {
      type = lib.types.ints.between 7 12;
      default = 8;
      description = "Virtual terminal for the passphrase prompt (outside logind's automatic gettys).";
    };
  };

  config = lib.mkIf (config.foyelle.enable && cfg.encrypted) {
    # systemd's initrd asks for the passphrase on every console (nixpkgs is retiring the scripted one).
    boot.initrd.systemd.enable = lib.mkDefault true;
    boot.initrd.luks.devices.root = {
      inherit (cfg) device;
      allowDiscards = true;
    };
    fileSystems."/" = {
      device = "/dev/mapper/root";
      fsType = "ext4";
    };
    # The encrypted image has a fixed size; nothing to grow at boot.
    boot.growPartition = lib.mkForce false;

    # Public by design: the System connector's disk status checks whether it still opens the disk.
    environment.etc."foyelle/disk-initial-passphrase" = {
      text = cfg.initialPassphrase;
      mode = "0400";
    };

    systemd.services.foyelle-disk-passphrase = {
      description = "Foyelle: change the disk passphrase on tty${vt}";
      conflicts = [ "getty@tty${vt}.service" ];
      serviceConfig = {
        Type = "oneshot";
        ExecStart = prompt;
        ExecStopPost = "${pkgs.kbd}/bin/chvt 1";
        StandardInput = "tty-force";
        StandardOutput = "tty";
        StandardError = "tty";
        TTYPath = "/dev/tty${vt}";
        TTYReset = true;
        TTYVHangup = true;
        TTYVTDisallocate = true;
        TimeoutStartSec = "15min";
      };
    };
  };
}
