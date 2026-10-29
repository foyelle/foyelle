# The harness: patched Hermes running `hermes serve` (the JSON-RPC gateway the canvas talks to).
# It reaches data only through connectors (MCP over firewalled local ports) and the network only
# through its own egress proxy (model endpoints) and the local model host.
{
  config,
  lib,
  pkgs,
  foyellePkgs,
  ...
}:
let
  cfg = config.foyelle;
  hcfg = cfg.harness;
  home = "${cfg.stateDir}/harness";
  enabledConnectors = lib.filterAttrs (_: c: c.enable) cfg.connectors;
  # Connectors up at boot: the harness waits for these. A service the person connects later is
  # configured too, and joins with reload.mcp once foyelle-services-sync has started it.
  bootConnectors = lib.filterAttrs (_: c: c.autoStart) enabledConnectors;

  # Foyelle's enforced harness settings, merged into config.yaml at every start.
  enforced = lib.recursiveUpdate {
    # The remote route when a key is configured; otherwise the local model server, so a fresh
    # machine answers as soon as the Mac runs Ollama (or any OpenAI-compatible server).
    model =
      if cfg.models.remote.keyFile != null then
        {
          provider = cfg.models.remote.provider;
          default = cfg.models.remote.model;
        }
      else
        {
          provider = "custom";
          base_url = cfg.models.local.baseUrl;
          default = cfg.models.local.model;
          api_key = "local";
        };
    providers.local = {
      base_url = cfg.models.local.baseUrl;
      default_model = cfg.models.local.model;
      transport = "chat_completions";
    };
    # Only the agent's own state tools; enabled MCP servers (the connectors) fold in on their own.
    # Terminal, files, web, browser, code execution and delegation stay off, and the policy plugin
    # denies them as well. Gateway sessions read platform_toolsets.<platform>, not `toolsets`.
    # cronjob lets a bot set up routines (the policy asks first); routine runs (platform cron)
    # get no clarify and no cronjob, and Hermes keeps them from scheduling more routines.
    platform_toolsets =
      lib.genAttrs [ "cli" "tui" "desktop" "dashboard" ] (_: [
        "memory"
        "skills"
        "todo"
        "clarify"
        "session_search"
        "cronjob"
      ])
      // {
        cron = [
          "memory"
          "skills"
          "todo"
          "session_search"
        ];
      };
    cron.allow_agent_scheduling = false;
    mcp_servers = lib.mapAttrs (name: c: {
      url = "http://127.0.0.1:${toString c.port}/mcp";
      trust = "full"; # Foyelle's policy plugin is the gate, not Hermes's untrusted-server prompt
      timeout = c.timeout;
      connect_timeout = 10;
    }) enabledConnectors;
    plugins = {
      # The policy loads from HERMES_REQUIRED_PLUGINS_DIR in every profile, whatever this says.
      enabled = [ "foyelle-policy" ];
      # The policy hook waits for a human approval; run it inline with no wall-clock cap.
      hook_callback_timeout = 0;
    };
    approvals = {
      mode = "manual"; # never let the "smart" guardian model approve on the user's behalf
      timeout = 300;
      cron_mode = "deny"; # nobody can answer an approval in a routine run
    };
    skills.write_approval = true; # new or changed skills are staged as diffs for review
    telemetry.shared_metrics = {
      enabled = false;
      send = false;
    };
    updates.check = false;
    auth.adopt_external_logins = false;
    nous.guest = false;
    tools.connectors.enabled = false;
    model_catalog.enabled = false;
    # Few, first-party tools: offer them all directly instead of behind the tool-search bridge.
    tools.tool_search.enabled = "off";
    # Both routes, for foyelle-config-merge (removed before config.yaml is written).
    _foyelle.routes = {
      remote = {
        inherit (cfg.models.remote) provider model;
        key_env = lib.head cfg.models.remote.keyEnv;
      };
      local = {
        base_url = cfg.models.local.baseUrl;
        inherit (cfg.models.local) model;
      };
    };
  } hcfg.extraSettings;

  enforcedFile = pkgs.writeText "foyelle-harness-enforced.json" (builtins.toJSON enforced);

  proxy = "http://${hcfg.egress.address}:3128";

  # Key variables and endpoints of the remote providers Foyelle has been used with.
  knownProviders = {
    openrouter = {
      label = "OpenRouter";
      keyEnv = [ "OPENROUTER_API_KEY" ];
      hosts = [ "openrouter.ai" ];
    };
    opencode-zen = {
      label = "OpenCode Zen";
      keyEnv = [
        "OPENCODE_ZEN_API_KEY"
        "OPENCODE_GO_API_KEY"
      ];
      hosts = [ "opencode.ai" ];
    };
  };
