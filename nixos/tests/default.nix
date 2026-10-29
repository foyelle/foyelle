# Checks: unit tests of every Foyelle component, the egress guard on the patched harness source,
# and NixOS VM tests for the milestone gates (run in CI on x86_64-linux with KVM).
{
  pkgs,
  inputs,
  self,
  foyelle,
}:
let
  inherit (pkgs) lib;
  py = pkgs.python3.withPackages (ps: [ ps.pytest ]);
  testPython = pkgs.python3.withPackages (ps: [
    ps.websockets
    ps.mcp
    ps.httpx
  ]);

  unit = {
    policy-plugin = pkgs.runCommand "foyelle-policy-plugin-tests" { nativeBuildInputs = [ py ]; } ''
      cp -r ${../../harness/plugin} plugin && chmod -R u+w plugin && cd plugin
      python -m pytest -q -p no:cacheprovider tests
      touch $out
    '';

    apply-helper = pkgs.runCommand "foyelle-apply-helper-tests" {
      nativeBuildInputs = [
        py
        pkgs.git
      ];
    } ''
      cp -r ${../../system/apply-helper} helper && chmod -R u+w helper && cd helper
      export HOME=$TMPDIR
      python -m pytest -q -p no:cacheprovider
      touch $out
    '';

    egress-guard = pkgs.runCommand "foyelle-egress-guard" { nativeBuildInputs = [ pkgs.python3 ]; } ''
      mkdir -p repo/scripts repo/harness
      cp ${../../scripts/egress-guard} repo/scripts/egress-guard
      cp ${../../harness/egress-baseline.json} repo/harness/egress-baseline.json
      python3 repo/scripts/egress-guard ${foyelle.harness-src}
      touch $out
    '';

    config-merge = pkgs.runCommand "foyelle-config-merge-tests" {
      nativeBuildInputs = [ (pkgs.python3.withPackages (ps: [ ps.pyyaml ])) ];
    } ''
      python3 ${../pkgs/config-merge-test.py} ${../pkgs/config-merge.py}
      touch $out
    '';

    # The release VM must never carry the developer access that scripts/deploy-vm uses.
    release-no-ssh =
      let
        release = self.nixosConfigurations."alpha-vm-${pkgs.stdenv.hostPlatform.system}".config;
      in
      assert lib.assertMsg (
        !release.services.openssh.enable && !release.foyelle.dev.enable
      ) "the release VM configuration has SSH or developer access on";
      pkgs.runCommand "foyelle-release-no-ssh" { } "touch $out";

    # Unit tests run in the package builds (pytestCheckHook, npm test).
    inherit (foyelle) connectors canvas;
  };

  # Shared NixOS test settings: Foyelle plus a scripted model on a second node.
  base =
    { config, ... }:
    {
      imports = [ self.nixosModules.default ];
      foyelle.enable = true;
      foyelle.canvas.softwareRendering = true;
      boot.loader.systemd-boot.bootCounting.enable = false; # test VMs boot without a bootloader
      virtualisation = {
        memorySize = 4096;
        cores = 2;
        diskSize = 4096;
      };
      environment.systemPackages = [
        testPython
        pkgs.curl
        pkgs.jq
      ];
    };

  modelNode =
    { ... }:
    {
      networking.firewall.allowedTCPPorts = [ 11434 ];
      systemd.services.fake-model = {
        wantedBy = [ "multi-user.target" ];
        script = ''
          mkdir -p /var/lib/fake-model
          [ -f /var/lib/fake-model/script.json ] || echo '[]' > /var/lib/fake-model/script.json
          exec ${pkgs.python3}/bin/python3 ${./fake-model.py} 11434 /var/lib/fake-model/script.json
        '';
      };
    };

  modelSettings =
    { nodes, ... }:
    let
      ip = nodes.model.networking.primaryIPAddress;
    in
    {
      foyelle.models = {
        remote = {
          provider = "custom";
          model = "fake";
        };
        local = {
          host = ip;
          baseUrl = "http://${ip}:11434/v1";
          model = "fake";
        };
      };
      foyelle.harness.extraSettings.model = {
        provider = "custom";
        default = "fake";
        base_url = "http://${ip}:11434/v1";
        api_key = "test";
      };
    };

  runTest =
    test:
    pkgs.testers.runNixOSTest (
      test
      // {
        node.specialArgs = { inherit inputs self; };
      }
    );

  vmTests = {
    # M1 gate: the image boots straight into the canvas under cage, and the canvas reaches the
    # harness gateway with the per-boot token; nothing else may.
    boot-canvas = runTest {
      name = "foyelle-boot-canvas";
      nodes.machine = {
        imports = [ base ];
        # As on the UTM image: the canvas on cage's Xwayland display with the SPICE clipboard agent.
        foyelle.canvas.hostClipboard = true;
        services.spice-vdagentd.enable = true;
      };
      testScript = ''
        machine.wait_for_unit("foyelle-harness.service")
        # wait_for_open_port probes as root, which the firewall refuses on the gateway port by design.
        machine.wait_until_succeeds("ss -ltn | grep -q 127.0.0.1:9119", timeout=300)
        machine.wait_for_unit("cage-tty1.service")
        machine.wait_until_succeeds("pgrep -u foyelle -f foyelle-canvas", timeout=120)
        # The canvas itself is connected to the gateway (not only reachable by a test client).
        machine.wait_until_succeeds("ss -tnpH state established '( dport = :9119 )' | grep -q electron", timeout=120)

        # No window manager, display manager or launcher on the system.
        machine.fail("pgrep -x 'Xorg|sway|gnome-shell|plasmashell|weston|labwc'")
        machine.fail("systemctl list-unit-files display-manager.service | grep -q enabled")

        # The canvas user reaches the gateway with the token; root and other users are refused.
        machine.succeed("su -s /bin/sh foyelle -c '${testPython}/bin/python3 ${./gateway-client.py} ping'")
        machine.fail("${testPython}/bin/python3 ${./gateway-client.py} ping")
        machine.succeed("stat -c '%a %G' /run/foyelle/harness-token | grep -q '640 foyelle-gateway'")

        # Watchdog: kill the canvas, it comes back.
        machine.succeed("pkill -u foyelle -f foyelle-canvas")
        machine.wait_until_succeeds("pgrep -u foyelle -f foyelle-canvas", timeout=60)
        machine.screenshot("canvas")
      '';
    };

    # M3 gate: every connector runs isolated from the others and from the network.
    isolation = runTest {
      name = "foyelle-isolation";
      nodes.machine =
        { pkgs, ... }:
        {
          imports = [ base ];
          foyelle.connectors.web.egressHosts = [ "allowed.example" ];
          # Two probe connectors built from the same template, to test it from the inside.
          foyelle.connectors.probe-a = {
            enable = true;
            package = pkgs.writeShellApplication {
              name = "foyelle-connector-probe-a";
              runtimeInputs = [
                pkgs.curl
                pkgs.coreutils
                pkgs.python3
              ];
              text = builtins.readFile ./probe.sh;
            };
            port = 7390;
            index = 90;
            egressHosts = [ "allowed.example" ];
            credentials.secret = "/etc/foyelle-test-secret-a";
          };
          foyelle.connectors.probe-b = {
            enable = true;
            package = pkgs.writeShellApplication {
              name = "foyelle-connector-probe-b";
              text = "echo b-private > \"$STATE_DIRECTORY/private\"; exec sleep infinity";
              runtimeInputs = [ pkgs.coreutils ];
            };
            port = 7391;
            index = 91;
            egressHosts = [ "other.example" ];
            credentials.secret = "/etc/foyelle-test-secret-b";
          };
          environment.etc."foyelle-test-secret-a".text = "secret-a";
          environment.etc."foyelle-test-secret-b".text = "secret-b";
        };
      testScript = ''
        import json
        machine.wait_for_unit("multi-user.target")
        for unit in ["shell", "files", "web", "system", "probe-a", "probe-b"]:
            machine.wait_for_unit(f"foyelle-connector-{unit}.service")
        machine.wait_until_succeeds("test -f /var/lib/private/foyelle/connectors/probe-a/results.json", timeout=120)
        results = json.loads(machine.succeed("cat /var/lib/private/foyelle/connectors/probe-a/results.json"))
        print(json.dumps(results, indent=1))
        expect = {
            "own_secret": "ok", "other_secret": "denied", "other_state": "denied", "harness_state": "denied",
            "home_empty": "ok", "connector_port": "denied", "gateway_port": "denied",
            "own_proxy_allowed_host": "ok", "own_proxy_denied_host": "denied",
            "other_proxy": "denied", "direct_internet": "denied",
        }
        bad = {k: (v, results.get(k)) for k, v in expect.items() if results.get(k) != v}
        assert not bad, f"isolation failures: {bad}"

        # Each real connector runs as its own dynamic user (shell runs as the harness by design).
        for unit in ["files", "web", "system"]:
            machine.succeed(f"systemctl show -p DynamicUser foyelle-connector-{unit} | grep -q yes")
            machine.succeed(f"systemctl show -p IPAddressDeny foyelle-connector-{unit} | grep -q '0.0.0.0/0'")
        # The harness user reaches a connector; root does not.
        machine.succeed("su -s /bin/sh foyelle-harness -c 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:7301/mcp' | grep -qv '^000$'")
        machine.fail("curl -s --max-time 3 http://127.0.0.1:7301/mcp")
        policy = json.loads(machine.succeed("cat /etc/foyelle/policy.json"))
        assert policy["connectors"]["web"]["sensitivity"] == "public"
        assert policy["connectors"]["system"]["tools"]["apply"]["tier"] == "system"
      '';
    };

    # M2 + M3 + egress gates, end to end: a chat turn through the real harness reaches a
    # connector, the tool's surface opens with its HTML, the surface calls a tool through the
    # host, the audit log records it all, and nothing left the machine.
    end-to-end = runTest {
      name = "foyelle-end-to-end";
      nodes.model = modelNode;
      nodes.machine =
        { nodes, config, ... }:
        {
          imports = [
            base
            (modelSettings { inherit nodes; })
          ];
          # Log every name the resolver looks up, so the test can show none is a Nous host.
          systemd.services.systemd-resolved.environment.SYSTEMD_LOG_LEVEL = "debug";
          networking.nftables.tables.egress-watch = {
            family = "inet";
            content = ''
              chain output {
                type filter hook output priority -10; policy accept;
                oifname "lo" accept
                ip daddr ${nodes.model.networking.primaryIPAddress} tcp dport 11434 accept
                # New TCP/UDP flows from processes; kernel traffic (neighbour discovery) has no owner.
                meta l4proto != { tcp, udp } accept
                meta skuid 0 accept
                # Platform DNS and NTP (their lookups are checked by name below).
                meta skuid { ${toString config.ids.uids.systemd-resolve}, ${toString config.ids.uids.systemd-timesync} } accept
                ct state new counter log prefix "foyelle-egress: "
              }
            '';
          };
        };
      testScript = ''
        import json
        import re
        start_all()
        model.wait_for_unit("fake-model.service")
        model.wait_for_open_port(11434)
        machine.wait_for_unit("foyelle-harness.service")
        # wait_for_open_port probes as root, which the firewall refuses on the gateway port by design.
        machine.wait_until_succeeds("ss -ltn | grep -q 127.0.0.1:9119", timeout=300)
        machine.wait_for_unit("foyelle-connector-files.service")
        machine.succeed("echo 'quarterly notes' > /var/lib/foyelle/files/Documents/notes.txt")

        script = [{"tool_call": {"name": "mcp__files__browse", "arguments": {"path": "documents"}}},
                  {"text": "You have one document."}]
        model.succeed(f"echo '{json.dumps(script)}' > /var/lib/fake-model/script.json")
        machine.succeed("""echo '{"path": "documents/notes.txt"}' > /tmp/read-args.json && chmod 644 /tmp/read-args.json""")

        client = "su -s /bin/sh foyelle -c '${testPython}/bin/python3 ${./gateway-client.py} {}'"
        out = json.loads(machine.succeed(client.format(
            'chat \"what is in my documents\" --expect-surface --call-surface-tool read @/tmp/read-args.json')))
        print(out)
        assert out["ok"] and out["reply"] == "You have one document.", out
        assert out["surfaces"] and out["surfaces"][0]["uri"].startswith("ui://files/"), out
        assert "quarterly notes" in json.dumps(out["surface_call"]), out

        # System surfaces open without a tool call.
        listed = json.loads(machine.succeed(client.format("list-surfaces")))
        uris = {s["uri"] for s in listed["surfaces"]}
        assert {"ui://shell/status", "ui://shell/memory", "ui://shell/audit"} <= uris, uris

        # The audit log has the model's call and the surface's call.
        audit = [json.loads(l) for l in machine.succeed("cat /var/lib/foyelle/audit/audit.jsonl").splitlines()]
        origins = {(r["tool"], r["origin"]) for r in audit}
        assert ("mcp__files__browse", "model") in origins and ("mcp__files__read", "surface") in origins, origins

        # Every name looked up on the machine, and none of them is a Nous host.
        lookups = sorted(set(re.findall(r"Looking up RR for ([^ ]+) IN",
                                         machine.succeed("journalctl -u systemd-resolved --no-pager"))))
        print("DNS lookups:", lookups)
        # Everything in this test is reached by address, so any lookup at all is unexpected traffic
        # (a Nous host, or background calls such as Chromium's component updater). Loopback names
        # are the exception: resolved answers localhost and the reverse zones of 127.0.0.0/8 and
        # ::1 itself and never forwards them (a reverse lookup of 127.0.0.1 is not traffic).
        loopback_v6 = "1." + "0." * 31 + "ip6.arpa"
        def local_only(name):
            return (name in ("localhost", loopback_v6) or name.endswith(".localhost")
                    or name.endswith(".127.in-addr.arpa"))
        outside = [name for name in lookups if not local_only(name)]
        assert outside == [], f"unexpected DNS lookups: {outside}"

        # Nothing but the model endpoint was contacted by any other process.
        counters = machine.succeed("nft list table inet egress-watch")
        print(counters)
        assert "packets 0 bytes 0" in counters, "unexpected outbound traffic"
        machine.fail("journalctl -k | grep -q 'foyelle-egress:'")
      '';
    };

    # M4 gate: a proposal is applied through the helper with a one-time approval token, a broken
    # change rolls back automatically, and nothing is applied without a token.
    system-loop = runTest {
      name = "foyelle-system-loop";
      nodes.machine =
        { pkgs, ... }:
        let
          # Builds are replaced by prebuilt specialisations (a VM test cannot evaluate nixpkgs): a
          # commit maps to the specialisation named in its subject line. Both the connector's build
          # and the helper's own rebuild use it.
          fakeBuild = pkgs.writeShellScript "fake-build" ''
            subject=$(${pkgs.git}/bin/git -c safe.directory='*' -C /var/lib/foyelle/connectors/system/config show -s --format=%s "$1")
            readlink -f /run/booted-system/specialisation/"$subject"
          '';
        in
        {
          imports = [ base ];
          foyelle.system.buildCommand = toString fakeBuild;
          foyelle.connectors.system.environment.FOYELLE_NIX_BUILD_COMMAND = builtins.toJSON [
            (toString fakeBuild)
            "{rev}"
          ];
          foyelle.system.healthTimeout = 30;
          specialisation.good.configuration = {
            environment.etc."foyelle-generation".text = "good";
          };
          specialisation.bad.configuration = {
            environment.etc."foyelle-generation".text = "bad";
            systemd.services.foyelle-harness.serviceConfig.ExecStart = pkgs.lib.mkForce "${pkgs.coreutils}/bin/false";
            systemd.services.foyelle-harness.serviceConfig.Restart = pkgs.lib.mkForce "no";
          };
        };
      testScript = builtins.readFile ./system-loop.py;
    };

    # Disk encryption: unlock at boot, initial-passphrase warning, the approved passphrase prompt
    # on tty8 (typed on the keyboard, never through Foyelle), and the new passphrase at next boot.
    disk-encryption = runTest {
      name = "foyelle-disk-encryption";
      nodes.machine =
        { lib, ... }:
        {
          imports = [ base ];
          virtualisation = {
            emptyDiskImages = [ 2048 ];
            useBootLoader = true;
            useEFIBoot = true;
            # The encrypted root starts empty; the system comes from the host's store.
            mountHostNixStore = true;
          };
          boot.loader.systemd-boot.enable = true;
          environment.systemPackages = [ pkgs.cryptsetup ];
          specialisation.luks.configuration = {
            foyelle.disk = {
              encrypted = true;
              device = "/dev/vdb";
            };
            boot.initrd.luks.devices = lib.mkVMOverride { root.device = "/dev/vdb"; };
            virtualisation.rootDevice = "/dev/mapper/root";
          };
        };
      testScript =
        { nodes, ... }:
        let
          client = pkgs.writeText "helper-client.py" ''
            import socket, sys
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect("/run/foyelle/apply.sock")
            s.sendall(sys.argv[1].encode() + b"\n")
            print(s.makefile().readline().strip())
          '';
        in
        ''
          helper_client = "${client}"
          luks_system = "${nodes.machine.specialisation.luks.configuration.system.build.toplevel}"
        ''
        + builtins.readFile ./disk-encryption.py;
    };
  };
in
unit // lib.optionalAttrs pkgs.stdenv.hostPlatform.isx86_64 vmTests
