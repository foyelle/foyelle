# The screen: cage (a kiosk compositor that shows one maximised program and blocks everything
# outside it) runs the Electron canvas as the only program on tty1. There is no window manager
# and no launcher. VT switching stays on so developers can reach the recovery console on tty2.
{
  config,
  lib,
  pkgs,
  foyellePkgs,
  ...
}:
let
  cfg = config.foyelle;
  ccfg = cfg.canvas;
  # Reads spice-vdagent's debug log (stdin) and passes it on; when the host asks for a display size
  # ("monitor 0, config 1920x1200+0+0"), sets it on every cage output once the requests stop for
  # half a second (dragging a window corner sends many).
  followSize = pkgs.writeShellScript "foyelle-follow-display-size" ''
    export PATH=${
      lib.makeBinPath [
        pkgs.wlr-randr
        pkgs.gawk
        pkgs.coreutils
      ]
    }
    pending=""
    apply() {
      for output in $(wlr-randr | awk '/^[^ ]/ { print $1 }'); do
        wlr-randr --output "$output" --custom-mode "$1" || true
      done
    }
    while true; do
      if IFS= read -r -t 0.5 line; then
        printf '%s\n' "$line"
        # The host's request is the size vdagent then tries to set ("Changing screen size to WxH").
        # Its "monitor 0, config" lines are no good: after its own X11 attempt fails it logs the
        # config it restores, the old size, which would undo the request and make the host
        # window grow to match.
        case "$line" in
          *"Changing screen size to "*)
            size="''${line##*Changing screen size to }"
            size="''${size%%[!0-9x]*}"
            case "$size" in [0-9]*x[0-9]*) pending="$size" ;; esac
            ;;
        esac
      else
        status=$?
        if [ -n "$pending" ]; then
          apply "$pending"
          pending=""
        fi
        [ "$status" -gt 128 ] || break # a timeout continues; end of input stops
      fi
    done
  '';
  launcher = pkgs.writeShellScript "foyelle-canvas" ''
    export FOYELLE_HARNESS_URL=ws://127.0.0.1:${toString cfg.harness.port}/api/ws
    export FOYELLE_HARNESS_TOKEN_FILE=${cfg.harness.tokenFile}
    export FOYELLE_SURFACE_TITLES=${
      lib.escapeShellArg (
        builtins.toJSON (
          lib.mapAttrs (_: c: c.title) (lib.filterAttrs (_: c: c.enable && c.title != null) cfg.connectors)
        )
      )
    }
    # Wait for the gateway token; the canvas reconnects on its own after that.
    for _ in $(seq 1 100); do [ -r ${cfg.harness.tokenFile} ] && break; sleep 0.1; done
    ${lib.optionalString ccfg.hostClipboard ''
      # The SPICE session agent shares the clipboard with the VM host. It speaks X11 only, so it
      # and the canvas run on cage's Xwayland display (cage sets DISPLAY for its child).
      export DISPLAY="''${DISPLAY:-:0}"
      ${
        if ccfg.followDisplaySize then
          "${pkgs.spice-vdagent}/bin/spice-vdagent -x -d 2> >(${followSize} >&2) &"
        else
          "${pkgs.spice-vdagent}/bin/spice-vdagent -x &"
      }
    ''}
    exec ${ccfg.electron}/bin/electron \
      ${
        if ccfg.hostClipboard then
          "--ozone-platform=x11"
        else
          "--ozone-platform=wayland --enable-features=UseOzonePlatform"
      } \
      --disable-background-networking --disable-component-update --no-pings \
      ${lib.optionalString ccfg.softwareRendering "--disable-gpu"} \
      ${lib.escapeShellArgs ccfg.extraArgs} \
      ${ccfg.package}/lib/foyelle-canvas
  '';
