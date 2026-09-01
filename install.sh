#!/usr/bin/env sh
# Clone (or update) github-dashy and link it onto PATH as `gitdashy` (and `prs`).
# ponytail: a clone, not a copied file — the in-app updater is just `git pull`.
set -e
REPO=${REPO:-https://github.com/MartinRovang/github-dashy.git}
DIR=${DIR:-$HOME/.github-dashy}
BIN=${BIN:-$HOME/.local/bin}
NAME=${NAME:-gitdashy}

# ponytail: colours only when stdout is a terminal, empty strings otherwise
if [ -t 1 ]; then
	B=$(printf '\033[1m'); DIM=$(printf '\033[2m'); R=$(printf '\033[0m')
	PINK=$(printf '\033[38;5;213m'); CYAN=$(printf '\033[38;5;75m')
	GREEN=$(printf '\033[38;5;78m'); YELLOW=$(printf '\033[38;5;221m')
else
	B= DIM= R= PINK= CYAN= GREEN= YELLOW=
fi

printf '\n  %s%sgithub-dashy%s %s— smarter reviews, better code%s\n\n' "$B" "$PINK" "$R" "$DIM" "$R"

[ -d "$DIR/.git" ] || git clone -q "$REPO" "$DIR"
git -C "$DIR" fetch --tags -q
TAG=$(git -C "$DIR" tag -l 'v*' --sort=-v:refname | head -1)   # newest release, or main if untagged
git -C "$DIR" checkout -q "${TAG:-main}"
mkdir -p "$BIN"
ln -sf "$DIR/prs.py" "$BIN/$NAME"
ln -sf "$DIR/prs.py" "$BIN/prs"   # ponytail: keep the old name working for existing installs
chmod +x "$DIR/prs.py"

row() { printf '    %s%-22s%s %s%s%s\n' "$CYAN" "$1" "$R" "$DIM" "$2" "$R"; }
warn() { printf '\n    %s⚠%s  %s\n' "$YELLOW" "$R" "$1"; }

printf '  %s✓%s installed %s%s%s  %s→ %s%s\n\n' "$GREEN" "$R" "$B" "${TAG:-main}" "$R" "$DIM" "$DIR" "$R"
printf '  Run it with %s%s%s%s in your terminal:\n\n' "$B" "$PINK" "$NAME" "$R"
row "$NAME"          "your PRs, review-requested, assigned"
row "$NAME --demo"   "try it with canned data (no gh, no claude)"
row "$NAME --help"   "all flags and keys"

case ":$PATH:" in
	*":$BIN:"*) ;;
	*) warn "$BIN is not on your PATH — add to your shell rc:"
	   printf '       %sexport PATH="%s:$PATH"%s\n' "$CYAN" "$BIN" "$R" ;;
esac
command -v gh >/dev/null || warn "gh is required — https://cli.github.com"
printf '\n'
