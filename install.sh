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
	*) printf '  no prebuilt binary for %s-%s; build from source: git clone https://github.com/%s && cargo install --path %s/src-tauri\n' "$(uname -s)" "$(uname -m)" "$REPO" "$(basename "$REPO")"; exit 1 ;;
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
# the release publishes <asset>.sha256 beside the binary; a download that does not match it is not
# installed. A release without one (anything before v2.0.1) is taken as before.
WANT=$(curl -fsSL --retry 2 "$URL.sha256" 2>/dev/null | cut -d' ' -f1 || true)
if [ -n "$WANT" ]; then
	GOT=$(sha256sum "$TMP" 2>/dev/null | cut -d' ' -f1 || shasum -a 256 "$TMP" | cut -d' ' -f1)
	[ "$GOT" = "$WANT" ] || { rm -f "$TMP"; printf '\n  checksum mismatch for %s: not installed\n\n' "$ASSET"; exit 1; }
fi
chmod +x "$TMP"
mv -f "$TMP" "$BIN/$NAME"
# ponytail: installs before the rewrite linked $BIN/prs at prs.py, which this release removes. Repoint
# it, so the old name keeps working instead of dying with "No such file or directory". Only when it is
# already there: this does not create the legacy name on a fresh machine.
if [ -e "$BIN/prs" ] || [ -L "$BIN/prs" ]; then
	ln -sf "$BIN/$NAME" "$BIN/prs"
fi
# ponytail: a launcher entry, so it starts from the app menu with no terminal attached.
# Linux only: macOS wants a .app bundle, which a bare binary is not.
if [ "$(uname -s)" = Linux ]; then
	APPS=$HOME/.local/share/applications
	ICON=$HOME/.local/share/icons/$NAME.png
	mkdir -p "$APPS" "$(dirname "$ICON")"
	# the binary carries the same icon it draws in its own window; no download for it
	"$BIN/$NAME" --icon > "$ICON" 2>/dev/null || rm -f "$ICON"
	# ponytail: the app menu starts us with no shell, so no GH_TOKEN from the user's rc. The launcher
	# re-runs through an interactive shell, which sources it — but only for the shells where -ic means
	# that. Anything else (tcsh has no such combination) gets the binary straight, token or not.
	cat > "$BIN/$NAME-launch" <<-EOF
		#!/bin/sh
		case \${SHELL##*/} in
			bash|zsh|fish) exec "\$SHELL" -ic 'exec "$BIN/$NAME"' ;;
		esac
		exec "$BIN/$NAME"
	EOF
	chmod +x "$BIN/$NAME-launch"
	cat > "$APPS/$NAME.desktop" <<-EOF
		[Desktop Entry]
		Type=Application
		Name=github-dashy
		Comment=Your PRs, review-requested, assigned
		Exec=$BIN/$NAME-launch
		Icon=$ICON
		Terminal=false
		Categories=Development;
	EOF
	update-desktop-database "$APPS" 2>/dev/null || true
fi

# ponytail: --version is the cheapest full load of the binary, so a missing Linux webview shows
# up here as a loader error instead of as a window that never opens.
ERR=$("$BIN/$NAME" --version 2>&1 >/dev/null) || true
TAG=$("$BIN/$NAME" --version 2>/dev/null | awk '{print $NF}')

row() { printf '    %s%-22s%s %s%s%s\n' "$CYAN" "$1" "$R" "$DIM" "$2" "$R"; }
warn() { printf '\n    %s⚠%s  %s\n' "$YELLOW" "$R" "$1"; }

printf '  %s✓%s installed %s%s%s  %s: %s%s\n\n' "$GREEN" "$R" "$B" "${TAG:-latest}" "$R" "$DIM" "$BIN/$NAME" "$R"
printf '  Run it with %s%s%s%s in your terminal:\n\n' "$B" "$PINK" "$NAME" "$R"
row "$NAME"          "the desktop dashboard: your PRs, review-requested, assigned"
row "$NAME --browser" "the same dashboard in your browser"
row "$NAME --demo"   "try it with canned data (no token, no claude)"
row "$NAME --help"   "all flags and subcommands"
[ "$(uname -s)" = Linux ] && printf '\n  %sor launch it from your app menu: no terminal needed%s\n' "$DIM" "$R"

if [ -z "$TAG" ]; then
	warn "$NAME did not start: ${ERR:-unknown error}"
	if [ "$(uname -s)" = Linux ]; then
		printf '       %sit needs the system webview:%s\n' "$DIM" "$R"
		printf '       %sapt install libwebkit2gtk-4.1-0 libgtk-3-0%s   %s(dnf: webkit2gtk4.1 gtk3 / pacman: webkit2gtk-4.1 gtk3)%s\n' "$CYAN" "$R" "$DIM" "$R"
	fi
fi

case ":$PATH:" in
	*":$BIN:"*) ;;
	*) warn "$BIN is not on your PATH: add to your shell rc:"
	   printf '       %sexport PATH="%s:$PATH"%s\n' "$CYAN" "$BIN" "$R" ;;
esac
[ -n "${GH_TOKEN:-}${GITHUB_TOKEN:-}" ] || warn "no github token: export GH_TOKEN=... (scope: repo)"
printf '\n'
