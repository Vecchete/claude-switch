#!/usr/bin/env bash
# Installs claude-switch from this checkout into the places it runs from. Run it again after any
# change here: it copies what differs and leaves the rest alone.
#
#   ./install.sh                  install
#   ./install.sh --check          say what differs from this checkout, change nothing, fail when something does
#   sudo ./install.sh --root      install the claude-switch-remote service, which brings Remote Control back in
#                                 the sessions outside tmux after a switch, and runs as root to do it
#   sudo ./install.sh --remove-root
#                                 take that service out again
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
# The claude-switch-remote service, root's. CLAUDE_SWITCH_SYSTEM puts it under a scratch folder, for the tests.
SYSTEM=${CLAUDE_SWITCH_SYSTEM:-}
SYSTEM_UNITS=$SYSTEM/etc/systemd/system
ROOT_SOURCES=("$ROOT/share/remote-keys.py" "$ROOT/systemd/system/claude-switch-remote.socket"
    "$ROOT/systemd/system/claude-switch-remote@.service")
ROOT_TARGETS=("$SYSTEM/usr/local/libexec/claude-switch/remote-keys.py" "$SYSTEM_UNITS/claude-switch-remote.socket"
    "$SYSTEM_UNITS/claude-switch-remote@.service")
ROOT_MODES=(755 644 644)

CHECK="" ROOT_STEP=""
case ${1:-} in
    "") ;;
    --check) CHECK=yes ;;
    --root|--remove-root) ROOT_STEP=$1 ;;
    *)
        printf 'Usage: %s [--check | --root | --remove-root]\n' "$0" >&2
        exit 2
        ;;
esac
if [ -n "$ROOT_STEP" ] && [ "$(id -u)" -ne 0 ] && [ -z "$SYSTEM" ]; then
    printf '%s needs root: sudo %s %s\n' "$ROOT_STEP" "$0" "$ROOT_STEP" >&2
    exit 1
fi
# sudo would install everything for root, in root's home.
if [ -z "$ROOT_STEP" ] && [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
    printf 'Run %s as yourself. Only the service of root needs sudo: sudo %s --root\n' "$0" "$0" >&2
    exit 1
fi

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

# The service of root, and nothing else: it presses the keys that run /remote-control and /after-switch in a session
# outside tmux, which only root can.
if [ "$ROOT_STEP" = --root ]; then
    for index in "${!ROOT_SOURCES[@]}"; do
        place "${ROOT_SOURCES[index]}" "${ROOT_TARGETS[index]}" "${ROOT_MODES[index]}"
    done
    if changed "$SYSTEM_UNITS/*"; then
        systemctl daemon-reload
    fi
    systemctl enable --now claude-switch-remote.socket >/dev/null 2>&1 \
        || printf 'Could not start claude-switch-remote.socket: systemctl enable --now claude-switch-remote.socket\n'
    [ "${#CHANGED[@]}" -gt 0 ] || printf 'Nothing to install, the service matches %s.\n' "$ROOT"
    exit 0
fi
if [ "$ROOT_STEP" = --remove-root ]; then
    systemctl disable --now claude-switch-remote.socket >/dev/null 2>&1 || true
    for target in "${ROOT_TARGETS[@]}"; do
        if [ -e "$target" ]; then
            rm -f "$target"
            CHANGED+=("$target")
            printf 'removed %s\n' "$target"
        fi
    done
    rmdir "$(dirname "${ROOT_TARGETS[0]}")" 2>/dev/null || true
    if [ "${#CHANGED[@]}" -gt 0 ]; then
        systemctl daemon-reload
    else
        printf 'The service is not installed.\n'
    fi
    exit 0
fi

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

# The service of root, when it's installed, is checked too. Only sudo ./install.sh --root updates it.
ROOT_DIFFERS=""
if [ -e "${ROOT_TARGETS[1]}" ]; then
    for index in "${!ROOT_SOURCES[@]}"; do
        if [ -n "$CHECK" ]; then
            place "${ROOT_SOURCES[index]}" "${ROOT_TARGETS[index]}" "${ROOT_MODES[index]}"
        elif ! cmp -s "${ROOT_SOURCES[index]}" "${ROOT_TARGETS[index]}"; then
            ROOT_DIFFERS=yes
        fi
    done
fi

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
if [ -n "$ROOT_DIFFERS" ]; then
    printf 'The claude-switch-remote service differs from this checkout. Update it with: sudo %s --root\n' "$0"
fi
[ "${#CHANGED[@]}" -gt 0 ] || printf 'Nothing to install, everything matches %s.\n' "$ROOT"
