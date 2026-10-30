# The alpha connector set and its policy. Tiers: read and reversible are allowed (logged),
# outbound asks every time, system always goes through the review flow. `untrusted` marks tools
# that return content from outside the user's control; `egress` marks tools that send data out.
{
  config,
  lib,
  foyellePkgs,
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
  outbound = {
    tier = "outbound";
  };
  # The Accounts card's tools: only the card may connect or disconnect an account, and the
  # password it sends is a secret (never hashed into the audit log or shown on an approval).
  accountTools = {
    account_status = read;
    account_providers = read;
    account_connect = reversible // {
      surface_only = true;
      secret_args = [ "password" ];
    };
    account_disconnect = reversible // {
      surface_only = true;
    };
    account_google_start = reversible // {
      surface_only = true;
    };
    account_google_finish = reversible // {
      surface_only = true;
      secret_args = [ "code" ];
    };
  };
  gcfg = cfg.google;
  googleOn = gcfg.clientId != null;
  # Sign in with Google (signin.nix): the helpers exchange the code at Google's token endpoint.
  googleEnv = lib.optionalAttrs googleOn (
    {
      FOYELLE_GOOGLE_CLIENT_ID = gcfg.clientId;
    }
    // lib.optionalAttrs (gcfg.clientSecret != null) {
      FOYELLE_GOOGLE_CLIENT_SECRET = gcfg.clientSecret;
    }
  );
