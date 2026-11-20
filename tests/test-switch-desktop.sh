#!/usr/bin/env bash
# Usage: test-switch-desktop.sh SCRIPT. claude-switch drives Claude Desktop through desktop.py, here a fake
# that logs its calls, on a scratch HOME.
set -u
SCRIPT=$(readlink -f "$1")
HERE=$(dirname "$(readlink -f "$0")")
T=$(mktemp -d "${TMPDIR:-/tmp}/claude-switch-desktop.XXXXXX")
trap 'rm -rf "$T"' EXIT
export HOME=$T/home SHELL=/bin/bash FAKE_DESKTOP_LOG=$T/desktop.log
unset CLAUDE_CONFIG_DIR ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN CLAUDE_CODE_OAUTH_TOKEN
mkdir -p "$HOME/.claude" "$HOME/.claude-accounts" "$HOME/.local/share/claude-switch" "$T/bin"
cp "$HERE/stub-claude.sh" "$T/bin/claude"
chmod +x "$T/bin/claude"
export PATH=$T/bin:$PATH
chmod 700 "$HOME/.claude-accounts"

jq -n '{claudeAiOauth: {accessToken: "A-access", refreshToken: "A-refresh", expiresAt: 4102444800000,
    refreshTokenExpiresAt: 4102444800000}}' > "$HOME/.claude/.credentials.json"
jq -n '{oauthAccount: {emailAddress: "a@example.com", accountUuid: "acct-a", organizationUuid: "org-a-0000"}}' \
    > "$HOME/.claude.json"
jq -n '{name: "second", login: {claudeAiOauth: {accessToken: "B-access", refreshToken: "B-refresh",
    expiresAt: 4102444800000, refreshTokenExpiresAt: 4102444800000}},
    oauthAccount: {emailAddress: "b@example.com", accountUuid: "acct-b", organizationUuid: "org-b-0000"}}' \
    > "$HOME/.claude-accounts/b@example.com_org-b-00.json"
chmod 600 "$HOME/.claude/.credentials.json" "$HOME/.claude.json" "$HOME"/.claude-accounts/*.json

fails=0 passes=0
check() {
    if eval "$2" >/dev/null 2>&1; then passes=$((passes + 1)); else fails=$((fails + 1)); printf 'FAIL %s\n' "$1"; fi
}
calls() { tr '\n' '|' < "$T/desktop.log" 2>/dev/null; }
live() { jq -r .oauthAccount.emailAddress "$HOME/.claude.json"; }
# Runs the script in a terminal that answers ANSWER to the question. ANSWER ARGS...
in_terminal() {
    local answer=$1
    shift
    printf '%s\n' "$answer" | script -qfec "$(printf '%q ' "$SCRIPT" "$@")" /dev/null > "$T/out" 2>&1
}

"$SCRIPT" second > "$T/out" 2>&1
check "no desktop.py: the switch works" '[ "$(live)" = b@example.com ] && [ ! -e "$T/desktop.log" ]'
"$SCRIPT" a@example.com > "$T/out" 2>&1

cp "$HERE/fake-desktop.sh" "$HOME/.local/share/claude-switch/desktop.py"
chmod +x "$HOME/.local/share/claude-switch/desktop.py"

"$SCRIPT" second > "$T/out" 2>&1
check "closed Desktop: switched to the account" '[ "$(calls)" = "switch acct-b|" ]'
check "closed Desktop: says so" 'grep -q "Claude Desktop is on acct-b" "$T/out"'
check "closed Desktop: the store lock stays here" '! grep -q "lock inherited" "$T/desktop.log"'
rm -f "$T/desktop.log"

"$SCRIPT" second > "$T/out" 2>&1
check "already on it: Desktop is brought along" '[ "$(calls)" = "switch acct-b|" ]'
rm -f "$T/desktop.log"

FAKE_DESKTOP_SWITCH=3 "$SCRIPT" a@example.com > "$T/out" 2>&1; rc=$?
check "open Desktop, no terminal: the switch still succeeds" '[ "$rc" -eq 0 ] && [ "$(live)" = a@example.com ]'
check "open Desktop, no terminal: Desktop is left alone" '[ "$(calls)" = "switch acct-a|" ]'
check "open Desktop, no terminal: says how to bring it along" 'grep -q "claude-switch a@example.com" "$T/out"'
rm -f "$T/desktop.log"

printf 'y\n' | FAKE_DESKTOP_SWITCH=3,0 "$SCRIPT" second > "$T/out" 2>&1
check "open Desktop, a yes piped in: only a terminal answers" '[ "$(calls)" = "switch acct-b|" ]'
"$SCRIPT" a@example.com > "$T/out" 2>&1
rm -f "$T/desktop.log"

FAKE_DESKTOP_SWITCH=3,0 in_terminal y second
check "open Desktop, yes: closed, switched, started" '[ "$(calls)" = "switch acct-b|quit|switch acct-b|start|" ]'
check "open Desktop, yes: the question names the account" 'grep -q "Restart it on second?" "$T/out"'
rm -f "$T/desktop.log"

FAKE_DESKTOP_SWITCH=3 in_terminal n a@example.com
check "open Desktop, no: left alone" '[ "$(calls)" = "switch acct-a|" ] && [ "$(live)" = a@example.com ]'
rm -f "$T/desktop.log"

FAKE_DESKTOP_SWITCH=3,0 in_terminal "" second
check "open Desktop, Enter: Yes is the default" '[ "$(calls)" = "switch acct-b|quit|switch acct-b|start|" ] && grep -q "\[Y/n\]" "$T/out"'
rm -f "$T/desktop.log"
"$SCRIPT" a@example.com > "$T/out" 2>&1
rm -f "$T/desktop.log"

# Desktop runs this switch: it stays open on its account, and nothing is asked.
FAKE_DESKTOP_SWITCH=6 in_terminal y second
check "run by Desktop: left alone, nothing asked" '[ "$(calls)" = "switch acct-b|" ] && ! grep -q "\[Y/n\]" "$T/out" && [ "$(live)" = b@example.com ]'
rm -f "$T/desktop.log"
"$SCRIPT" a@example.com > "$T/out" 2>&1
rm -f "$T/desktop.log"

FAKE_DESKTOP_SWITCH=3 FAKE_DESKTOP_QUIT=1 in_terminal y second
check "Desktop that doesn't close: not started twice, said" '[ "$(calls)" = "switch acct-b|quit|" ] && grep -q "didn.t close" "$T/out"'
rm -f "$T/desktop.log"

FAKE_DESKTOP_SWITCH=3,4 in_terminal y a@example.com
check "closed but unnamed: Desktop is started again anyway" '[ "$(calls)" = "switch acct-a|quit|switch acct-a|start|" ]'
rm -f "$T/desktop.log"

FAKE_DESKTOP_SWITCH=4 "$SCRIPT" second > "$T/out" 2>&1; rc=$?
check "unnamed Desktop: the switch still succeeds" '[ "$rc" -eq 0 ] && [ "$(live)" = b@example.com ]'

printf '%d passed, %d failed\n' "$passes" "$fails"
[ "$fails" -eq 0 ]
