{ pkgs, ... }:

{
  languages.python = {
    enable = true;
    package = pkgs.python312;
    uv = {
      enable = true;
      sync = {
        enable = true;
        allPackages = true;
        allExtras = true;
      };
    };
  };

  packages = [ pkgs.python312Packages.invoke ];
}
