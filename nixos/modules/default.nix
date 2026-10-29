{ inputs, self }:
{ lib, pkgs, ... }:
{
  imports = [
    ./harness.nix
    ./connectors.nix
    ./canvas.nix
    ./system-loop.nix
    ./dev.nix
    ./disk.nix
    ./voice.nix
    ./signin.nix
    ./bridges.nix
  ];

  options.foyelle = {
    enable = lib.mkEnableOption "Foyelle, the agent-native shell";

    stateDir = lib.mkOption {
      type = lib.types.str;
      default = "/var/lib/foyelle";
      description = "Root of Foyelle's persistent state.";
    };

    ids = lib.mkOption {
      type = lib.types.attrsOf lib.types.int;
      default = {
        harness = 790;
        canvas = 1000; # a normal user: it owns the graphical session
        tokenGroup = 793;
        applyGroup = 792;
      };
      description = "Static uids/gids. The firewall matches the harness and canvas uids numerically.";
    };
  };

  config = {
    # Passed as a module argument rather than an overlay: NixOS tests (and users with a read-only
    # nixpkgs.pkgs) cannot take another overlay.
    _module.args.foyellePkgs = import ../pkgs { inherit pkgs inputs self; };
  };
}
