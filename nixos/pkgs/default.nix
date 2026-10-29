# Foyelle packages. `harness` is upstream Hermes at the pinned commit with harness/patches
# applied, built by upstream's own nix/hermes-agent.nix from the patched tree.
{
  pkgs,
  inputs,
  self,
}:
let
  inherit (pkgs) lib;
  upstream = lib.importJSON ../../harness/upstream.json;
  series = lib.filter (line: line != "") (
    lib.splitString "\n" (builtins.readFile ../../harness/patches/series)
  );
  system = pkgs.stdenv.hostPlatform.system;
in
rec {
  harness-src = pkgs.applyPatches {
    name = "foyelle-harness-src";
    src = inputs.hermes-upstream;
    patches = map (name: ../../harness/patches + "/${name}") series;
  };

  harness = pkgs.callPackage "${harness-src}/nix/hermes-agent.nix" {
    inherit (inputs) uv2nix pyproject-nix pyproject-build-systems;
    npm-lockfile-fix = inputs.npm-lockfile-fix.packages.${system}.default;
    inherit (upstream) rev;
    # The tools pinned in upstream's pm/lock.json, as upstream's nix/packages.nix builds them.
    # Hermes puts ffmpeg on its PATH only for voice mode, text-to-speech, transcription and
    # messaging-platform audio, all off in Foyelle, so it is left out. Those tools degrade
    # cleanly without it.
    pmPackages =
      lib.filterAttrs (_: lib.isDerivation) (pkgs.callPackage "${harness-src}/nix/pm-packages.nix" { })
      // {
        ffmpeg = pkgs.runCommand "ffmpeg-omitted" { } "mkdir -p $out/bin";
      };
  };

  # The policy engine and audit log, laid out as a Hermes directory plugin.
  policy-plugin = pkgs.runCommand "foyelle-policy-plugin" { } ''
    mkdir -p $out/foyelle-policy
    cp ${../../harness/plugin/foyelle_policy}/*.py ${../../harness/plugin/foyelle_policy}/plugin.yaml $out/foyelle-policy/
  '';

  apply-helper = pkgs.writers.writePython3Bin "foyelle-apply" {
    # E265: the writer adds its own shebang, which turns the file's into a comment.
    flakeIgnore = [
      "E501"
      "E265"
    ];
  } (builtins.readFile ../../system/apply-helper/foyelle_apply.py);

  connectors = pkgs.callPackage ./connectors.nix { };

  canvas = pkgs.callPackage ./canvas.nix { };

  # Third-party MCP Apps, packaged from pinned releases.
  system-monitor = pkgs.callPackage ./system-monitor.nix { };

  # Merges Foyelle's enforced keys into the harness config.yaml at every start.
  config-merge = pkgs.writers.writePython3Bin "foyelle-config-merge" {
    libraries = [ pkgs.python3Packages.pyyaml ];
    flakeIgnore = [ "E501" ];
  } (builtins.readFile ./config-merge.py);

  # The flake source, so the on-device config repo can build without fetching it.
  source = self.outPath;
}
