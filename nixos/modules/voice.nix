# Talking to Foyelle: speech to text on this computer. The canvas records while the user holds the
# microphone and sends the audio here, on loopback; the words come back and go to Foyelle like
# typed ones. No audio and no text leaves the machine: the service has no network beyond loopback,
# and the model is part of the image.
#
# The model is Moondream's Parakeet Ultra (September 2026, CC-BY-4.0), post-trained from NVIDIA's
# Parakeet TDT 0.6B v3: 25 European languages, detected by itself, with punctuation. It runs as
# int8 ONNX on sherpa-onnx, about 0.15 s for a spoken request on four cores.
{
  config,
  lib,
  pkgs,
  foyellePkgs,
  ...
}:
let
  cfg = config.foyelle;
  vcfg = cfg.voice;
  # The sherpa-onnx export of Parakeet Ultra, pinned to a commit of its Hugging Face repository,
  # each file by its SHA-256 (the repository's SHA256SUMS and LFS ids agree).
  modelRepo = "leogaudin/sherpa-onnx-nemo-parakeet-ultra-int8";
  modelRev = "f7bc472677ff70be95cdb0ef9df509fee6f5ec0b";
  modelFiles = {
    "encoder.int8.onnx" = "8bd3b8dc07056def6dfd19910e9f90c9e1a4e7e20901f58bdfe2a570a8081a2a";
    "decoder.int8.onnx" = "94373c6405c8149d1c9d1e9862514e0e3e88dab1c7de18fac9d5df9c6ba5efdd";
    "joiner.int8.onnx" = "8a71aaccfdba3d451775507a889afd2257d2e754463fd81095a8c8f290f924c2";
    "tokens.txt" = "d58544679ea4bc6ac563d1f545eb7d474bd6cfa467f0a6e2c1dc1c7d37e3c35d";
  };
  model = pkgs.linkFarm "parakeet-ultra-int8" (
    lib.mapAttrsToList (name: sha256: {
      inherit name;
      path = pkgs.fetchurl {
        name = "parakeet-ultra-${name}";
        url = "https://huggingface.co/${modelRepo}/resolve/${modelRev}/${name}";
        inherit sha256;
      };
    }) modelFiles
  );
  # The server (foyelle_connectors.speech) with sherpa-onnx, which the connectors themselves don't need.
  python = pkgs.python3.withPackages (ps: [
    (ps.toPythonModule foyellePkgs.connectors)
    ps.sherpa-onnx
  ]);
in
{
  options.foyelle.voice = {
    enable = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Speech to text on this computer, for the canvas's microphone button.";
    };
    port = lib.mkOption {
      type = lib.types.port;
      default = 7310;
      description = "Loopback port of the speech service.";
    };
    threads = lib.mkOption {
      type = lib.types.ints.positive;
      default = 4;
      description = "CPU threads the speech model may use while it writes out a recording.";
    };
  };

  config = lib.mkIf (cfg.enable && vcfg.enable) {
    systemd.services.foyelle-voice = {
      description = "Foyelle speech to text (Parakeet Ultra on sherpa-onnx, loopback only)";
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        ExecStart = lib.escapeShellArgs [
          "${python}/bin/python"
          "-m"
          "foyelle_connectors.speech"
          "--host"
          "127.0.0.1"
          "--port"
          (toString vcfg.port)
          "--model-dir"
          "${model}"
          "--threads"
          (toString vcfg.threads)
        ];
        DynamicUser = true;
        Restart = "on-failure";
        RestartSec = 2;
        # Loopback only: the audio it hears never goes anywhere else.
        IPAddressDeny = "any";
        IPAddressAllow = [
          "127.0.0.1/32"
          "::1/128"
        ];
        RestrictAddressFamilies = [
          "AF_INET"
          "AF_INET6"
          "AF_UNIX"
        ];
        MemoryMax = "2G";
        CapabilityBoundingSet = "";
        NoNewPrivileges = true;
        PrivateTmp = true;
        PrivateDevices = true;
        ProtectHome = true;
        ProtectSystem = "strict";
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        ProtectClock = true;
        ProtectHostname = true;
        LockPersonality = true;
        RestrictNamespaces = true;
        RestrictRealtime = true;
        SystemCallArchitectures = "native";
        UMask = "0077";
      };
    };

    # The microphone reaches the canvas through PipeWire (Chromium speaks PulseAudio to it).
    services.pipewire = {
      enable = true;
      alsa.enable = true;
      pulse.enable = true;
    };
    security.rtkit.enable = true;
    users.users.${cfg.canvas.user}.extraGroups = [ "audio" ];

    services.cage.environment.FOYELLE_VOICE_URL = "http://127.0.0.1:${toString vcfg.port}/inference";
  };
}
