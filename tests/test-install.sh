#!/usr/bin/env bash
# Usage: test-install.sh INSTALL. The installer on a scratch HOME, with a fake systemctl.
set -u
INSTALL=$(readlink -f "$1")
T=$(mktemp -d "${TMPDIR:-/tmp}/claude-switch-install.XXXXXX")
trap 'rm -rf "$T"' EXIT
export HOME=$T/home
export FAKE_SYSTEMCTL_LOG=$T/systemctl.log
# The files of root go there, never to /etc and /usr.
export CLAUDE_SWITCH_SYSTEM=$T/system
# They move the folder bash-completion loads from, out of the scratch HOME.
unset BASH_COMPLETION_USER_DIR XDG_DATA_HOME
mkdir -p "$HOME/.local/bin" "$HOME/.config/google-chrome-beta" "$T/bin"
cp "$(dirname "$(readlink -f "$0")")/fake-systemctl.sh" "$T/bin/systemctl"
chmod +x "$T/bin/systemctl"
export PATH=$T/bin:$PATH
printf 'the old script\n' > "$HOME/.local/bin/claude-switch.sh"

fails=0 passes=0
check() {
    if eval "$2" >/dev/null 2>&1; then passes=$((passes + 1)); else fails=$((fails + 1)); printf 'FAIL %s\n' "$1"; fi
}
SHARE=$HOME/.local/share/claude-switch
UNITS=$HOME/.config/systemd/user
MANIFEST=$HOME/.config/google-chrome-beta/NativeMessagingHosts/com.lucasvecchete.claude_switch.json
COMPLETION=$HOME/.local/share/bash-completion/completions/claude-switch

"$INSTALL" > "$T/out" 2>&1; rc=$?
check "install succeeds" '[ "$rc" -eq 0 ]'
check "the command is claude-switch" '[ -x "$HOME/.local/bin/claude-switch" ]'
check "the old name is gone" '[ ! -e "$HOME/.local/bin/claude-switch.sh" ]'
check "the host is installed" '[ -x "$SHARE/chrome-host.py" ]'
check "the Desktop helper is installed" '[ -x "$SHARE/desktop.py" ]'
check "the usage helper is installed" '[ -x "$SHARE/usage.py" ] && cmp -s "$SHARE/usage.py" "$(dirname "$INSTALL")/share/usage.py"'
check "the watch runs claude-switch" 'grep -q "/.local/bin/claude-switch watch" "$UNITS/claude-switch-watch.service"'
check "the watch timer is installed" '[ -f "$UNITS/claude-switch-watch.timer" ]'
check "the watch timer is left off while automatic switching is" '! grep -q "claude-switch-watch" "$T/systemctl.log"'
check "the extension is installed" '[ -f "$SHARE/chrome-extension/manifest.json" ] && [ -f "$SHARE/chrome-extension/worker.js" ]'
check "the completion is where bash-completion loads it" \
    '[ "$(stat -c %a "$COMPLETION")" = 644 ] && cmp -s "$COMPLETION" "$(dirname "$INSTALL")/share/completion.bash"'
check "the completion: how to load it in an open shell" 'grep -q "source $COMPLETION" "$T/out"'
check "the timer runs claude-switch" 'grep -q "/.local/bin/claude-switch refresh" "$UNITS/claude-switch-refresh.service"'
check "the timer is installed" '[ -f "$UNITS/claude-switch-refresh.timer" ]'
check "units reloaded, timer enabled" 'grep -qx -- "--user daemon-reload" "$T/systemctl.log" && grep -qx -- "--user enable --now claude-switch-refresh.timer" "$T/systemctl.log"'
check "host manifest: path" '[ "$(jq -r .path "$MANIFEST")" = "$SHARE/chrome-host.py" ]'
check "host manifest: the extension of the key" '[ "$(jq -r ".allowed_origins[0]" "$MANIFEST")" = chrome-extension://klclebpiaondgkoelekdoimhcboaenal/ ]'
check "no manifest for a browser that is not here" '[ ! -e "$HOME/.config/google-chrome" ]'

"$INSTALL" --check > "$T/out" 2>&1; rc=$?
check "check after install: clean" '[ "$rc" -eq 0 ]'
: > "$T/systemctl.log"
"$INSTALL" > "$T/out" 2>&1
check "a second install changes nothing" 'grep -q "Nothing to install" "$T/out"'
check "a second install reloads no unit" '! grep -q daemon-reload "$T/systemctl.log"'

