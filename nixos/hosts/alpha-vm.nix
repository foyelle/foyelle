# The alpha reference machine: a NixOS VM under QEMU (UTM) on Apple Silicon, UEFI, systemd-boot.
# The configuration stays hardware-agnostic: the same host also evaluates for x86_64-linux, which
# is what CI tests on.
{
  config,
  lib,
  pkgs,
  modulesPath,
  ...
}:
{
  imports = [ (modulesPath + "/profiles/qemu-guest.nix") ];

  foyelle.enable = true;
  # Root on LUKS2 (nixos/modules/disk.nix); vm-image builds the encrypted disk.
  foyelle.disk.encrypted = lib.mkDefault true;

  networking.hostName = "foyelle";
  networking.useDHCP = lib.mkDefault true;
  networking.firewall.enable = true; # nothing listens beyond loopback

  boot.loader.systemd-boot.enable = true;
  boot.loader.systemd-boot.configurationLimit = 20;
  boot.loader.efi.canTouchEfiVariables = false;
  boot.loader.timeout = 2;
  boot.initrd.availableKernelModules = [
    "virtio_pci"
    "virtio_blk"
    "virtio_scsi"
    "virtio_net"
    "virtio_gpu"
    "xhci_pci"
    "usbhid"
  ];
  boot.kernelParams = [ "console=tty0" ];

  # Same labels as the qemu-efi disk image (the encrypted image overrides the root, see disk.nix).
  fileSystems."/" = lib.mkDefault {
    device = "/dev/disk/by-label/nixos";
    fsType = "ext4";
    autoResize = true;
  };
  fileSystems."/boot" = lib.mkDefault {
    device = "/dev/disk/by-label/ESP";
    fsType = "vfat";
  };
  boot.growPartition = true;
  # Write the qcow2 compressed in one pass (no second full copy while building).
  image.modules.qemu-efi.image.format = "qcow2-compressed";

  services.qemuGuest.enable = true;
  # Copy and paste between the Mac and the canvas (UTM's SPICE clipboard).
  services.spice-vdagentd.enable = true;
  foyelle.canvas.hostClipboard = lib.mkDefault true;
  hardware.graphics.enable = true;

  # The UTM "Shared Network" gateway is the Mac host, where the local model server runs.
  foyelle.models.local.host = lib.mkDefault "192.168.64.1";
  foyelle.models.local.baseUrl = lib.mkDefault "http://192.168.64.1:11434/v1";

  time.timeZone = lib.mkDefault "UTC";
  i18n.defaultLocale = "en_US.UTF-8";
  console.keyMap = lib.mkDefault "us";

  # No SSH and no other listeners in the alpha image; the recovery console is on tty2.
  services.openssh.enable = lib.mkDefault false;

  documentation.enable = false;
  # Everything is a flake (the config repo pins nixpkgs and the registry points at it); a channel
  # would only add an unpinned nixpkgs to NIX_PATH.
  nix.channel.enable = false;
  system.stateVersion = "26.05";

  # `nixos-rebuild build-vm` / `nix run .#…vm`: a disposable VM for development.
  virtualisation.vmVariant = {
    virtualisation = {
      memorySize = 6144;
      cores = 4;
      graphics = true;
      qemu.options = [ "-device virtio-vga" ];
    };
    foyelle.canvas.softwareRendering = true;
    foyelle.models.local = {
      host = "10.0.2.2";
      baseUrl = "http://10.0.2.2:11434/v1";
    };
  };
}
