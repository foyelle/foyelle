# The official MCP Apps example "system monitor" (github.com/modelcontextprotocol/ext-apps,
# examples/system-monitor-server, MIT), as published on npm: a bun-bundled server and a
# single-file view, so it runs on Node with no node_modules and fetches nothing at run time.
{
  stdenvNoCC,
  fetchurl,
  nodejs,
  writeShellScript,
}:
stdenvNoCC.mkDerivation (finalAttrs: {
  pname = "mcp-server-system-monitor";
  version = "2.0.3";
  src = fetchurl {
    url = "https://registry.npmjs.org/@modelcontextprotocol/server-system-monitor/-/server-system-monitor-${finalAttrs.version}.tgz";
    hash = "sha512-vrpj3fM+uG3php2+EvN8tycuRDuTcFic7N3qMvUVwI3wjj2F1ECNBBEshZEHQSFURLOfoV+AKaBoXD1e0FY5NQ==";
  };
  sourceRoot = "package";
  dontBuild = true;
  installPhase = ''
    mkdir -p $out/lib $out/bin
    cp -r dist LICENSE* README* package.json $out/lib/ 2>/dev/null || cp -r dist package.json $out/lib/
    # Foyelle's connector template gives the port as FOYELLE_CONNECTOR_PORT; the server reads PORT.
    install -m 0755 ${writeShellScript "foyelle-connector-sysmon" ''
      export PORT="''${FOYELLE_CONNECTOR_PORT:-3001}"
      exec ${nodejs}/bin/node @out@/lib/dist/index.js
    ''} $out/bin/foyelle-connector-sysmon
    substituteInPlace $out/bin/foyelle-connector-sysmon --replace-fail @out@ $out
  '';
  meta.description = "MCP Apps example: live CPU and memory charts";
})
