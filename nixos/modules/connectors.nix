# Connectors: one systemd service each, built from one template (mkConnectorService) so every
# connector gets the same isolation:
#   - DynamicUser: a fresh uid per service, so connectors cannot read each other's files;
#   - LoadCredential: secrets visible only to that service;
#   - ProtectHome=tmpfs plus bind mounts of exactly the folders the connector needs;
#   - IPAddressDeny=any, allowing only 127.0.0.1 (where the harness reaches it) and the loopback
#     address of its own egress proxy, which allows named hosts only;
#   - a firewall rule so only the harness uid may connect to a connector port.
# The policy file /etc/foyelle/policy.json is generated from the same declarations.
{
  config,
  lib,
  pkgs,
  foyellePkgs,
  ...
}:
let
  cfg = config.foyelle;
  enabled = lib.filterAttrs (_: c: c.enable) cfg.connectors;

  toolRule = lib.types.submodule {
    options = {
      tier = lib.mkOption {
        type = lib.types.enum [
          "read"
          "reversible"
          "outbound"
          "system"
        ];
      };
      untrusted = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "The tool returns content from outside the user's control (taints the session).";
      };
      egress = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "The tool sends data off the machine (asked about in a tainted session).";
      };
      surface_only = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "Only the connector's own surface (the user's UI) may call it; the model never can.";
      };
      public = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "What it returns holds nothing about the person: no sharing question on a private connector.";
      };
      secret_args = lib.mkOption {
        type = lib.types.listOf lib.types.str;
        default = [ ];
        description = "Arguments holding a secret (a password, a key): never hashed into the audit log or shown on an approval.";
      };
    };
  };

  # Python with the connector SDK (foyelle_connectors.sdk), for connectors written for one machine.
  # (An application does not propagate its libraries, so they are listed.)
  sdkPython = pkgs.python3.withPackages (ps: [
    (ps.toPythonModule foyellePkgs.connectors)
    ps.mcp
    ps.httpx
    ps.uvicorn
    ps.starlette
  ]);

  connectorOpts =
    { name, config, ... }:
    {
      options = {
        enable = lib.mkEnableOption "the ${name} connector";
        service = lib.mkOption {
          type = lib.types.bool;
          default = false;
          description = "A Services directory entry: offered to the model per conversation (harness/plugin conversation.py).";
        };
        byHint = lib.mkOption {
          type = lib.types.bool;
          default = false;
          description = ''
            Tools not listed in `tools` take their tier from their own MCP hints (readOnlyHint,
            destructiveHint), and the person is asked once per conversation before an unreviewed
            one runs. For services whose directory entry says "new_tools": "by_hint".
          '';
        };
        autoStart = lib.mkOption {
          type = lib.types.bool;
          default = true;
          description = ''
            Start the connector (and its egress proxy) at boot. A service the person connects from
            the Services card is built but not started; foyelle-services-sync starts it when they
            connect it (bridges.nix). The harness and the policy know it either way.
          '';
        };
        title = lib.mkOption {
          type = lib.types.nullOr lib.types.str;
          default = null;
          example = "System monitor";
          description = "Display name for this connector's surfaces when they declare no title of their own.";
        };
        source = lib.mkOption {
          type = lib.types.nullOr lib.types.path;
          default = null;
          example = lib.literalExpression "./connectors/${name}";
          description = ''
            A connector written for this machine: a folder with server.py (and any view files),
            run with Foyelle's Python and connector SDK, isolated like the first-party ones. Set
            in the config repo and reviewed as a system change (see the System connector's
            howto tool).
          '';
        };
        package = lib.mkOption {
          type = lib.types.package;
          default =
            if config.source == null then
              foyellePkgs.connectors
            else
              pkgs.writeShellScriptBin "foyelle-connector-${name}" ''
                cd ${config.source}
                exec ${sdkPython}/bin/python ${config.source}/server.py
              '';
          defaultText = lib.literalExpression "foyellePkgs.connectors";
        };
        command = lib.mkOption {
          type = lib.types.str;
          default = "foyelle-connector-${name}";
          description = "Executable in the package's bin/.";
        };
        port = lib.mkOption { type = lib.types.port; };
        index = lib.mkOption {
          type = lib.types.ints.between 1 129;
          description = ''
            Selects the loopback address 127.0.1.<index> of this connector's egress proxy: 10-39 for
            Foyelle's own and the Services directory's, 40-99 for connectors written for this
            machine, 110-129 for services added by URL (100 and 101 are the harness's and the
            sign-in browser's).
          '';
        };
        sensitivity = lib.mkOption {
          type = lib.types.enum [
            "private"
            "public"
          ];
          default = "private";
          description = "Private content goes only to the local model unless the user approves, per session.";
        };
        tools = lib.mkOption {
          type = lib.types.attrsOf toolRule;
          default = { };
          description = "Policy per tool; tools not listed get the default tier (outbound: ask).";
        };
        egressHosts = lib.mkOption {
          type = lib.types.listOf lib.types.str;
          default = [ ];
          description = "Host names this connector may reach through its egress proxy.";
        };
        egressPorts = lib.mkOption {
          type = lib.types.listOf lib.types.port;
          default = [ 443 ];
        };
        directAddresses = lib.mkOption {
          type = lib.types.listOf lib.types.str;
          default = [ ];
          example = [ "203.0.113.10/32" ];
          description = "Addresses the connector may reach without the proxy (protocols a proxy cannot carry, such as IMAP and SMTP).";
        };
        environment = lib.mkOption {
          type = lib.types.attrsOf lib.types.str;
          default = { };
        };
        credentials = lib.mkOption {
          type = lib.types.attrsOf lib.types.str;
          default = { };
          description = "Credential name -> host file, passed with LoadCredential (read as $CREDENTIALS_DIRECTORY/<name>).";
        };
        readOnlyPaths = lib.mkOption {
          type = lib.types.listOf lib.types.str;
          default = [ ];
          description = "Folders bind-mounted read-only into the connector.";
        };
        readWritePaths = lib.mkOption {
          type = lib.types.listOf lib.types.str;
          default = [ ];
          description = "Folders bind-mounted read-write into the connector.";
        };
        supplementaryGroups = lib.mkOption {
          type = lib.types.listOf lib.types.str;
          default = [ ];
        };
        memoryMax = lib.mkOption {
          type = lib.types.str;
          default = "512M";
        };
        timeout = lib.mkOption {
          type = lib.types.int;
          default = 120;
          description = "Harness-side MCP call timeout in seconds.";
        };
        extraServiceConfig = lib.mkOption {
          type = lib.types.attrs;
          default = { };
        };
      };
    };

  proxyAddress = c: "127.0.1.${toString c.index}";

  mkConnectorService =
    name: c:
    let
      proxy = "http://${proxyAddress c}:3128";
    in
    {
      description = "Foyelle connector: ${name}";
      wantedBy = lib.optional c.autoStart "multi-user.target";
      requires = lib.optional (c.egressHosts != [ ]) "foyelle-egress-${name}.service";
      after = [ "network.target" ] ++ lib.optional (c.egressHosts != [ ]) "foyelle-egress-${name}.service";
      environment = {
        FOYELLE_CONNECTOR_HOST = "127.0.0.1";
        FOYELLE_CONNECTOR_PORT = toString c.port;
        HOME = "/var/lib/foyelle/connectors/${name}";
        PYTHONDONTWRITEBYTECODE = "1";
        NO_PROXY = "127.0.0.1,localhost";
        no_proxy = "127.0.0.1,localhost";
      }
      // lib.optionalAttrs (c.egressHosts != [ ]) {
        HTTPS_PROXY = proxy;
        HTTP_PROXY = proxy;
        https_proxy = proxy;
        http_proxy = proxy;
      }
      // c.environment;
      serviceConfig = {
        ExecStart = "${c.package}/bin/${c.command}";
        Restart = "always";
        RestartSec = 2;
        TimeoutStopSec = 10;
        DynamicUser = true;
        StateDirectory = "foyelle/connectors/${name}";
        StateDirectoryMode = "0700";
        LoadCredential = lib.mapAttrsToList (cred: path: "${cred}:${path}") c.credentials;
        SupplementaryGroups = c.supplementaryGroups;

        ProtectHome = "tmpfs";
        BindReadOnlyPaths = c.readOnlyPaths;
        BindPaths = c.readWritePaths;
        ProtectSystem = "strict";
        PrivateTmp = true;
        PrivateDevices = true;
        NoNewPrivileges = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectKernelLogs = true;
        ProtectControlGroups = true;
        ProtectClock = true;
        ProtectHostname = true;
        ProtectProc = "invisible";
        ProcSubset = "pid";
        RestrictNamespaces = true;
        RestrictRealtime = true;
        RestrictSUIDSGID = true;
        LockPersonality = true;
        CapabilityBoundingSet = "";
        RestrictAddressFamilies = [
          "AF_UNIX"
          "AF_INET"
          "AF_INET6"
        ];
        SystemCallArchitectures = "native";
        SystemCallFilter = [ "@system-service" ];
        MemoryMax = c.memoryMax;
        UMask = "0077";
        IPAddressDeny = "any";
        IPAddressAllow = [
          "127.0.0.1/32"
          "127.0.0.53/32" # systemd-resolved stub: DNS only, upstream lookups are resolved's
        ]
        ++ lib.optional (c.egressHosts != [ ]) "${proxyAddress c}/32"
        ++ c.directAddresses;
      }
      // c.extraServiceConfig;
    };

  policy = {
    local_model_base_urls = [ cfg.models.local.baseUrl ];
    default_tier = "outbound";
    connectors = lib.mapAttrs (
      _: c:
      {
        inherit (c) sensitivity tools;
      }
      // lib.optionalAttrs c.byHint { by_hint = true; }
      // lib.optionalAttrs c.service {
        service = true;
        inherit (c) title;
      }
    ) enabled;
  };

  # tinyproxy with a default-deny host filter; CONNECT only to the listed ports.
  mkProxyService =
    name: p:
    let
      filter = pkgs.writeText "foyelle-egress-${name}.filter" (
        lib.concatMapStrings (host: if host == "*" then ".*\n" else "^${lib.escapeRegex host}$\n") p.hosts
      );
      conf = pkgs.writeText "foyelle-egress-${name}.conf" ''
        Listen ${p.address}
        Port 3128
        Timeout 600
        MaxClients 64
        Allow 127.0.0.0/8
        DisableViaHeader Yes
        FilterDefaultDeny Yes
        FilterType ere
        FilterURLs Off
        FilterCaseSensitive Off
        Filter "${if (p.filterFile or null) != null then p.filterFile else filter}"
        ${lib.concatMapStrings (port: "ConnectPort ${toString port}\n") p.ports}
        ${lib.optionalString (cfg.egressUpstream != null) "Upstream http ${cfg.egressUpstream}"}
        LogLevel Notice
      '';
    in
    {
      description = "Foyelle egress proxy: ${name}";
      wantedBy = lib.optional (p.autoStart or true) "multi-user.target";
      after = [ "network.target" ];
      serviceConfig = {
        ExecStart = "${pkgs.tinyproxy}/bin/tinyproxy -d -c ${conf}";
        Restart = "always";
        DynamicUser = true;
        ProtectHome = true;
        ProtectSystem = "strict";
        PrivateTmp = true;
        PrivateDevices = true;
        NoNewPrivileges = true;
        CapabilityBoundingSet = "";
        RestrictAddressFamilies = [
          "AF_INET"
          "AF_INET6"
          "AF_UNIX"
        ];
        SystemCallFilter = [ "@system-service" ];
        MemoryMax = "128M";
        # A proxy that only fetches public hosts: no private, link-local or CGNAT targets.
        IPAddressDeny = [
          "10.0.0.0/8"
          "172.16.0.0/12"
          "192.168.0.0/16"
          "169.254.0.0/16"
          "100.64.0.0/10"
          "fc00::/7"
          "fe80::/10"
        ];
      }
      // lib.optionalAttrs (cfg.egressUpstream != null) {
        # Systemd lets an allowed address through a denied range.
        IPAddressAllow = [ "${upstreamHost}/32" ];
      };
    };

  allProxies =
    cfg.egressProxies
    // lib.mapAttrs (_: c: {
      address = proxyAddress c;
      hosts = c.egressHosts;
      ports = c.egressPorts;
      inherit (c) autoStart;
    }) (lib.filterAttrs (_: c: c.egressHosts != [ ]) enabled);

  ports = lib.mapAttrsToList (_: c: c.port) enabled;
  upstreamHost = lib.head (lib.splitString ":" cfg.egressUpstream);
