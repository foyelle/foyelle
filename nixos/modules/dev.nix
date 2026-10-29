# Developer access to a Foyelle VM: SSH for root with the developer's own keys, so that
# scripts/deploy-vm can push and switch a new system without rebuilding the disk image, and so
# logs can be read from the Mac. This bypasses Foyelle's reviewed system-change loop and must never
# be on in a release image; the release configurations leave it off.
{
  config,
  lib,
  ...
}:
let
  cfg = config.foyelle.dev;
in
{
  options.foyelle.dev = {
    enable = lib.mkEnableOption "developer SSH access (never in a release image)";
    authorizedKeys = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = "SSH public keys allowed to log in as root.";
    };
    address = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = "192.168.64.250/24";
      description = ''
        A fixed extra address on every Ethernet interface (besides DHCP), so the deploy script
        finds the VM without a lookup. UTM's shared network is 192.168.64.0/24.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.authorizedKeys != [ ];
        message = "foyelle.dev.enable needs at least one key in foyelle.dev.authorizedKeys";
      }
    ];

    services.openssh = {
      enable = true;
      openFirewall = true;
      settings = {
        PasswordAuthentication = false;
        KbdInteractiveAuthentication = false;
        PermitRootLogin = "prohibit-password";
      };
    };
    users.users.root.openssh.authorizedKeys.keys = cfg.authorizedKeys;

    networking.useNetworkd = true;
    networking.useDHCP = false;
    systemd.network.networks."10-ethernet" = {
      matchConfig.Type = "ether";
      networkConfig.DHCP = "yes";
      address = lib.optional (cfg.address != null) cfg.address;
    };
  };
}
