# A UEFI disk image whose root filesystem is inside LUKS2, as the alpha plan asks ("the VM disk is
# LUKS-encrypted with a passphrase"). Everything happens inside a throwaway VM: partition, format
# LUKS with the initial passphrase, install the system with nixos-install, install systemd-boot,
# then write a compressed qcow2. The passphrase is changed on the System surface after first boot
# (the image ships with a known initial one, so it is not a secret until changed).
#
# The disk is a fixed virtual size (sparse and compressed, so the file stays small); no partition
# growing is needed.
{
  pkgs,
  lib,
  config,
  name ? "foyelle-alpha",
  diskSizeMiB ? 32768,
  espSizeMiB ? 512,
  initialPassphrase,
  memSize ? 2048,
}:
let
  toplevel = config.system.build.toplevel;
  closureInfo = pkgs.closureInfo { rootPaths = [ toplevel ]; };
  binPath = lib.makeBinPath (
    with pkgs;
    [
      cryptsetup
      kmod
      dosfstools
      e2fsprogs
      gptfdisk
      util-linux
      coreutils
      findutils
      gnused
      config.system.build.nixos-install
      nixos-enter
      nix
      systemdMinimal
    ]
  );
in
pkgs.vmTools.runInLinuxVM (
  pkgs.runCommand name
    {
      inherit memSize;
      preVM = ''
        export PATH=${binPath}:$PATH
        diskImage=disk.raw
        truncate -s ${toString diskSizeMiB}M $diskImage
        sgdisk --clear \
          --new=1:0:+${toString espSizeMiB}M --typecode=1:EF00 --change-name=1:ESP \
          --new=2:0:0 --typecode=2:8309 --change-name=2:foyelle-root \
          $diskImage
      '';
      postVM = ''
        mkdir -p $out
        ${pkgs.qemu-utils}/bin/qemu-img convert -f raw -O qcow2 -c $diskImage $out/${name}.qcow2
        rm -f $diskImage
      '';
    }
    ''
      export PATH=${binPath}:$PATH
      set -euo pipefail

      # The initial passphrase is public (it is changed on the System surface after first boot).
      passphrasePath=$TMPDIR/passphrase
      printf '%s' ${lib.escapeShellArg initialPassphrase} > $passphrasePath
      # No udev in this VM: device-mapper creates /dev/mapper nodes itself.
      export DM_DISABLE_UDEV=1
      mkdir -p /run/cryptsetup
      # The build VM's initrd loads only a few modules; MODULE_DIR points at the rest.
      modprobe dm_mod
      modprobe dm_crypt

      # The partition table was written outside the VM; make the kernel see it.
      partx -u /dev/vda || true
      [ -b /dev/vda2 ] || { echo "no root partition"; exit 1; }

      mkfs.vfat -F 32 -n ESP /dev/vda1

      # LUKS2 with argon2id. The cost is fixed rather than benchmarked: this VM may be emulated and
      # far slower than the machine that unlocks the disk. Changing the passphrase on the System
      # surface re-derives the key slot with a benchmark on the real machine.
      cryptsetup luksFormat --type luks2 --batch-mode --label foyelle-root \
        --pbkdf argon2id --pbkdf-memory 262144 --pbkdf-parallel 2 --pbkdf-force-iterations 4 \
        --key-file "$passphrasePath" /dev/vda2
      cryptsetup open --key-file "$passphrasePath" /dev/vda2 root
      rm -f "$passphrasePath"
      mkfs.ext4 -F -L nixos /dev/mapper/root

      mkdir -p /mnt
      mount /dev/mapper/root /mnt
      mkdir -p /mnt/boot
      mount /dev/vda1 /mnt/boot

      export HOME=$TMPDIR NIX_STATE_DIR=$TMPDIR/state
      nix-store --load-db < ${closureInfo}/registration
      nixos-install --root /mnt --no-bootloader --no-root-passwd --no-channel-copy \
        --system ${toplevel} --substituters ""

      # systemd-boot finds the ESP through /dev/block without udev.
      mkdir -p /dev/block
      ln -sf /dev/vda1 /dev/block/$(cat /sys/class/block/vda1/dev)
      NIXOS_INSTALL_BOOTLOADER=1 nixos-enter --root /mnt -- \
        /nix/var/nix/profiles/system/bin/switch-to-configuration boot

      umount -R /mnt
      cryptsetup close root
    ''
)
