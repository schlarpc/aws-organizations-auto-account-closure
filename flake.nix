{
  outputs = { self, nixpkgs }: {
    packages.x86_64-linux.default = (let
      pkgs = nixpkgs.legacyPackages.x86_64-linux;
      python = pkgs.python3.withPackages (ps:
        with ps;
        (pkgs.lib.attrValues rec {
          troposphere = (buildPythonPackage rec {
            pname = "troposphere";
            version = "4.1.0";
            src = fetchPypi {
              inherit pname version;
              sha256 =
                "ed203c12ef0ffdbe3ba4388ec95db9831c76e44e673f4cb0f7a091a5bd98d416";
            };
            propagatedBuildInputs = [ cfn-flip awacs ];
            doCheck = false;
          });
          awacs = (buildPythonPackage rec {
            pname = "awacs";
            version = "2.2.0";
            src = fetchPypi {
              inherit pname version;
              sha256 =
                "cd64501f18c7a20992292aa7bd02c909da229643a5274ee9d3494df94e3a5a45";
            };
          });
        }));
      app = pkgs.writeShellApplication {
        name = "aws-organizations-auto-account-closure";
        text = ''
          ${python}/bin/python3 ${./create_template.py} "$@"
        '';
      };
    in app);
  };
}
