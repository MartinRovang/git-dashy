#!/usr/bin/env sh
# Download the gitdashy binary for this machine onto PATH.
# ponytail: one static binary from the GitHub release, no clone, no runtime. The in-app updater
# does the same download over the running executable.
set -e
REPO=${REPO:-MartinRovang/github-dashy}
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

printf '\n  %s%sgithub-dashy%s %s: smarter reviews, better code%s\n\n' "$B" "$PINK" "$R" "$DIM" "$R"

# the asset name update::asset_name() and ci.yml agree on
case "$(uname -s)-$(uname -m)" in
	Linux-x86_64) ASSET=gitdashy-linux-x86_64 ;;
	Darwin-arm64) ASSET=gitdashy-macos-arm64 ;;
	Darwin-x86_64) ASSET=gitdashy-macos-x86_64 ;;
	*) printf '  no prebuilt binary for %s-%s; build with: cargo install --git https://github.com/%s\n' "$(uname -s)" "$(uname -m)" "$REPO"; exit 1 ;;
esac

# ponytail: REF installs a release by tag; default is the newest release.
if [ -n "${REF:-}" ]; then
	URL="https://github.com/$REPO/releases/download/$REF/$ASSET"
else
	URL="https://github.com/$REPO/releases/latest/download/$ASSET"
fi
mkdir -p "$BIN"
TMP="$BIN/.$NAME.download"
curl -fsSL --retry 3 -o "$TMP" "$URL"
chmod +x "$TMP"
mv -f "$TMP" "$BIN/$NAME"
TAG=$("$BIN/$NAME" --version 2>/dev/null | awk '{print $NF}')

row() { printf '    %s%-22s%s %s%s%s\n' "$CYAN" "$1" "$R" "$DIM" "$2" "$R"; }
warn() { printf '\n    %s⚠%s  %s\n' "$YELLOW" "$R" "$1"; }

printf '  %s✓%s installed %s%s%s  %s: %s%s\n\n' "$GREEN" "$R" "$B" "${TAG:-latest}" "$R" "$DIM" "$BIN/$NAME" "$R"
printf '  Run it with %s%s%s%s in your terminal:\n\n' "$B" "$PINK" "$NAME" "$R"
row "$NAME"          "the desktop dashboard: your PRs, review-requested, assigned"
row "$NAME --browser" "the same dashboard in your browser"
row "$NAME --demo"   "try it with canned data (no token, no claude)"
row "$NAME --help"   "all flags and subcommands"

case ":$PATH:" in
	*":$BIN:"*) ;;
	*) warn "$BIN is not on your PATH: add to your shell rc:"
	   printf '       %sexport PATH="%s:$PATH"%s\n' "$CYAN" "$BIN" "$R" ;;
esac
[ -n "${GH_TOKEN:-}${GITHUB_TOKEN:-}" ] || warn "no github token: export GH_TOKEN=... (scope: repo)"
printf '\n'
