#!/usr/bin/env bash
# Installs claude-switch from this checkout into the places it runs from. Run it again after any
# change here: it copies what differs and leaves the rest alone.
#
#   ./install.sh           install
#   ./install.sh --check   say what differs from this checkout, change nothing, fail when something does
#
# The accounts, the saved sessions and every login stay in ~/.claude-accounts, never in this checkout.
set -euo pipefail
umask 022

ROOT=$(dirname "$(readlink -f "$0")")
BIN=$HOME/.local/bin
SHARE=$HOME/.local/share/claude-switch
UNITS=$HOME/.config/systemd/user
STORE=$HOME/.claude-accounts
# Where bash-completion looks for the completion of a command at its first Tab.
COMPLETIONS=${BASH_COMPLETION_USER_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/bash-completion}/completions
HOST_NAME=com.lucasvecchete.claude_switch
# The browsers that get the manifest of the native host, the ones installed here.
BROWSERS=(google-chrome-beta google-chrome)

CHECK=""
case ${1:-} in
    "") ;;
    --check) CHECK=yes ;;
    *)
        printf 'Usage: %s [--check]\n' "$0" >&2
        exit 2
        ;;
esac

DIFFERS=0
CHANGED=()
MANIFEST=$(mktemp)
trap 'rm -f "$MANIFEST"' EXIT

# The id Chrome gives the extension, from the key in its manifest.
extension_id() {
    python3 -I -c '
import base64, hashlib, json, sys
key = json.load(open(sys.argv[1]))["key"]
digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
print("".join(chr(ord("a") + int(c, 16)) for c in digest))' "$ROOT/share/chrome-extension/manifest.json"
}

# Puts SOURCE at TARGET with MODE, or with --check says that it differs. SOURCE TARGET MODE
place() {
    local source=$1 target=$2 mode=$3
    if [ -f "$target" ] && cmp -s "$source" "$target" && [ "$(stat -c %a "$target")" = "$mode" ]; then
        return 0
    fi
    if [ -n "$CHECK" ]; then
        printf 'differs: %s\n' "$target"
        DIFFERS=1
        return 0
    fi
    install -D -m "$mode" "$source" "$target"
    CHANGED+=("$target")
    printf 'installed %s\n' "$target"
}

changed() {
    local path
    for path in "${CHANGED[@]}"; do
        case $path in
            $1) return 0 ;;
        esac
    done
    return 1
}

place "$ROOT/bin/claude-switch" "$BIN/claude-switch" 755
place "$ROOT/share/chrome-host.py" "$SHARE/chrome-host.py" 755
place "$ROOT/share/desktop.py" "$SHARE/desktop.py" 755
place "$ROOT/share/usage.py" "$SHARE/usage.py" 755
place "$ROOT/share/completion.bash" "$COMPLETIONS/claude-switch" 644
for file in manifest.json worker.js; do
    place "$ROOT/share/chrome-extension/$file" "$SHARE/chrome-extension/$file" 644
done
for unit in claude-switch-refresh.service claude-switch-refresh.timer claude-switch-watch.service \
    claude-switch-watch.timer; do
    place "$ROOT/systemd/$unit" "$UNITS/$unit" 644
done

jq -n --arg name "$HOST_NAME" --arg path "$SHARE/chrome-host.py" --arg origin "chrome-extension://$(extension_id)/" \
    '{name: $name, description: "claude-switch: claude.ai in Chrome follows the Claude account switch",
      path: $path, type: "stdio", allowed_origins: [$origin]}' > "$MANIFEST"
for browser in "${BROWSERS[@]}"; do
    [ -d "$HOME/.config/$browser" ] || continue
    place "$MANIFEST" "$HOME/.config/$browser/NativeMessagingHosts/$HOST_NAME.json" 644
done

# The command was claude-switch.sh until 2026-10-31.
OLD=$BIN/claude-switch.sh
if [ -n "$CHECK" ]; then
    if [ -e "$OLD" ]; then
        printf 'left over: %s\n' "$OLD"
        DIFFERS=1
    fi
    [ "$DIFFERS" -eq 1 ] || printf 'Everything installed matches %s.\n' "$ROOT"
    exit "$DIFFERS"
fi

if changed "$UNITS/*"; then
    systemctl --user daemon-reload
fi
systemctl --user enable --now claude-switch-refresh.timer >/dev/null 2>&1 \
    || printf 'Could not enable claude-switch-refresh.timer: systemctl --user enable --now claude-switch-refresh.timer\n'
# The watch timer runs while automatic switching is on: claude-switch auto on and off start and stop it.
if [ -f "$STORE/.auto" ]; then
    systemctl --user enable --now claude-switch-watch.timer >/dev/null 2>&1 \
        || printf 'Could not enable claude-switch-watch.timer: systemctl --user enable --now claude-switch-watch.timer\n'
fi

# The timer runs the new name from here on, so the old one can go.
if [ -e "$OLD" ]; then
    rm -f "$OLD"
    printf 'removed %s, the command is claude-switch now\n' "$OLD"
fi

# Chrome keeps running the host it started. A host stopped between two switches is started again by
# the extension within 10 seconds, with the new code.
if changed "$SHARE/chrome-host.py" && pgrep -f "^python3 -I $SHARE/chrome-host.py" >/dev/null; then
    mkdir -p "$STORE"
    exec 9>>"$STORE/.lock"
    flock -w 300 9
    pkill -f "^python3 -I $SHARE/chrome-host.py" || true
    exec 9>&-
    printf 'restarted the Chrome host, the extension starts the new one\n'
fi
if changed "$SHARE/chrome-extension/*"; then
    printf 'Chrome loads the new extension when it starts again. To load it now: chrome://extensions, Reload on Claude account switch.\n'
fi
if changed "$COMPLETIONS/claude-switch"; then
    printf 'Tab completes claude-switch in new shells. In an open one: source %q\n' "$COMPLETIONS/claude-switch"
fi
[ "${#CHANGED[@]}" -gt 0 ] || printf 'Nothing to install, everything matches %s.\n' "$ROOT"
