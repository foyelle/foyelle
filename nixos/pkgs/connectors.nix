# The first-party MCP connectors (connectors/), built against nixpkgs' Python packages only.
{
  lib,
  python3,
  git,
  nvd,
  makeWrapper,
}:
let
  # caldav's HTTP client, niquests on urllib3-future, fails its own test suite at the pinned
  # nixpkgs (tests that reach SOCKS proxies and public hosts), so Hydra has no build of it and
  # every rebuild ran those tests for two hours. Build the two without their tests.
  python = python3.override {
    self = python;
    packageOverrides = _: prev: {
      urllib3-future = prev.urllib3-future.overridePythonAttrs { doCheck = false; };
      niquests = prev.niquests.overridePythonAttrs { doCheck = false; };
    };
  };
  python3Packages = python.pkgs;
in
python3Packages.buildPythonApplication {
  pname = "foyelle-connectors";
  version = "0.1.0";
  pyproject = true;
  src = lib.fileset.toSource {
    root = ../../connectors;
    fileset = lib.fileset.unions [
      ../../connectors/pyproject.toml
      ../../connectors/README.md
      ../../connectors/src
      ../../connectors/tests
    ];
  };
  build-system = [ python3Packages.hatchling ];
  dependencies = with python3Packages; [
    mcp
    httpx
    caldav
    icalendar
    uvicorn
    starlette
  ];
  nativeCheckInputs = [
    python3Packages.pytestCheckHook
    python3Packages.pytest-asyncio
    git
  ];
  nativeBuildInputs = [ makeWrapper ];
  # The System connector shells out to git and nvd (nix comes from the system PATH).
  postFixup = ''
    for bin in $out/bin/foyelle-connector-*; do
      wrapProgram "$bin" --prefix PATH : ${lib.makeBinPath [ git nvd ]}
    done
  '';
  pythonImportsCheck = [ "foyelle_connectors" ];
  meta = {
    description = "Foyelle first-party MCP connectors";
    license = lib.licenses.mit;
  };
}
