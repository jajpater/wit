{
  description = "wit — a git for documents";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        pythonPackages = pkgs.python3Packages;
      in
      {
        packages.default = pythonPackages.buildPythonApplication {
          pname = "wit";
          version = "0.0.0";
          src = ./.;

          # Gebruik het pyproject.toml build systeem
          pyproject = true;

          build-system = [
            pythonPackages.setuptools
          ];

          dependencies = [
            pythonPackages.blake3
          ];

          nativeCheckInputs = [
            pythonPackages.pytestCheckHook
          ];

          meta = with pkgs.lib; {
            description = "A git for documents: content-addressed storage with real files.";
            homepage = "https://github.com/jajpater/wit";
            license = licenses.mit;
            mainProgram = "wit";
          };
        };

        devShells.default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (ps: with ps; [
              blake3
              pytest
            ]))
            # Eventueel extra tools voor development
            pkgs.ruff
            pkgs.pyright
          ];
        };
      }
    );
}
