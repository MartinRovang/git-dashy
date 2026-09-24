{
  description = "github-dashy: smarter reviews, better code";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  # The release binary links the system webview at FHS paths, and the app updates itself by writing
  # over its own executable, which the Nix store forbids. So the package is a launcher: on first run
  # it downloads the release binary into ~/.local/lib/gitdashy and runs it in an FHS env that carries
  # the webview. Updates then write that file, as on any other distro.
  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};

      fhs = pkgs.buildFHSEnv {
        name = "gitdashy";
        targetPkgs = p: with p; [
          webkitgtk_4_1 gtk3 glib cairo gdk-pixbuf libsoup_3 dbus
          gsettings-desktop-schemas git curl coreutils
        ];
        runScript = pkgs.writeShellScript "gitdashy-run" ''
          set -e
          dir="''${GITDASHY_LIB_DIR:-$HOME/.local/lib/gitdashy}"
          app="$dir/gitdashy"
          if [ ! -x "$app" ]; then
            echo "gitdashy: first run, downloading the app" >&2
            mkdir -p "$dir"
            url=https://github.com/NeoMedSys/git-dashy/releases/latest/download/gitdashy-linux-x86_64
            curl -fsSL --retry 3 -o "$app.download" "$url"
            want=$(curl -fsSL --retry 2 "$url.sha256" | cut -d' ' -f1)
            [ "$(sha256sum "$app.download" | cut -d' ' -f1)" = "$want" ] \
              || { rm -f "$app.download"; echo "gitdashy: checksum mismatch, not installed" >&2; exit 1; }
            chmod +x "$app.download"
            mv -f "$app.download" "$app"
          fi
          exec "$app" "$@"
        '';
      };

      # the app menu starts us with no shell, so no GH_TOKEN from the user's rc: go through one (as install.sh does)
      launch = pkgs.writeShellScriptBin "gitdashy-launch" ''
        case ''${SHELL##*/} in
          bash|zsh|fish) exec "$SHELL" -ic 'exec ${fhs}/bin/gitdashy' ;;
        esac
        exec ${fhs}/bin/gitdashy
      '';

      desktop = pkgs.makeDesktopItem {
        name = "github-dashy";
        desktopName = "github-dashy";
        comment = "Your PRs, review-requested, assigned";
        exec = "gitdashy-launch";
        icon = "gitdashy";
        categories = [ "Development" ];
        startupWMClass = "gitdashy";
      };

      icon = pkgs.runCommand "gitdashy-icon" { } ''
        install -Dm644 ${./src-tauri/icons/128x128.png} $out/share/icons/hicolor/128x128/apps/gitdashy.png
        install -Dm644 ${./src-tauri/icons}/128x128@2x.png $out/share/icons/hicolor/256x256/apps/gitdashy.png
      '';
    in
    {
      packages.${system}.default = pkgs.symlinkJoin {
        name = "gitdashy";
        paths = [ fhs launch desktop icon ];
        meta = {
          description = "Your PRs, review-requested, assigned";
          platforms = [ system ];
          mainProgram = "gitdashy";
        };
      };

      apps.${system}.default = {
        type = "app";
        program = "${self.packages.${system}.default}/bin/gitdashy";
      };
    };
}
