# Services the person already uses, through their own MCP servers (the entries of Hermes's
# catalogue, optional-mcps/ in the harness), each behind a local bridge helper
# (connectors/…/bridge). The harness only ever talks to the bridge, which is an ordinary Foyelle
# helper: sandboxed, with an egress proxy that reaches the service's hosts and nothing else, and a
# Foyelle tier on every tool it passes through. Tools not listed here are invisible, and
# everything a service returns is untrusted.
#
# A bridge is off until enabled: turning one on sends what the person asks of it to that service.
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.foyelle;
  read = {
    tier = "read";
  };
  reversible = {
    tier = "reversible";
  };
  # The Accounts card's tools for a service that needs an account: only the card may call them,
  # and the one-time code is a secret argument.
  accountTools = {
    account_status = read;
    account_signin_start = reversible // {
      surface_only = true;
    };
    account_signin_finish = reversible // {
      surface_only = true;
      secret_args = [ "code" ];
    };
    account_disconnect = reversible // {
      surface_only = true;
    };
  };
  # The Services directory: every reviewed service, by id.
  directoryPath = ../../connectors/src/foyelle_connectors/services/directory;
  directory = lib.mapAttrs' (
    file: _:
    let
      e = lib.importJSON (directoryPath + "/${file}");
    in
    lib.nameValuePair e.id e
  ) (lib.filterAttrs (file: kind: kind == "regular" && lib.hasSuffix ".json" file) (builtins.readDir directoryPath));
  # Services the person added by URL (the config repo's services.json, through a reviewed system
  # change): their own slots, nobody reviewed them, tools follow their hints.
  custom = lib.listToAttrs (map (e: lib.nameValuePair e.id e) cfg.customServices);
  entryBridge =
    isCustom: e:
    {
      title = lib.mkDefault e.title;
      url = lib.mkDefault e.url;
      hosts = lib.mkDefault e.hosts;
      sensitivity = lib.mkDefault e.sensitivity;
      category = lib.mkDefault (e.category or "other");
      summary = lib.mkDefault (e.summary or "");
      reviewed = lib.mkDefault (!isCustom && (e.reviewed or null) != null);
      port = lib.mkDefault ((if isCustom then 7340 else 7320) + e.slot);
      index = lib.mkDefault ((if isCustom then 110 else 20) + e.slot);
      tools = lib.mkDefault (
        lib.mapAttrs (_: r: { inherit (r) tier; } // lib.optionalAttrs (r.egress or false) { egress = true; }) (
          lib.filterAttrs (_: r: !(r.hidden or false)) (e.tools or { })
        )
      );
      views = lib.mkDefault (e.views or { });
      newTools = lib.mkDefault (if isCustom then "by_hint" else e.new_tools or "review");
      hidden = lib.mkDefault (lib.attrNames (lib.filterAttrs (_: r: r.hidden or false) (e.tools or { })));
    }
    // lib.optionalAttrs (e.auth.kind == "oauth") {
      auth = "oauth";
      signin = {
        page = lib.mkDefault e.auth.signin_page;
        hosts = lib.mkDefault e.auth.signin_hosts;
      };
    };
  # The bridge renames tools the same way (search-flight → search_flight).
  localName = lib.replaceStrings [ "-" "." ] [ "_" "_" ];
  enabled = lib.filterAttrs (_: b: b.enable) cfg.bridges;
  # What foyelle-services-sync may start: every service, with its port, and those always on.
  servicesFile = {
    available = lib.mapAttrs (_: b: {
      inherit (b)
        port
        title
        category
        summary
        reviewed
        ;
      account = b.auth == "oauth";
      private = b.sensitivity == "private";
      signin_hosts = if b.auth == "oauth" then b.signin.hosts else [ ];
    }) cfg.bridges;
    always = lib.attrNames enabled;
  };
  # Where the shell helper keeps the services the person connected (shell/services.py).
  connectedFile = "${cfg.stateDir}/connectors/shell/services.json";

  bridgeOpts =
    { name, ... }:
    {
      options = {
        enable = lib.mkEnableOption ''
          the ${name} service always on, whatever the person chose (development). Every service is
          built either way; the person connects one from the Services card (foyelle-services-sync)
        '';
        title = lib.mkOption {
          type = lib.types.str;
          description = "The service's name, as the person sees it.";
        };
        url = lib.mkOption {
          type = lib.types.strMatching "https://[^/]+(/.*)?";
          description = "The service's MCP endpoint.";
        };
        hosts = lib.mkOption {
          type = lib.types.listOf lib.types.str;
          description = "Hosts the bridge may reach (the endpoint's, and any its sign-in needs).";
        };
        tools = lib.mkOption {
          type = lib.types.attrsOf (lib.types.attrsOf lib.types.anything);
          description = ''
            The service's tools to pass through, by the service's own name, each with its Foyelle
            policy (tier, egress). They are always marked untrusted.
          '';
        };
        views = lib.mkOption {
          type = lib.types.attrsOf (lib.types.strMatching "[a-z][a-z0-9_]*");
          default = { };
          example = {
            search-flight = "flights";
          };
          description = ''
            Foyelle's cards for a tool's results, by the service's tool name: the view
            connectors/…/ui/views/<bridge>_<view>.html, served by the bridge as ui://<bridge>/<view>.
          '';
        };
        sensitivity = lib.mkOption {
          type = lib.types.enum [
            "private"
            "public"
          ];
          default = "private";
          description = "Public when the service holds nothing about the person (flight search).";
        };
        auth = lib.mkOption {
          type = lib.types.enum [
            "none"
            "oauth"
          ];
          default = "none";
          description = "oauth: the person signs in from the Accounts card, in the sign-in browser (signin.nix).";
        };
        signin = {
          page = lib.mkOption {
            type = lib.types.nullOr (lib.types.strMatching "https://[^/?#]+/[^?#]*");
            default = null;
            description = "The service's sign-in page (its OAuth authorization endpoint). Any other is refused.";
          };
          hosts = lib.mkOption {
            type = lib.types.listOf lib.types.str;
            default = [ ];
            description = "Hosts the sign-in browser may reach for this service's sign-in page.";
          };
        };
        newTools = lib.mkOption {
          type = lib.types.enum [
            "review"
            "by_hint"
          ];
          default = "review";
          description = "by_hint: tools the entry doesn't list pass through, with tiers from their own hints.";
        };
        hidden = lib.mkOption {
          type = lib.types.listOf lib.types.str;
          default = [ ];
          description = "The service's tools that never reach the harness, even with newTools = by_hint.";
        };
        category = lib.mkOption {
          type = lib.types.str;
          default = "other";
          description = "Where the Services card lists it (tasks, travel, notes…).";
        };
        summary = lib.mkOption {
          type = lib.types.str;
          default = "";
          description = "One line about the service, for the Services card.";
        };
        reviewed = lib.mkOption {
          type = lib.types.bool;
          default = true;
          description = "Foyelle reviewed this entry (false for services the person added by URL).";
        };
        port = lib.mkOption { type = lib.types.port; };
        index = lib.mkOption {
          type = lib.types.ints.between 20 129;
          description = "Egress proxy 127.0.1.<index>: 20 + slot for directory services, 110 + slot for those added by URL.";
        };
      };
    };
