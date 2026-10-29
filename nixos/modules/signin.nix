# Sign in with Google, and to services that need an account (bridges.nix): the provider's own
# sign-in page, in Firefox, full screen over the canvas.
#
# Google lets a desktop app sign in only in a real browser (embedded web views are refused), and
# sends the browser back to a loopback address with a one-time code; services' MCP sign-ins work
# the same way. The canvas starts Firefox
# with a fresh profile, listens on 127.0.0.1 for the code and passes it to the helper that started
# the sign-in (canvas/src/main/signin.ts, connectors/…/sdk/google.py). Cage shows the newest
# window, so Firefox covers the canvas until it closes.
#
# What keeps Firefox to that one job:
#   - its own egress proxy, which reaches the sign-in hosts only (Google's when Google sign-in is
#     set up, and each enabled service's), and nothing else on the network: the kiosk's
#     IPAddressAllow adds that proxy's address, nothing more;
#   - locked policies: no add-ons, no about:config, no telemetry, no saved passwords, the proxy
#     locked, and every page blocked except those sign-in pages and the loopback answer;
#   - a throwaway profile, deleted when the Accounts card closes.
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.foyelle;
  scfg = cfg.signin;
  googleOn = cfg.google.clientId != null;
  # Every directory service with an account: the person may connect any of them at runtime, and
  # Firefox's allowed pages are fixed when it is built. They are reviewed hosts, and the sign-in
  # browser only ever opens the page Foyelle asks for.
  services = lib.filterAttrs (_: b: b.auth == "oauth") cfg.bridges;
  serviceHosts = lib.concatMap (b: b.signin.hosts) (lib.attrValues services);
  on = cfg.enable && (googleOn || services != { });
  proxy = "${scfg.proxyAddress}:3128";
  hosts = lib.unique (lib.optionals googleOn scfg.googleHosts ++ serviceHosts);
  pages =
    lib.optionals googleOn [
      "https://accounts.google.com/*"
      "https://accounts.youtube.com/*"
      "https://www.google.com/recaptcha/*"
    ]
    ++ map (h: "https://${h}/*") (lib.unique serviceHosts);

  firefox = pkgs.wrapFirefox pkgs.firefox-unwrapped {
    extraPolicies = {
      DisableTelemetry = true;
      DisableFirefoxStudies = true;
      DisablePocket = true;
      DisableFirefoxAccounts = true;
      DisableAppUpdate = true;
      DisableSystemAddonUpdate = true;
      DisableFeedbackCommands = true;
      DisableDeveloperTools = true;
      DisableFormHistory = true;
      DisableSetDesktopBackground = true;
      DontCheckDefaultBrowser = true;
      NoDefaultBookmarks = true;
      DisplayBookmarksToolbar = "never";
      OverrideFirstRunPage = "";
      OverridePostUpdatePage = "";
      PasswordManagerEnabled = false;
      OfferToSaveLogins = false;
      SearchSuggestEnabled = false;
      NetworkPrediction = false;
      CaptivePortal = false;
      BlockAboutConfig = true;
      BlockAboutProfiles = true;
      BlockAboutAddons = true;
      BlockAboutSupport = true;
      ExtensionSettings."*".installation_mode = "blocked";
      DNSOverHTTPS = {
        Enabled = false;
        Locked = true;
      };
      UserMessaging = {
        ExtensionRecommendations = false;
        FeatureRecommendations = false;
        UrlbarInterventions = false;
        SkipOnboarding = true;
        MoreFromMozilla = false;
        FirefoxLabs = false;
        Locked = true;
      };
      Proxy = {
        Mode = "manual";
        Locked = true;
        HTTPProxy = proxy;
        UseHTTPProxyForAllProtocols = true;
        Passthrough = "127.0.0.1";
      };
      # Only the sign-in pages can be shown (frames included); the answer comes back on loopback.
      WebsiteFilter = {
        Block = [ "<all_urls>" ];
        Exceptions = pages ++ [ "http://127.0.0.1/*" ];
      };
    };
    # Background services that would only knock on the proxy's closed doors.
    extraPrefs = ''
      lockPref("browser.safebrowsing.malware.enabled", false);
      lockPref("browser.safebrowsing.phishing.enabled", false);
      lockPref("browser.safebrowsing.downloads.enabled", false);
      lockPref("app.normandy.enabled", false);
      lockPref("network.connectivity-service.enabled", false);
      lockPref("network.dns.disablePrefetch", true);
      lockPref("network.prefetch-next", false);
      lockPref("network.proxy.allow_hijacking_localhost", false);
      lockPref("security.OCSP.enabled", 0);
      lockPref("signon.rememberSignons", false);
      lockPref("browser.aboutwelcome.enabled", false);
    '';
  };
in
{
  options.foyelle.signin = {
    proxyAddress = lib.mkOption {
      type = lib.types.strMatching "127\\.0\\.1\\.[0-9]{1,3}";
      default = "127.0.1.101"; # next to the harness's (.100), clear of connector indexes
      description = "Loopback address of the sign-in browser's egress proxy.";
    };
    googleHosts = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [
        "accounts.google.com"
        "accounts.youtube.com"
        "www.google.com" # reCAPTCHA, when Google asks
        "apis.google.com"
        "ssl.gstatic.com"
        "www.gstatic.com"
        "fonts.gstatic.com"
        "fonts.googleapis.com"
        "lh3.googleusercontent.com" # the account picture
        "play.google.com" # the sign-in page's own logging; harmless to refuse, quieter to allow
      ];
      description = "Hosts the sign-in browser may reach for Google's sign-in (HTTPS only); services add their own.";
    };
    browser = lib.mkOption {
      type = lib.types.package;
      default = firefox;
      defaultText = lib.literalMD "Firefox with Foyelle's locked policies";
      description = "The sign-in browser. It must take Firefox's command line (--kiosk, --profile).";
    };
  };

  config = lib.mkIf on {
    # The proxy lets through Google's hosts and the sign-in hosts of the services the person
    # connected only: foyelle-services-sync writes this filter (and restarts the proxy). Firefox's
    # own allowed pages name every directory service's sign-in page, since its policies are fixed
    # when it is built; the proxy is what keeps the others closed.
    foyelle.egressProxies.signin = {
      address = scfg.proxyAddress;
      inherit hosts;
      ports = [ 443 ];
      filterFile = "/run/foyelle/signin.filter";
    };
    environment.etc."foyelle/signin-base.filter".text = lib.concatMapStrings (h: "^${lib.escapeRegex h}$\n") (
      lib.optionals googleOn scfg.googleHosts
    );
    systemd.services.foyelle-egress-signin = {
      wants = [ "foyelle-services-sync.service" ];
      after = [ "foyelle-services-sync.service" ];
    };

    services.cage.environment = {
      FOYELLE_SIGNIN_BROWSER = "${scfg.browser}/bin/firefox";
      FOYELLE_SIGNIN_PROXY = proxy;
      # The sign-in pages the canvas may open, and the services the Accounts card lists.
      FOYELLE_SIGNIN_PAGES = builtins.toJSON (
        lib.optional googleOn "https://accounts.google.com/o/oauth2/v2/auth"
        ++ lib.mapAttrsToList (_: b: b.signin.page) services
      );
      FOYELLE_SERVICES = builtins.toJSON (lib.mapAttrs (_: b: b.title) services);
    };

    systemd.services."cage-tty1" = {
      wants = [ "foyelle-egress-signin.service" ];
      after = [ "foyelle-egress-signin.service" ];
      serviceConfig.IPAddressAllow = [ "${scfg.proxyAddress}/32" ];
    };
  };
}
