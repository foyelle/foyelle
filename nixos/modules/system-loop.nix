# Agent-driven system changes. The agent never holds root: the System connector writes a commit
# to a branch of the on-device config repo, builds it without switching, shows the diff, and on
# the user's approval asks the apply helper (root, socket-activated, separate unit) to switch to
# that exact commit. The helper checks the one-time approval token the harness minted, builds the
# commit itself, switches, health-checks and rolls back on failure.
{
  config,
  lib,
  pkgs,
  foyellePkgs,
  self,
  inputs,
  ...
}:
let
  cfg = config.foyelle;
  scfg = cfg.system;
  repo = "${cfg.stateDir}/connectors/system/config";
  system = pkgs.stdenv.hostPlatform.system;
  template = pkgs.runCommand "foyelle-config-template" { } ''
    mkdir -p $out
    substitute ${../config-template/flake.nix} $out/flake.nix \
      --subst-var-by source ${self.outPath} \
      --subst-var-by system ${system} \
      --subst-var-by host ${scfg.host}
    cp ${../config-template/configuration.nix} $out/configuration.nix
    cp ${../config-template/README.md} $out/README.md
    cp ${pkgs.writeText "machine.json" (builtins.toJSON scfg.machineSettings)} $out/machine.json
  '';
in
{
  options.foyelle.system = {
    machineSettings = lib.mkOption {
      type = lib.types.attrsOf lib.types.anything;
      default = { };
      description = ''
        NixOS settings this machine was built with beyond the release (plain JSON values), kept
        in the config repo as machine.json so changes applied through the System loop keep them.
        The dev configuration uses it for its developer access.
      '';
    };
    host = lib.mkOption {
      type = lib.types.str;
      default = "foyelle";
      description = "nixosConfigurations attribute of the on-device config repo.";
    };
    healthUnits = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [
        "foyelle-harness.service"
        "cage-tty1.service"
      ];
      description = "Units that must be active after a switch, or the helper rolls back.";
    };
    healthTimeout = lib.mkOption {
      type = lib.types.int;
      default = 60;
    };
    buildCommand = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Test hook: a command that prints the out path for a commit instead of building it.";
    };
  };

  config = lib.mkIf cfg.enable {
    users.groups.foyelle-apply.gid = cfg.ids.applyGroup;

    nix.settings = {
      experimental-features = [
        "nix-command"
        "flakes"
      ];
      # Connectors build as unprivileged (dynamic) users through the daemon; only root is trusted.
      allowed-users = [ "*" ];
      trusted-users = [ "root" ];
    };

    # Every flake input's source is in the image, so the config repo evaluates offline, and so is
    # the patched Hermes source: evaluating the harness package builds it (import from
    # derivation), and the machine has no compiler toolchain or network to rebuild it.
    system.extraDependencies = map (input: input.outPath) (lib.attrValues inputs) ++ [
      self.outPath
      foyellePkgs.harness-src
    ];

    environment.etc."foyelle/config-template".source = template;

    systemd.sockets.foyelle-apply = {
      description = "Foyelle apply helper socket";
      wantedBy = [ "sockets.target" ];
      listenStreams = [ "/run/foyelle/apply.sock" ];
      socketConfig = {
        SocketMode = "0660";
        SocketUser = "root";
        SocketGroup = "foyelle-apply";
        Accept = false;
      };
    };

    systemd.services.foyelle-apply = {
      description = "Foyelle apply helper (switches to approved config commits)";
      requires = [ "foyelle-apply.socket" ];
      # Never restarted or stopped by a switch: it is the process doing the switching.
      restartIfChanged = false;
      stopIfChanged = false;
      path = [
        config.nix.package
        pkgs.git
        pkgs.coreutils
        pkgs.gnutar
        pkgs.cryptsetup
        pkgs.shadow
        config.systemd.package
      ];
      environment = {
        FOYELLE_DISK_DEVICE = config.foyelle.disk.device;
        FOYELLE_DISK_INITIAL_KEY = "/etc/foyelle/disk-initial-passphrase";
        FOYELLE_CONFIG_REPO = repo;
        FOYELLE_HOST = scfg.host;
        FOYELLE_APPROVALS_DIR = "/run/foyelle/approvals";
        FOYELLE_APPLY_STATE = "${cfg.stateDir}/apply";
        FOYELLE_HEALTH_UNITS = lib.concatStringsSep " " scfg.healthUnits;
        FOYELLE_HEALTH_TIMEOUT = toString scfg.healthTimeout;
        # git refuses to read a repository owned by another user unless told it is safe.
        GIT_CONFIG_COUNT = "1";
        GIT_CONFIG_KEY_0 = "safe.directory";
        GIT_CONFIG_VALUE_0 = repo;
      }
      // lib.optionalAttrs (scfg.buildCommand != null) { FOYELLE_APPLY_BUILD_CMD = scfg.buildCommand; };
      serviceConfig = {
        ExecStart = "${foyellePkgs.apply-helper}/bin/foyelle-apply";
        StateDirectory = "foyelle/apply";
        NoNewPrivileges = true;
        PrivateTmp = true;
        IPAddressDeny = "any";
      };
    };

    # Boot-level changes are installed as the next boot entry; if it never reaches
    # boot-complete.target, systemd-boot counts down and falls back to the previous entry.
    boot.loader.systemd-boot.bootCounting.enable = lib.mkDefault true;

    systemd.services.foyelle-connector-system.path = [
      config.nix.package
      pkgs.git
    ];
    # A new release brings a new template: restart, so the start-up step moves the config repo to it.
    systemd.services.foyelle-connector-system.restartTriggers = [ template ];

    foyelle.connectors.system = {
      supplementaryGroups = [ "foyelle-apply" ];
      environment = {
        FOYELLE_CONFIG_REPO = repo;
        FOYELLE_HOST = scfg.host;
        FOYELLE_APPLY_SOCKET = "/run/foyelle/apply.sock";
        FOYELLE_CONFIG_TEMPLATE = "/etc/foyelle/config-template";
        # Ports and proxy indexes in use, for the howto guide (agent-written connectors).
        FOYELLE_CONNECTOR_SLOTS = lib.concatStringsSep ";" (
          lib.mapAttrsToList (n: c: "${n}: port ${toString c.port}, index ${toString c.index}") (
            lib.filterAttrs (_: c: c.enable) cfg.connectors
          )
        );
        NIX_REMOTE = "daemon";
      };
      extraServiceConfig = {
        # Seed the config repo from the template on first start.
        ExecStartPre = pkgs.writeShellScript "foyelle-config-repo-init" ''
          set -eu
          export PATH=${
            lib.makeBinPath [
              pkgs.git
              pkgs.coreutils
              pkgs.gnugrep
              pkgs.gnused
              pkgs.diffutils
              config.nix.package
            ]
          }
          source_of() { grep -o 'path:/nix/store/[^"]*' "$1" | head -1; }
          if [ ! -d ${repo}/.git ]; then
            mkdir -p ${repo}
            cp -r /etc/foyelle/config-template/. ${repo}/
            chmod -R u+w ${repo}
            cd ${repo}
            git init -q -b main
            git config user.name "Foyelle"
            git config user.email "foyelle@localhost"
            # Flakes only see tracked files: add them before locking, then commit both.
            git add -A
            nix --extra-experimental-features "nix-command flakes" flake lock --offline || true
            git add -A
            git commit -qm "Initial configuration"
          else
            # A new Foyelle release is running (an update, or scripts/deploy-vm): bring the files the
            # release owns (flake.nix with its source, machine.json, README.md) up to date, so
            # proposals build against the code that runs. configuration.nix is the user's and is
            # left alone. Only on a clean main checkout; pending proposals then show as out of date.
            cd ${repo}
            if [ "$(git symbolic-ref --quiet HEAD)" = refs/heads/main ] && git diff --quiet HEAD; then
              changed=""
              for f in flake.nix machine.json README.md; do
                if ! cmp -s /etc/foyelle/config-template/$f $f; then
                  cp /etc/foyelle/config-template/$f $f
                  chmod u+w $f
                  changed=1
                fi
              done
              if [ -n "$changed" ]; then
                git add -A
                nix --extra-experimental-features "nix-command flakes" flake lock --offline || true
                git add -A
                want=$(source_of flake.nix)
                git commit -qm "Follow the running Foyelle release (''${want#path:/nix/store/})"
              fi
            fi
          fi
        '';
        # The connector talks to the Nix daemon and the apply helper over Unix sockets only.
        ReadWritePaths = [ "/nix/var/nix/daemon-socket" ];
      };
    };
  };
}