in
{
  options.foyelle.oauthClientMetadataUrl = lib.mkOption {
    type = lib.types.nullOr (lib.types.strMatching "https://[^/]+/.+");
    default = null;
    example = "https://foyelle.org/oauth/client.json";
    description = ''
      Foyelle's published OAuth client document (a Client ID Metadata Document, served at exactly
      this https address; docs/oauth-client-metadata.json). Services whose sign-in server supports
      such documents then take this address as Foyelle's client id, and nothing is registered per
      computer; the others keep using dynamic client registration.
    '';
  };

  options.foyelle.customServices = lib.mkOption {
    type = lib.types.listOf (lib.types.attrsOf lib.types.anything);
    default = [ ];
    description = ''
      Services the person added by URL: the entries of the config repo's services.json (the same
      schema as the Services directory, checked by system.propose_service).
    '';
  };

  options.foyelle.bridges = lib.mkOption {
    type = lib.types.attrsOf (lib.types.submodule bridgeOpts);
    default = { };
    description = "Services reached through their own MCP servers, each behind a local bridge helper.";
  };

  config = lib.mkIf cfg.enable {
    # Foyelle's selection from the catalogue, reviewed whatever upstream approves: one JSON entry
    # per service in the Services directory (connectors/…/services/directory, checked by
    # foyelle_connectors.services.directory). Hidden tools never reach the harness.
    foyelle.bridges = lib.mapAttrs (_: entryBridge false) directory // lib.mapAttrs (_: entryBridge true) custom;

    foyelle.connectors = lib.mapAttrs (name: b: {
      enable = true;
      autoStart = b.enable;
      byHint = b.newTools == "by_hint";
      service = true;
      inherit (b) title sensitivity port index;
      command = "foyelle-connector-bridge";
      egressHosts = b.hosts;
      egressPorts = [ 443 ];
      environment = {
        FOYELLE_BRIDGE_NAME = name;
        FOYELLE_BRIDGE_TITLE = b.title;
        FOYELLE_BRIDGE_URL = b.url;
        FOYELLE_BRIDGE_TOOLS = builtins.toJSON (lib.attrNames b.tools);
        FOYELLE_BRIDGE_VIEWS = builtins.toJSON b.views;
        FOYELLE_BRIDGE_NEW_TOOLS = b.newTools;
        FOYELLE_BRIDGE_HIDDEN = builtins.toJSON b.hidden;
      }
      // lib.optionalAttrs (b.auth == "oauth") {
        FOYELLE_BRIDGE_AUTH = "oauth";
        FOYELLE_BRIDGE_SIGNIN_PAGE = b.signin.page;
      }
      // lib.optionalAttrs (b.auth == "oauth" && cfg.oauthClientMetadataUrl != null) {
        FOYELLE_OAUTH_CLIENT_METADATA_URL = cfg.oauthClientMetadataUrl;
      };
      tools =
        lib.mapAttrs' (tool: rule: lib.nameValuePair (localName tool) (rule // { untrusted = true; })) b.tools
        // lib.optionalAttrs (b.auth == "oauth") accountTools;
    }) cfg.bridges;

    environment.etc."foyelle/services.json".text = builtins.toJSON servicesFile;

    # Connecting a service from the Services card writes the shell helper's services.json; this
    # starts (or stops) that service's helper and egress proxy, for services in the directory only.
    # It also runs at boot, before the harness, so connected services are up when it starts.
    systemd.paths.foyelle-services-sync = {
      wantedBy = [ "multi-user.target" ];
      pathConfig.PathChanged = connectedFile;
    };
    systemd.services.foyelle-services-sync = {
      description = "Start the services the person connected";
      wantedBy = [ "multi-user.target" ];
      before = [ "foyelle-harness.service" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = false;
      };
      path = [
        pkgs.jq
        pkgs.coreutils
        pkgs.gnugrep
        pkgs.gnused
        pkgs.diffutils
        pkgs.iproute2
        config.systemd.package
      ];
      script = ''
        # Applied until the file stops changing: a connect or disconnect made while this runs would
        # not start it again (a path unit doesn't re-trigger a running service).
        while true; do
          seen=$(cat ${connectedFile} 2>/dev/null || true)
          connected=$(printf '%s' "$seen" | jq -r '.connected[]? // empty' 2>/dev/null || true)
          always=" $(jq -r '.always | join(" ")' /etc/foyelle/services.json) "
          for id in $(jq -r '.available | keys[]' /etc/foyelle/services.json); do
            case "$always" in *" $id "*) continue ;; esac
            if printf '%s\n' $connected | grep -qx -- "$id"; then
              systemctl start "foyelle-egress-$id.service" "foyelle-connector-$id.service" || true
              # Wait until it listens (only the harness may connect to a connector's port).
              port=$(jq -r --arg id "$id" '.available[$id].port' /etc/foyelle/services.json)
              for _ in $(seq 1 60); do
                [ -n "$(ss -Hltn "sport = :$port")" ] && break
                sleep 0.5
              done
            else
              systemctl stop "foyelle-connector-$id.service" "foyelle-egress-$id.service" || true
            fi
          done
          # The sign-in browser's proxy: Google's hosts and the connected services' sign-in hosts.
          if [ -e /etc/foyelle/signin-base.filter ]; then
            {
              cat /etc/foyelle/signin-base.filter
              for id in $connected $(jq -r '.always[]' /etc/foyelle/services.json); do
                jq -r --arg id "$id" '.available[$id].signin_hosts[]? // empty' /etc/foyelle/services.json
              done | sort -u | sed 's/[.]/\\./g; s/^/^/; s/$/$/'
            } > /run/foyelle/signin.filter.new
            if ! cmp -s /run/foyelle/signin.filter.new /run/foyelle/signin.filter; then
              mv /run/foyelle/signin.filter.new /run/foyelle/signin.filter
              systemctl try-restart foyelle-egress-signin.service || true
            else
              rm -f /run/foyelle/signin.filter.new
            fi
          fi
          [ "$(cat ${connectedFile} 2>/dev/null || true)" = "$seen" ] && break
        done
      '';
    };

    assertions = lib.mapAttrsToList (name: b: {
      assertion = b.auth == "none" || (b.signin.page != null && b.signin.hosts != [ ]);
      message = "foyelle.bridges.${name}: a service with auth = \"oauth\" needs signin.page and signin.hosts";
    }) enabled;
  };
}