in
{
  options.foyelle.google = {
    clientId = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = ''
        Google OAuth client ID (a "Desktop app" client) for signing in with Google from the
        Accounts card, in the sign-in browser (signin.nix). See docs/google-signin.md.
      '';
    };
    clientSecret = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = ''
        That client's "secret". Google treats desktop clients as public and still asks for it on
        token requests, so it ships in the image and is not secret; a code is only useful to the
        helper that holds its PKCE verifier.
      '';
    };
  };

  options.foyelle.files.roots = lib.mkOption {
    type = lib.types.attrsOf lib.types.str;
    default = {
      documents = "${cfg.stateDir}/files/Documents";
    };
    description = "Folders the Files connector may see, by lower-case name. Nothing else is mounted into it.";
  };

  config = lib.mkIf cfg.enable {
    systemd.tmpfiles.rules = lib.mapAttrsToList (_: path: "d ${path} 0777 root root -") cfg.files.roots;

    foyelle.connectors = {
      # System surfaces (status, memory, settings, audit). It manages the harness's own memory
      # files, so it runs as the harness user rather than a dynamic one.
      shell = {
        enable = lib.mkDefault true;
        port = 7300;
        index = 10;
        sensitivity = "private";
        tools = {
          status = read;
          memory_read = read;
          memory_export = read;
          open_accounts = read;
          open_cards = read;
          memory_write = reversible;
          user_name_set = reversible;
          settings_get = read;
          settings_set = reversible;
          audit_query = read;
          privacy_today = read;
          # The Services card: only the person connects or disconnects a service.
          services_list = read;
          # Services per conversation: the model loads one; the chips turn one on or off.
          use_service = read // {
            public = true;
          };
          conversation_services = read;
          conversation_service_set = reversible // {
            surface_only = true;
          };
          service_connect = reversible // {
            surface_only = true;
          };
          service_disconnect = reversible // {
            surface_only = true;
          };
          # Bots: the person's own; only the canvas makes, changes or deletes one.
          bots_list = read // {
            public = true;
          };
          bot_save = reversible // {
            surface_only = true;
          };
          bot_delete = reversible // {
            surface_only = true;
          };
          foyelle_share_set = reversible // {
            surface_only = true;
          };
          # What routines left for the person: shown and dropped by the canvas.
          waiting_list = read // {
            surface_only = true;
          };
          waiting_drop = reversible // {
            surface_only = true;
          };
          routine_results = read // {
            surface_only = true;
          };
          theme_list = read // {
            public = true;
          };
          theme_get = read // {
            public = true;
          };
          theme_use = reversible // {
            public = true;
          };
          theme_create = reversible // {
            public = true;
          };
          theme_delete = reversible // {
            public = true;
          };
          # The remote model key is typed by the user on the Settings surface, never by the model.
          model_key_save = reversible // {
            surface_only = true;
            secret_args = [ "key" ];
          };
          model_key_clear = reversible // {
            surface_only = true;
          };
        };
        environment = {
          FOYELLE_MEMORY_DIR = "${cfg.stateDir}/harness/memories";
          FOYELLE_AUDIT_LOG = "${cfg.stateDir}/audit/audit.jsonl";
          FOYELLE_SETTINGS_FILE = "/etc/foyelle/harness-enforced.json";
          FOYELLE_SETTINGS_OVERRIDES = "${cfg.stateDir}/connectors/shell/settings.json";
          FOYELLE_HARNESS_ENV = "${cfg.stateDir}/harness/.env";
          FOYELLE_REMOTE_KEY_ENV = lib.concatStringsSep " " cfg.models.remote.keyEnv;
          FOYELLE_REMOTE_LABEL = cfg.models.remote.label;
          FOYELLE_CONNECTORS = lib.concatStringsSep "," (
            lib.mapAttrsToList (n: c: "${n}=${toString c.port}") (lib.filterAttrs (_: c: c.enable) cfg.connectors)
          );
          # Tools that send something off the computer, for the privacy summary.
          FOYELLE_OUTBOUND_TOOLS = lib.concatStringsSep "," (
            lib.concatLists (
              lib.mapAttrsToList (
                n: c:
                lib.mapAttrsToList (t: _: "${n}.${t}") (
                  lib.filterAttrs (_: r: (r.tier or "") == "outbound" || (r.egress or false)) c.tools
                )
              ) (lib.filterAttrs (_: c: c.enable) cfg.connectors)
            )
          );
          FOYELLE_HEALTH_METHOD = "systemd";
        };
        extraServiceConfig = {
          DynamicUser = lib.mkForce false;
          User = "foyelle-harness";
          Group = "foyelle-harness";
          ProtectHome = lib.mkForce true;
          # "-": the harness creates its memory directory on first use.
          # Its memory files, and its .env for the remote model key typed on the Settings surface.
          ReadWritePaths = [
            "-${cfg.stateDir}/harness/memories"
            "-${cfg.stateDir}/harness"
          ];
          ReadOnlyPaths = [ "-${cfg.stateDir}/audit" ];
        };
      };

      files = {
        enable = lib.mkDefault true;
        port = 7301;
        index = 11;
        sensitivity = "private";
        tools = {
          browse = read;
          list = read;
          search = read;
          read = read;
          write = reversible;
          move = reversible;
          trash = reversible;
          undo = reversible;
          versions = read;
          read_version = read;
          restore = reversible;
          # Base64 image data is for the file browser to draw, not for the model's context.
          read_image = read // {
            surface_only = true;
          };
        };
        environment.FOYELLE_FILES_ROOTS = lib.concatStringsSep "," (
          lib.mapAttrsToList (name: path: "${name}=${path}:rw") cfg.files.roots
        );
        readWritePaths = lib.attrValues cfg.files.roots;
      };

      email = {
        # Starts "not connected"; the person connects an account from the Accounts card (kept in
        # the connector's own state directory). An account can still be set here instead, with
        # environment (FOYELLE_EMAIL_ADDRESS, FOYELLE_IMAP_HOST, …) and credentials.email-password.
        enable = lib.mkDefault true;
        port = 7302;
        index = 12;
        sensitivity = "private";
        # IMAP and SMTP go through this connector's own proxy (CONNECT), on mail ports only. Any
        # host name is allowed because the account decides it; the connector connects only to the
        # servers of the account the person connected, and the model cannot change them.
        egressHosts = lib.mkDefault [ "*" ];
        egressPorts = [
          993
          465
          587
        ]
        ++ lib.optional googleOn 443;
        environment = googleEnv;
        tools = accountTools // {
          search = read // {
            untrusted = true;
          };
          read = read // {
            untrusted = true;
          };
          draft = reversible // {
            egress = true;
          };
          delete_draft = outbound;
          send = outbound;
        };
      };

      calendar = {
        # Starts "not connected", like email; CalDAV over HTTPS through its own proxy.
        enable = lib.mkDefault true;
        port = 7303;
        index = 13;
        sensitivity = "private"; # invites carry attendee names and addresses
        egressHosts = lib.mkDefault [ "*" ];
        egressPorts = [ 443 ];
        # Times without a zone (and "today") are the machine's own (time.timeZone; UTC when unset).
        environment = googleEnv // {
          FOYELLE_TIMEZONE = if config.time.timeZone == null then "UTC" else config.time.timeZone;
        };
        tools = accountTools // {
          list_events = read // {
            untrusted = true;
          };
          get_event = read // {
            untrusted = true;
          };
          create_event = reversible // {
            egress = true;
          };
          update_event = reversible // {
            egress = true;
          };
          respond_invite = outbound;
          delete_event = outbound;
        };
      };

      web = {
        enable = lib.mkDefault true;
        port = 7304;
        index = 14;
        sensitivity = "public";
        # Fetch may reach any public web host on 80/443; the connector itself refuses private and
        # loopback targets, holds no user data, and its calls are asked about in tainted sessions.
        egressHosts = lib.mkDefault [ "*" ];
        egressPorts = [
          80
          443
        ];
        tools = {
          search = read // {
            egress = true;
          };
          fetch = read // {
            untrusted = true;
            egress = true;
          };
        };
      };

      # The official MCP Apps example "system monitor": live CPU and memory charts of this
      # machine. A third-party server, run under the same template as Foyelle's own.
      sysmon = {
        enable = lib.mkDefault true;
        title = "System monitor";
        package = foyellePkgs.system-monitor;
        command = "foyelle-connector-sysmon";
        port = 7307;
        index = 17;
        sensitivity = "private"; # hostname and hardware details
        tools = {
          get_system_info = read;
          # App-only upstream (its chart polls it), but only reads CPU and memory counters, so the
          # agent may read it too, to answer questions about load and memory.
          poll_system_stats = read;
        };
        # It reads /proc/stat and /proc/meminfo, which ProcSubset=pid hides.
        extraServiceConfig.ProcSubset = "all";
      };

      # Surfaces the agent makes for the user, available without a rebuild (see the System
      # connector's howto "surface"). They call other connectors' tools through the gateway as
      # origin "workshop": approvals apply and surface-only tools are refused.
      workshop = {
        enable = lib.mkDefault true;
        port = 7306;
        index = 16;
        sensitivity = "public";
        tools = {
          save = reversible;
          list = read;
          read = read;
          show = read;
          versions = read;
          restore = reversible;
          remove = reversible;
        };
      };

      system = {
        enable = lib.mkDefault true;
        port = 7305;
        index = 15;
        sensitivity = "private";
        timeout = 3600; # builds can take a while
        memoryMax = "2G";
        tools = {
          status = read;
          propose = reversible;
          propose_service = reversible;
          build = reversible;
          diff = read;
          reject = reversible;
          apply = {
            tier = "system";
          };
          rollback = {
            tier = "system";
          };
          disk_status = read;
          howto = read;
          # Opens the passphrase prompt on tty8; the passphrase itself never reaches the connector.
          disk_passphrase = {
            tier = "system";
          };
          # Opens passwd for the recovery user on tty8; the password never reaches the connector.
          recovery_password = {
            tier = "system";
          };
        };
      };
    };
  };
}