in
{
  options.foyelle.canvas = {
    package = lib.mkOption {
      type = lib.types.package;
      default = foyellePkgs.canvas;
      defaultText = lib.literalExpression "foyellePkgs.canvas";
    };
    electron = lib.mkOption {
      type = lib.types.package;
      default = pkgs.electron;
      defaultText = lib.literalExpression "pkgs.electron";
    };
    user = lib.mkOption {
      type = lib.types.str;
      default = "foyelle";
    };
    hostClipboard = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Share the clipboard with the VM host through the SPICE agent (UTM, virt-manager). The
        canvas then runs on cage's Xwayland display, because spice-vdagent supports X11 only.
      '';
    };
    followDisplaySize = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        With hostClipboard: apply the VM host's display size requests, so a VM window resized on
        the host (UTM's dynamic resolution) gets a matching resolution instead of a scaled picture.
        The host sends the size to the SPICE agent, which can only try X11 RandR (refused by
        Xwayland) or GNOME's display service; the launcher reads the size from the agent's log and
        sets it on cage with wlr-randr.
      '';
    };
    softwareRendering = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Render without a GPU (pixman in cage, --disable-gpu in Electron), for VMs without virgl.";
    };
    extraArgs = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
    };
    recovery = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "A hidden recovery console on tty2 (development only, not part of the product).";
      };
      initialHashedPassword = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = ''
          Hashed password of the `recovery` user (mkpasswd -m yescrypt), used when the account is
          created. Null: locked until the user sets a password on the System surface ("Set recovery
          password…"), which opens `passwd` on its own console; the password never passes through
          Foyelle.
        '';
      };
      authorizedKeys = lib.mkOption {
        type = lib.types.listOf lib.types.str;
        default = [ ];
      };
    };
  };

  config = lib.mkIf cfg.enable {
    users.users.${ccfg.user} = {
      isNormalUser = true;
      uid = cfg.ids.canvas;
      extraGroups = [
        "foyelle-gateway"
        "video"
        "input"
      ];
      # No password: the account only ever runs the canvas under cage.
      hashedPassword = "!";
    };

    users.users.recovery = lib.mkIf ccfg.recovery.enable {
      isNormalUser = true;
      extraGroups = [ "wheel" ];
      # initial*, not hashedPassword: a password set later with passwd must survive activations.
      initialHashedPassword = if ccfg.recovery.initialHashedPassword == null then "!" else ccfg.recovery.initialHashedPassword;
      openssh.authorizedKeys.keys = ccfg.recovery.authorizedKeys;
    };

    services.cage = {
      enable = true;
      user = ccfg.user;
      program = launcher;
      # CAGE_NATURAL_SCROLL=1 turns on natural (reversed) scrolling; see foyelle-canvas-settings.
      package = pkgs.cage.overrideAttrs (old: {
        patches = (old.patches or [ ]) ++ [ ../pkgs/cage-natural-scroll.patch ];
        buildInputs = old.buildInputs ++ [ pkgs.libinput ];
      });
      extraArguments = [ "-s" ]; # allow VT switching to the recovery console
      environment = {
        XDG_SESSION_TYPE = "wayland";
      }
      // lib.optionalAttrs (!ccfg.hostClipboard) {
        NIXOS_OZONE_WL = "1";
      }
      // lib.optionalAttrs ccfg.softwareRendering {
        WLR_RENDERER = "pixman";
        WLR_NO_HARDWARE_CURSORS = "1";
      };
    };

    # Watchdog: a crashed canvas (or compositor) comes straight back instead of leaving a blank screen.
    systemd.services."cage-tty1" = {
      after = [
        "foyelle-harness-token.service"
        "foyelle-canvas-settings.service"
      ];
      wants = [
        "foyelle-harness-token.service"
        "foyelle-canvas-settings.service"
      ];
      serviceConfig = {
        Restart = lib.mkForce "always";
        RestartSec = 1;
        # Canvas settings the kiosk reads at start (see foyelle-canvas-settings).
        EnvironmentFile = "-/run/foyelle/canvas.env";
        # The compositor and the canvas need no network beyond the harness on loopback.
        IPAddressDeny = "any";
        IPAddressAllow = [
          "127.0.0.1/32"
          "::1/128"
        ];
      };
      unitConfig = {
        StartLimitIntervalSec = 0;
      };
    };

    hardware.graphics.enable = true;
    fonts.packages = [
      ccfg.package # Foyelle's own: Nunito, Gochi Hand, JetBrains Mono and DejaVu Sans Condensed
      pkgs.inter
      pkgs.noto-fonts
      pkgs.noto-fonts-cjk-sans # Chinese, Japanese and Korean (62 MB, with a monospace variant)
      pkgs.noto-fonts-color-emoji
    ];

    # Recovery console only: no display manager, no desktop, no launcher anywhere on the system.
    services.getty.autologinUser = lib.mkForce null;
    systemd.services."getty@tty1".enable = false;
    systemd.services."autovt@tty1".enable = false;

    # Canvas settings from the Settings surface that the kiosk reads at start (natural scrolling):
    # written to an environment file for cage, and the kiosk restarts when they change.
    systemd.paths.foyelle-canvas-settings = {
      wantedBy = [ "multi-user.target" ];
      pathConfig.PathChanged = "${cfg.stateDir}/connectors/shell/settings.json";
    };
    systemd.services.foyelle-canvas-settings = {
      description = "Apply Settings surface changes to the canvas kiosk";
      serviceConfig.Type = "oneshot";
      path = [
        pkgs.jq
        pkgs.coreutils
        config.systemd.package
      ];
      script = ''
        settings=${cfg.stateDir}/connectors/shell/settings.json
        natural=$(jq -r 'if .ui.natural_scroll == true then 1 else 0 end' "$settings" 2>/dev/null || echo 0)
        mkdir -p /run/foyelle
        new="CAGE_NATURAL_SCROLL=$natural"
        if [ "$(cat /run/foyelle/canvas.env 2>/dev/null)" != "$new" ]; then
          echo "$new" > /run/foyelle/canvas.env
          # Restart the kiosk only when it is already running (at boot it starts after this).
          systemctl try-restart --no-block cage-tty1.service
        fi
      '';
    };

    # Set the recovery console's password from the keyboard, on tty8, after an approval (apply
    # helper, tool system.recovery_password). Like the disk passphrase prompt, Foyelle only starts
    # it; passwd reads the password on the console.
    systemd.services.foyelle-recovery-password = lib.mkIf ccfg.recovery.enable {
      description = "Foyelle: set the recovery console password on tty8";
      conflicts = [ "getty@tty8.service" ];
      serviceConfig = {
        Type = "oneshot";
        ExecStart = pkgs.writeShellScript "foyelle-recovery-password" ''
          set -u
          export PATH=${
            lib.makeBinPath [
              pkgs.shadow
              pkgs.kbd
              pkgs.coreutils
              pkgs.ncurses
            ]
          }
          chvt 8
          clear
          cat <<'TEXT'
          Foyelle: set the recovery console password

          The recovery console (Ctrl+Alt+F2, user "recovery") is a text login for repairing this
          machine when the canvas does not work. Type the new password twice. Nothing you type here
          reaches Foyelle, the agent or the audit log.

          TEXT
          if passwd recovery; then
            result="The recovery password was set."
            status=0
          else
            result="The recovery password was NOT changed."
            status=1
          fi
          printf '\n%s Press Enter to return to Foyelle.\n' "$result"
          read -r -t 120 _ || true
          exit "$status"
        '';
        ExecStopPost = "${pkgs.kbd}/bin/chvt 1";
        StandardInput = "tty-force";
        StandardOutput = "tty";
        StandardError = "tty";
        TTYPath = "/dev/tty8";
        TTYReset = true;
        TTYVHangup = true;
        TTYVTDisallocate = true;
        TimeoutStartSec = "15min";
      };
    };
  };
}