in
{
  options.foyelle = {
    models = {
      remote = {
        provider = lib.mkOption {
          type = lib.types.str;
          default = "openrouter";
          description = "Hermes provider for the default (remote) route (openrouter, opencode-zen, …).";
        };
        label = lib.mkOption {
          type = lib.types.str;
          default = knownProviders.${cfg.models.remote.provider}.label or cfg.models.remote.provider;
          description = "The provider's name on the Settings surface.";
        };
        keyEnv = lib.mkOption {
          type = lib.types.nonEmptyListOf lib.types.str;
          default =
            knownProviders.${cfg.models.remote.provider}.keyEnv or [
              "${lib.toUpper (lib.replaceStrings [ "-" ] [ "_" ] cfg.models.remote.provider)}_API_KEY"
            ];
          description = ''
            Environment variables the Settings surface writes the API key to (in the harness's
            private .env). The first one decides whether a key is configured.
          '';
        };
        model = lib.mkOption {
          type = lib.types.str;
          # Fast, cheap and good at tool calls ($0.05 in / $0.12 out per million tokens, Oct 2026).
          default = "deepseek/deepseek-v4.1-flash";
          description = ''
            Pinned main model on the remote route (the user can change it on the Settings
            surface, model.main). Auxiliary tasks follow the main model.
          '';
        };
        keyFile = lib.mkOption {
          type = lib.types.nullOr lib.types.path;
          default = null;
          description = "File holding the provider API key (see keyEnv). Never stored in the Nix store.";
        };
        hosts = lib.mkOption {
          type = lib.types.listOf lib.types.str;
          default = knownProviders.${cfg.models.remote.provider}.hosts or [ ];
          description = "Hosts the harness may reach through its egress proxy.";
        };
      };
      local = {
        baseUrl = lib.mkOption {
          type = lib.types.str;
          default = "http://192.168.64.1:11434/v1";
          description = "OpenAI-compatible model server on the Mac host (UTM shared network gateway).";
        };
        host = lib.mkOption {
          type = lib.types.str;
          default = "192.168.64.1";
          description = "IP address of the local model host; the harness may connect to it directly.";
        };
        model = lib.mkOption {
          type = lib.types.str;
          # Qwen 3.5 9B (Ollama library, Oct 2026): tools and thinking, 256K context, ~7 GB, so it
          # leaves room for context in the M4 Pro's 12 GB model budget. Benchmark against gemma4:12b.
          default = "qwen3.5:9b";
          description = "Model on the local server (chosen by benchmark before M3; candidates qwen3.5:9b, gemma4:12b).";
        };
      };
    };

    harness = {
      package = lib.mkOption {
        type = lib.types.package;
        default = foyellePkgs.harness;
        defaultText = lib.literalExpression "foyellePkgs.harness";
      };
      port = lib.mkOption {
        type = lib.types.port;
        default = 9119;
      };
      tokenFile = lib.mkOption {
        type = lib.types.str;
        default = "/run/foyelle/harness-token";
        description = "Gateway token, readable by the canvas group, regenerated at every boot.";
      };
      egress.address = lib.mkOption {
        type = lib.types.str;
        default = "127.0.1.100";
        description = "Loopback address of the harness's egress proxy.";
      };
      extraSettings = lib.mkOption {
        type = lib.types.attrs;
        default = { };
        description = "Extra harness config.yaml keys, merged over Foyelle's enforced settings.";
      };
    };
  };

  config = lib.mkIf cfg.enable {
    users.groups.foyelle-harness.gid = cfg.ids.harness;
    users.groups.foyelle-gateway.gid = cfg.ids.tokenGroup;
    users.users.foyelle-harness = {
      isSystemUser = true;
      uid = cfg.ids.harness;
      group = "foyelle-harness";
      home = home;
    };

    environment.etc."foyelle/harness-enforced.json".source = enforcedFile;

    systemd.tmpfiles.rules = [
      "d ${cfg.stateDir} 0755 root root -"
      "d /run/foyelle 0755 root root -"
      "d /run/foyelle/approvals 0700 foyelle-harness foyelle-harness -"
      "d ${cfg.stateDir}/audit 0750 foyelle-harness foyelle-harness -"
      "d ${home} 0700 foyelle-harness foyelle-harness -"
      # Which services each conversation uses: the policy plugin (here) and the shell helper (the
      # chips) both write it; both run as foyelle-harness.
      "d ${cfg.stateDir}/connectors/shell/conversations 0700 foyelle-harness foyelle-harness -"
      # What routines left for the person (the policy plugin writes, the shell helper lists).
      "d ${cfg.stateDir}/connectors/shell/waiting 0700 foyelle-harness foyelle-harness -"
    ]
    # The layout Hermes expects in managed mode (upstream nix/moduleCommon.nix stateSubdirs).
    ++ map (d: "d ${home}/${d} 0700 foyelle-harness foyelle-harness -") [
      "cron"
      "sessions"
      "logs"
      "memories"
      "plugins"
      "workspace"
    ];

    # A fresh gateway token at every boot: readable by the harness and the canvas group only.
    systemd.services.foyelle-harness-token = {
      description = "Foyelle gateway token";
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
      };
      script = ''
        umask 077
        mkdir -p /run/foyelle
        token=$(${pkgs.coreutils}/bin/head -c 32 /dev/urandom | ${pkgs.coreutils}/bin/base64 | tr -d '/+=\n')
        printf '%s' "$token" > ${hcfg.tokenFile}.tmp
        chown root:foyelle-gateway ${hcfg.tokenFile}.tmp
        chmod 0640 ${hcfg.tokenFile}.tmp
        mv ${hcfg.tokenFile}.tmp ${hcfg.tokenFile}
        printf 'HERMES_DASHBOARD_SESSION_TOKEN=%s\n' "$token" > /run/foyelle/harness.env
        chown foyelle-harness /run/foyelle/harness.env
        chmod 0400 /run/foyelle/harness.env
      '';
    };

    systemd.services.foyelle-harness = {
      description = "Foyelle harness (Hermes gateway)";
      wantedBy = [ "multi-user.target" ];
      requires = [
        "foyelle-harness-token.service"
        "foyelle-egress-harness.service"
      ];
      after = [
        "foyelle-harness-token.service"
        "foyelle-egress-harness.service"
        "network-online.target"
      ]
      ++ map (n: "foyelle-connector-${n}.service") (lib.attrNames bootConnectors)
      ++ [ "foyelle-services-sync.service" ];
      # Hermes reads each connector's tool list at start-up: restart when a switch changes a
      # connector's code or policy, so new tools reach the agent. Not partOf the connectors: a
      # crashing connector must not take the harness down with it.
      restartTriggers =
        lib.mapAttrsToList (n: c: "${n}:${c.package}:${builtins.toJSON c.tools}") enabledConnectors
        ++ [ config.environment.etc."foyelle/policy.json".text ];
      wants = [ "network-online.target" ];
      environment = {
        HOME = home;
        HERMES_HOME = home;
        HERMES_MANAGED = "true";
        HTTPS_PROXY = proxy;
        HTTP_PROXY = proxy;
        https_proxy = proxy;
        http_proxy = proxy;
        NO_PROXY = "127.0.0.1,localhost,${cfg.models.local.host}";
        no_proxy = "127.0.0.1,localhost,${cfg.models.local.host}";
        # Foyelle's policy, loaded in every profile (each bot is one); no tool runs without it.
        HERMES_REQUIRED_PLUGINS_DIR = "${foyellePkgs.policy-plugin}";
        # Routines: run the cron scheduler inside this process, under the policy.
        HERMES_SERVE_CRON = "1";
        FOYELLE_POLICY_FILE = "/etc/foyelle/policy.json";
        FOYELLE_AUDIT_LOG = "${cfg.stateDir}/audit/audit.jsonl";
        FOYELLE_APPROVALS_DIR = "/run/foyelle/approvals";
        # Which services each conversation uses (the policy plugin's conversation.py; the shell
        # helper writes the chips' choices here, as the same user).
        FOYELLE_CONVERSATIONS_DIR = "${cfg.stateDir}/connectors/shell/conversations";
        # The person's bots (the shell helper keeps them) and what their routines left waiting.
        FOYELLE_BOTS_FILE = "${cfg.stateDir}/connectors/shell/bots.json";
        FOYELLE_WAITING_DIR = "${cfg.stateDir}/connectors/shell/waiting";
      };
      preStart = ''
        # The policy comes from HERMES_REQUIRED_PLUGINS_DIR; drop the copy older systems installed.
        rm -rf ${home}/plugins/foyelle-policy
        # Foyelle's identity and how to use its tools, instead of Hermes's stock persona.
        install -m 0644 ${../harness/SOUL.md} ${home}/SOUL.md
        # Bots use the same model keys as Foyelle (saved on the Settings surface into its .env).
        for profile in ${home}/profiles/*/; do
          if [ -d "$profile" ] && [ -f ${home}/.env ]; then
            install -m 0600 ${home}/.env "$profile/.env"
          fi
        done
        # Every profile (Foyelle's own home and each bot's) gets the enforced keys.
        for profile in ${home} ${home}/profiles/*/; do
          [ -d "$profile" ] || continue
          ${foyellePkgs.config-merge}/bin/foyelle-config-merge ${enforcedFile} "$profile/config.yaml" \
            ${cfg.stateDir}/connectors/shell/settings.json "$profile/.env"
        done
        # Hermes parks an MCP server that is down at startup; wait for the connectors first.
        for port in ${lib.concatMapStringsSep " " (c: toString c.port) (lib.attrValues bootConnectors)}; do
          for _ in $(seq 1 60); do
            ${pkgs.bash}/bin/bash -c "exec 3<>/dev/tcp/127.0.0.1/$port" 2>/dev/null && break
            sleep 0.5
          done
        done
      ''
      + lib.optionalString (cfg.models.remote.keyFile != null) ''
        umask 077
        for profile in ${home} ${home}/profiles/*/; do
          [ -d "$profile" ] || continue
          printf 'OPENCODE_ZEN_API_KEY=%s\nOPENCODE_GO_API_KEY=%s\n' \
            "$(cat "$CREDENTIALS_DIRECTORY/model-key")" "$(cat "$CREDENTIALS_DIRECTORY/model-key")" > "$profile/.env"
        done
      '';
      serviceConfig = {
        User = "foyelle-harness";
        Group = "foyelle-harness";
        SupplementaryGroups = [ "foyelle-gateway" ];
        EnvironmentFile = "/run/foyelle/harness.env";
        StateDirectory = "foyelle/harness";
        WorkingDirectory = "${home}/workspace";
        StateDirectoryMode = "0700";
        ExecStart = "${hcfg.package}/bin/hermes serve --host 127.0.0.1 --port ${toString hcfg.port} --no-open --skip-build";
        Restart = "always";
        RestartSec = 2;
        LoadCredential = lib.optional (cfg.models.remote.keyFile != null) "model-key:${cfg.models.remote.keyFile}";

        # Upstream leaves home directories readable; Foyelle does not.
        ProtectHome = true;
        ProtectSystem = "strict";
        ReadWritePaths = [
          home
          "${cfg.stateDir}/audit"
          "/run/foyelle/approvals"
          "-${cfg.stateDir}/connectors/shell/conversations"
          "-${cfg.stateDir}/connectors/shell/waiting"
        ];
        PrivateTmp = true;
        PrivateDevices = true;
        NoNewPrivileges = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectKernelLogs = true;
        ProtectControlGroups = true;
        ProtectClock = true;
        ProtectHostname = true;
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
        UMask = "0077";
        # Network: loopback (connectors, its own proxy) and the local model host, nothing else.
        IPAddressDeny = "any";
        # Not "localhost": that is all of 127.0.0.0/8, which would include other units' proxies.
        IPAddressAllow = [
          "127.0.0.1/32"
          "::1/128"
          "127.0.0.53/32"
          "${hcfg.egress.address}/32"
          "${cfg.models.local.host}/32"
        ];
      };
    };

    # The Settings surface (shell connector) writes the user's model choice; apply it by restarting
    # the harness, which merges it into its config at start.
    systemd.paths.foyelle-harness-settings = {
      wantedBy = [ "multi-user.target" ];
      pathConfig.PathChanged = "${cfg.stateDir}/connectors/shell/settings.json";
    };
    # Only the model choice needs a harness restart (and the key, which the Settings surface saves
    # together with a route change); canvas settings such as natural scrolling must not cut off a
    # conversation in progress.
    systemd.services.foyelle-harness-settings = {
      description = "Apply Settings surface changes to the harness";
      serviceConfig.Type = "oneshot";
      # Also once at boot, to record the settings the harness started with.
      wantedBy = [ "multi-user.target" ];
      after = [ "foyelle-harness.service" ];
      path = [
        pkgs.jq
        pkgs.coreutils
        config.systemd.package
      ];
      script = ''
        settings=${cfg.stateDir}/connectors/shell/settings.json
        state=/run/foyelle/harness-settings.model
        model=$( { jq -cS '.model // {}' "$settings" 2>/dev/null || echo '{}'; sha256sum ${home}/.env 2>/dev/null; } | sha256sum)
        mkdir -p /run/foyelle
        if [ "$(cat "$state" 2>/dev/null)" != "$model" ]; then
          if [ -e "$state" ]; then systemctl try-restart --no-block foyelle-harness.service; fi
          echo "$model" > "$state"
        fi
      '';
    };

    foyelle.egressProxies.harness = {
      address = hcfg.egress.address;
      hosts = cfg.models.remote.hosts;
      ports = [ 443 ];
    };
  };
}
