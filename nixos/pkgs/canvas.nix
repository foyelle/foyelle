# The Electron canvas (canvas/). Electron itself comes from nixpkgs at run time; the build only
# bundles the TypeScript with esbuild.
{
  lib,
  buildNpmPackage,
  nodejs,
}:
buildNpmPackage {
  pname = "foyelle-canvas";
  version = "0.1.0";
  inherit nodejs;
  src = lib.fileset.toSource {
    root = ../../canvas;
    fileset = lib.fileset.difference ../../canvas (
      lib.fileset.unions [
        (lib.fileset.maybeMissing ../../canvas/node_modules)
        (lib.fileset.maybeMissing ../../canvas/dist)
      ]
    );
  };
  npmDepsHash = "sha256-y5Mz3JUaxj8gHXBfQmxrUItNp4SPMCVb8QaD2LotPF0=";
  env.ELECTRON_SKIP_BINARY_DOWNLOAD = "1";
  npmBuildScript = "build";
  doCheck = true;
  checkPhase = ''
    runHook preCheck
    npm test
    runHook postCheck
  '';
  installPhase = ''
    runHook preInstall
    mkdir -p $out/lib/foyelle-canvas
    cp -r dist package.json $out/lib/foyelle-canvas/
    # Also as system fonts, so cards (sandboxed frames that can't load the canvas's files) get them.
    mkdir -p $out/share/fonts/truetype $out/share/doc/foyelle-canvas
    cp src/renderer/fonts/*.ttf $out/share/fonts/truetype/
    cp src/renderer/fonts/OFL-*.txt src/renderer/fonts/LICENSE-*.txt $out/share/doc/foyelle-canvas/
    runHook postInstall
  '';
  meta = {
    description = "Foyelle canvas: the full-screen shell and MCP Apps host";
    license = lib.licenses.mit;
  };
}
