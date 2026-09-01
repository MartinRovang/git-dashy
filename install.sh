#!/usr/bin/env sh
# Clone (or update) github-dashy and link it onto PATH as `prs`.
# ponytail: a clone, not a copied file — the in-app updater is just `git pull`.
set -e
REPO=${REPO:-https://github.com/MartinRovang/github-dashy.git}
DIR=${DIR:-$HOME/.github-dashy}
BIN=${BIN:-$HOME/.local/bin}

if [ -d "$DIR/.git" ]; then git -C "$DIR" pull --ff-only -q; else git clone -q "$REPO" "$DIR"; fi
mkdir -p "$BIN"
ln -sf "$DIR/prs.py" "$BIN/prs"
chmod +x "$DIR/prs.py"

echo "installed: $BIN/prs -> $DIR/prs.py"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "add to your shell rc:  export PATH=\"$BIN:\$PATH\"" ;; esac
command -v gh >/dev/null || echo "note: gh is required — https://cli.github.com"
