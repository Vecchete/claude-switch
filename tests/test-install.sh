#!/usr/bin/env bash
# Usage: test-install.sh INSTALL. The installer on a scratch HOME, with a fake systemctl.
set -u
INSTALL=$(readlink -f "$1")
T=$(mktemp -d "${TMPDIR:-/tmp}/claude-switch-install.XXXXXX")
trap 'rm -rf "$T"' EXIT
export HOME=$T/home
export FAKE_SYSTEMCTL_LOG=$T/systemctl.log
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

"$INSTALL" > "$T/out" 2>&1; rc=$?
check "install succeeds" '[ "$rc" -eq 0 ]'
check "the command is claude-switch" '[ -x "$HOME/.local/bin/claude-switch" ]'
check "the old name is gone" '[ ! -e "$HOME/.local/bin/claude-switch.sh" ]'
check "the host is installed" '[ -x "$SHARE/chrome-host.py" ]'
check "the Desktop helper is installed" '[ -x "$SHARE/desktop.py" ]'
check "the extension is installed" '[ -f "$SHARE/chrome-extension/manifest.json" ] && [ -f "$SHARE/chrome-extension/worker.js" ]'
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

printf '%d passed, %d failed\n' "$passes" "$fails"
[ "$fails" -eq 0 ]
