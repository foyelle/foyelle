{
  description = "This machine's Foyelle configuration. The agent proposes changes as commits on proposal/* branches; you review and approve them on the System surface.";

  # The Foyelle release this machine runs, as a store path that is already on the machine.
  inputs.foyelle.url = "path:@source@";

  outputs =
    { foyelle, ... }:
    {
      nixosConfigurations.@host@ = foyelle.lib.mkSystem {
        system = "@system@";
        # machine.json: settings this machine was built with beyond the release (kept up to date
        # by Foyelle); configuration.nix: yours; services.json (when present): services you added
        # by URL (system.propose_service), yours too.
        modules =
          let
            machine = builtins.fromJSON (builtins.readFile ./machine.json);
          in
          [
            machine
            { foyelle.system.machineSettings = machine; }
            ./configuration.nix
          ]
          ++ (
            if builtins.pathExists ./services.json then
              [ { foyelle.customServices = (builtins.fromJSON (builtins.readFile ./services.json)).services or [ ]; } ]
            else
              [ ]
          );
      };
    };
}
