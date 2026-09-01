#!/usr/bin/env sh
# Clone (or update) github-dashy and link it onto PATH as `gitdashy` (and `prs`).
# ponytail: a clone, not a copied file — the in-app updater is just `git pull`.
set -e
REPO=${REPO:-https://github.com/MartinRovang/github-dashy.git}
DIR=${DIR:-$HOME/.github-dashy}
BIN=${BIN:-$HOME/.local/bin}
NAME=${NAME:-gitdashy}

[ -d "$DIR/.git" ] || git clone -q "$REPO" "$DIR"
git -C "$DIR" fetch --tags -q
TAG=$(git -C "$DIR" tag -l 'v*' --sort=-v:refname | head -1)   # newest release, or main if untagged
git -C "$DIR" checkout -q "${TAG:-main}"
mkdir -p "$BIN"
ln -sf "$DIR/prs.py" "$BIN/$NAME"
ln -sf "$DIR/prs.py" "$BIN/prs"   # ponytail: keep the old name working for existing installs
chmod +x "$DIR/prs.py"

cat <<BANNER

  ✓ github-dashy ${TAG:-main} installed

    You can access it with  gitdashy  in your terminal.

      gitdashy          your PRs, review-requested, assigned
      gitdashy --demo   try it with canned data (no gh, no claude)
      gitdashy --help   all flags and keys

BANNER
case ":$PATH:" in
	*":$BIN:"*) ;;
	*) echo "    ⚠  $BIN is not on your PATH — add this to your shell rc:"
	   echo "         export PATH=\"$BIN:\$PATH\""
	   echo "" ;;
esac
command -v gh >/dev/null || { echo "    ⚠  gh is required: https://cli.github.com"; echo ""; }
