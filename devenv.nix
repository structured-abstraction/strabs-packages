{ pkgs, ... }:

{
  languages.python = {
    enable = true;
    package = pkgs.python312;
    uv.enable = true;
  };

  packages = [
    pkgs.python312Packages.invoke
  ];
}
