{
  description = "Foyelle: an agent-native OS on a Hermes fork and NixOS (alpha)";

  inputs = {
    # Same nixpkgs and build inputs as the pinned Hermes upstream (harness/upstream.json),
    # so the harness builds exactly as upstream's own flake would.
    nixpkgs.url = "github:NixOS/nixpkgs/02c087f3fe673c9e317f206a627cb6b910336dbe";
    hermes-upstream = {
      url = "github:NousResearch/hermes-agent/edfa1c2a135532a8b64f7825c0ac0af6cfe73d4d";
      flake = false;
    };
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix/ddc400a6883b042646d0940e65971bb7f7e96107";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix/0fdaa03ea3c3c16dbb1fe4d69108f07227a4b42d";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs/a17819c85163988e063806ffc4f6d2716bc8ead8";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
    };
    npm-lockfile-fix = {
      url = "github:jeslie0/npm-lockfile-fix/c6093acb0c0548e0f9b8b3d82918823721930fe8";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    { self, nixpkgs, ... }@inputs:
    let
      inherit (nixpkgs) lib;
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = f: lib.genAttrs systems (system: f system nixpkgs.legacyPackages.${system});
      foyellePackages = pkgs: import ./nixos/pkgs { inherit pkgs inputs self; };
      mkImage =
        machine:
        let
          baseName = "foyelle-alpha-${machine.pkgs.stdenv.hostPlatform.system}";
        in
        if machine.config.foyelle.disk.encrypted then
          import ./nixos/image/luks-image.nix {
            inherit lib;
            inherit (machine) config pkgs;
            name = baseName;
            initialPassphrase = machine.config.foyelle.disk.initialPassphrase;
          }
        else
          import "${nixpkgs}/nixos/lib/make-disk-image.nix" {
            inherit lib baseName;
            inherit (machine) config pkgs;
            diskSize = "auto";
            format = "qcow2-compressed";
            partitionTableType = "efi";
            # Flakes only: a nixos channel would be another unreferenced nixpkgs copy (~200 MB).
            copyChannel = false;
          };
      # Developer keys come from the environment so they never enter the repository.
      devKeys = lib.filter (k: k != "") (lib.splitString "\n" (builtins.getEnv "FOYELLE_DEV_SSH_KEYS"));
      devSettings = {
        foyelle.dev = {
          enable = true;
          authorizedKeys = devKeys;
        };
        # Internet access goes through scripts/host-proxy on the Mac, which works when a VPN on
        # the Mac drops UTM's NAT traffic.
        foyelle.egressUpstream = "192.168.64.1:3129";
        # Builds of proposals fetch from cache.nixos.org through the same proxy (the daemon only:
        # nix.envVars would also export it to every shell).
        systemd.services.nix-daemon.environment = {
          https_proxy = "http://192.168.64.1:3129";
          http_proxy = "http://192.168.64.1:3129";
        };
        # The dev image stays unencrypted, so deploy-vm keeps matching the disk it was made with.
        foyelle.disk.encrypted = false;
        # The model already on the reference Mac (2.5 GB); the release default is qwen3.5:9b.
        foyelle.models.local.model = "qwen3:4b-instruct";
      };
      # The dev VM's config repo carries the same settings, so a change applied through the
      # System loop keeps the developer access instead of dropping it.
      devModule = lib.recursiveUpdate devSettings {
        foyelle.system.machineSettings = devSettings;
      };
    in
    {
      overlays.default = final: _prev: { foyelle = foyellePackages final; };

      packages = forAllSystems (
        system: pkgs:
        let
          p = foyellePackages pkgs;
        in
        {
          inherit (p)
            harness
            harness-src
            canvas
            connectors
            policy-plugin
            apply-helper
            ;
          # The developer variant: SSH for the keys in $FOYELLE_DEV_SSH_KEYS (needs --impure; see
          # scripts/deploy-vm). Build it once, then deploy changes without rebuilding the image.
          vm-image-dev = mkImage self.nixosConfigurations."alpha-vm-dev-${system}";
          # The alpha VM disk image (UEFI, compressed qcow2) for the matching architecture. The
          # qemu-efi image module only offers raw or qcow2, so call its builder directly with
          # qcow2-compressed: one pass, about a third of the size, no second full copy.
          vm-image = mkImage self.nixosConfigurations."alpha-vm-${system}";
          default = p.harness;
        }
      );

      nixosModules.default = import ./nixos/modules { inherit inputs self; };

      lib = {
        # A Foyelle system: the modules plus the alpha VM host. The on-device config repo
        # (nixos/config-template) calls this with the user's own configuration.nix.
        mkSystem =
          {
            system,
            modules ? [ ],
          }:
          lib.nixosSystem {
            inherit system;
            specialArgs = { inherit inputs self; };
            modules = [
              self.nixosModules.default
              ./nixos/hosts/alpha-vm.nix
            ]
            ++ modules;
          };
      };

      nixosConfigurations = {
        alpha-vm-aarch64-linux = self.lib.mkSystem { system = "aarch64-linux"; };
        alpha-vm-x86_64-linux = self.lib.mkSystem { system = "x86_64-linux"; };
        alpha-vm-dev-aarch64-linux = self.lib.mkSystem {
          system = "aarch64-linux";
          modules = [ devModule ];
        };
      };

      checks = forAllSystems (
        system: pkgs:
        import ./nixos/tests {
          inherit pkgs inputs self;
          foyelle = foyellePackages pkgs;
        }
      );

      formatter = forAllSystems (_: pkgs: pkgs.nixfmt-rfc-style);
    };
}