printf '# an edit\n' >> "$HOME/.local/bin/claude-switch"
chmod 644 "$SHARE/chrome-host.py"
"$INSTALL" --check > "$T/out" 2>&1; rc=$?
check "check sees an edited file" '[ "$rc" -eq 1 ] && grep -qx "differs: $HOME/.local/bin/claude-switch" "$T/out"'
check "check sees a lost mode" 'grep -qx "differs: $SHARE/chrome-host.py" "$T/out"'
check "check changes nothing" '[ "$(tail -1 "$HOME/.local/bin/claude-switch")" = "# an edit" ]'
printf 'the old script\n' > "$HOME/.local/bin/claude-switch.sh"
"$INSTALL" --check > "$T/out" 2>&1
check "check sees the old name" 'grep -qx "left over: $HOME/.local/bin/claude-switch.sh" "$T/out"'
"$INSTALL" > "$T/out" 2>&1
check "install puts everything back" '"$INSTALL" --check'
mkdir -p "$HOME/.claude-accounts" && : > "$HOME/.claude-accounts/.auto"
: > "$T/systemctl.log"
"$INSTALL" > "$T/out" 2>&1
check "with automatic switching on, the watch timer is started" 'grep -qx -- "--user enable --now claude-switch-watch.timer" "$T/systemctl.log"'

XDG_DATA_HOME=$T/data "$INSTALL" > "$T/out" 2>&1
check "the completion follows XDG_DATA_HOME" '[ -f "$T/data/bash-completion/completions/claude-switch" ]'
BASH_COMPLETION_USER_DIR=$T/user XDG_DATA_HOME=$T/data "$INSTALL" > "$T/out" 2>&1
check "and BASH_COMPLETION_USER_DIR before it" '[ -f "$T/user/completions/claude-switch" ]'

# The claude-switch-remote service of root, under a scratch root.
REPO=$(dirname "$INSTALL")
SERVICE=$T/system/usr/local/libexec/claude-switch/remote-keys.py
SOCKET_UNIT=$T/system/etc/systemd/system/claude-switch-remote.socket
SERVICE_UNIT=$T/system/etc/systemd/system/claude-switch-remote@.service
CLAUDE_SWITCH_SYSTEM= "$INSTALL" --root > "$T/out" 2>&1; rc=$?
check "the service of root needs root" '[ "$rc" -eq 1 ] && grep -q "needs root: sudo .* --root" "$T/out" && [ ! -e "$T/system" ]'
mkdir -p "$T/fakeroot"
printf '#!/bin/sh\n[ "$1" = -u ] && echo 0 || exec /usr/bin/id "$@"\n' > "$T/fakeroot/id"
chmod +x "$T/fakeroot/id"
PATH=$T/fakeroot:$PATH SUDO_USER=someone "$INSTALL" > "$T/out" 2>&1; rc=$?
check "sudo for everything is refused: it would install for root" '[ "$rc" -eq 1 ] && grep -q "as yourself" "$T/out"'
: > "$T/systemctl.log"
CLAUDE_SWITCH_SYSTEM=$T/system "$INSTALL" --root > "$T/out" 2>&1; rc=$?
check "the service: installed" '[ "$rc" -eq 0 ] && cmp -s "$SERVICE" "$REPO/share/remote-keys.py" \
    && [ "$(stat -c %a "$SERVICE")" = 755 ] && [ "$(stat -c %a "$SOCKET_UNIT")" = 644 ] \
    && cmp -s "$SERVICE_UNIT" "$REPO/systemd/system/claude-switch-remote@.service"'
check "the service: its socket started, with systemd of the system" \
    'grep -qx daemon-reload "$T/systemctl.log" && grep -qx "enable --now claude-switch-remote.socket" "$T/systemctl.log"'
check "the service: nothing of the user installed with it" '! grep -q "$HOME" "$T/out"'
CLAUDE_SWITCH_SYSTEM=$T/system "$INSTALL" --root > "$T/out" 2>&1
check "the service: a second install changes nothing" 'grep -q "Nothing to install, the service matches" "$T/out"'
CLAUDE_SWITCH_SYSTEM=$T/system "$INSTALL" --check > "$T/out" 2>&1; rc=$?
check "check: the service as installed" '[ "$rc" -eq 0 ]'
printf '# an edit\n' >> "$SERVICE"
CLAUDE_SWITCH_SYSTEM=$T/system "$INSTALL" --check > "$T/out" 2>&1; rc=$?
check "check sees an edited service" '[ "$rc" -eq 1 ] && grep -qx "differs: $SERVICE" "$T/out"'
CLAUDE_SWITCH_SYSTEM=$T/system "$INSTALL" > "$T/out" 2>&1
check "install says the service differs, and leaves it to sudo" \
    'grep -q "service differs from this checkout. Update it with: sudo .* --root" "$T/out" && [ "$(tail -1 "$SERVICE")" = "# an edit" ]'
: > "$T/systemctl.log"
CLAUDE_SWITCH_SYSTEM=$T/system "$INSTALL" --remove-root > "$T/out" 2>&1; rc=$?
check "the service: removed" '[ "$rc" -eq 0 ] && [ ! -e "$SERVICE" ] && [ ! -e "$SOCKET_UNIT" ] && [ ! -e "$SERVICE_UNIT" ] \
    && grep -qx "disable --now claude-switch-remote.socket" "$T/systemctl.log" && grep -qx daemon-reload "$T/systemctl.log"'
CLAUDE_SWITCH_SYSTEM=$T/system "$INSTALL" --remove-root > "$T/out" 2>&1
check "the service: removed twice" 'grep -q "not installed" "$T/out"'

printf '%d passed, %d failed\n' "$passes" "$fails"
[ "$fails" -eq 0 ]