in
{
  options.foyelle = {
    connectors = lib.mkOption {
      type = lib.types.attrsOf (lib.types.submodule connectorOpts);
      default = { };
      description = "First-party MCP connectors (see nixos/modules/connectors-default.nix).";
    };
    egressProxies = lib.mkOption {
      type = lib.types.attrsOf (
        lib.types.submodule {
          options = {
            address = lib.mkOption { type = lib.types.str; };
            hosts = lib.mkOption { type = lib.types.listOf lib.types.str; };
            ports = lib.mkOption {
              type = lib.types.listOf lib.types.port;
              default = [ 443 ];
            };
            filterFile = lib.mkOption {
              type = lib.types.nullOr lib.types.str;
              default = null;
              description = ''
                Read the host filter from this file at start instead of from `hosts` (one ERE per
                line), for a list that changes at runtime. Whoever writes it restarts the proxy.
              '';
            };
          };
        }
      );
      default = { };
      description = "Extra egress proxies (the harness's own).";
    };
    egressUpstream = lib.mkOption {
      type = lib.types.nullOr (lib.types.strMatching "[0-9.]+:[0-9]+");
      default = null;
      example = "192.168.64.1:3129";
      description = ''
        An HTTP proxy (IPv4 address and port) every egress proxy forwards its allowed requests to,
        instead of connecting itself. For a development VM on a Mac whose VPN swallows the VM's
        NAT traffic: scripts/host-proxy on the Mac forwards them through the Mac's own connection.
        The host filters and ports here still apply first.
      '';
    };
  };

  imports = [ ./connectors-default.nix ];

  config = lib.mkIf cfg.enable {
    environment.etc."foyelle/policy.json".text = builtins.toJSON policy;

    systemd.services =
      lib.mapAttrs' (name: c: lib.nameValuePair "foyelle-connector-${name}" (mkConnectorService name c)) enabled
      // lib.mapAttrs' (name: p: lib.nameValuePair "foyelle-egress-${name}" (mkProxyService name p)) allProxies;

    # Only the harness may open a connector port; only the canvas and the harness may open the
    # gateway port. Loopback traffic, matched on the sending socket's uid.
    services.resolved.enable = true;
    # The resolver serves loopback only: no mDNS or LLMNR announcing this machine on the network.
    services.resolved.settings.Resolve = {
      LLMNR = "no";
      MulticastDNS = "no";
    };
    networking.nftables.enable = true;
    networking.nftables.tables.foyelle = {
      family = "inet";
      content = ''
        chain output {
          type filter hook output priority 0; policy accept;
          ${lib.optionalString (ports != [ ]) ''
            oifname "lo" tcp dport { ${lib.concatMapStringsSep ", " toString ports} } meta skuid != ${toString cfg.ids.harness} counter reject with tcp reset
          ''}
          oifname "lo" tcp dport ${toString cfg.harness.port} meta skuid != { ${toString cfg.ids.harness}, ${toString cfg.ids.canvas} } counter reject with tcp reset
        }
      '';
    };

    assertions = [
      {
        assertion = lib.length (lib.unique (map (c: c.index) (lib.attrValues enabled))) == lib.length (lib.attrValues enabled);
        message = "foyelle.connectors.<name>.index must be unique";
      }
      {
        assertion = lib.length (lib.unique ports) == lib.length ports;
        message = "foyelle.connectors.<name>.port must be unique";
      }
      {
        # A connector's proxy is 127.0.1.<index>; the harness's and the sign-in browser's are set
        # by address, so a connector index must not land on one of them.
        assertion =
          let
            addresses = lib.mapAttrsToList (_: p: p.address) allProxies;
          in
          lib.length (lib.unique addresses) == lib.length addresses;
        message = "two egress proxies share an address: check foyelle.connectors.<name>.index against foyelle.egressProxies";
      }
    ];
  };
}
