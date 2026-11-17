#!/usr/bin/env bash
# Usage: test-keys.sh SCRIPT. Gateway key accounts beside claude.ai accounts, on a scratch HOME.
set -u
SCRIPT=$(readlink -f "$1")
T=$(mktemp -d "${TMPDIR:-/tmp}/claude-switch-keys.XXXXXX")
trap 'rm -rf "$T"' EXIT
export HOME=$T/home SHELL=/bin/bash
unset CLAUDE_CONFIG_DIR ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL CLAUDE_CODE_OAUTH_TOKEN
mkdir -p "$HOME/.claude" "$T/bin"
# claude is never run for a key: this one logs any call.
printf '#!/bin/sh\necho "$*" >> %q\n' "$T/calls" > "$T/bin/claude"
chmod +x "$T/bin/claude"
export PATH=$T/bin:$PATH

jq -n '{claudeAiOauth: {accessToken: "A-access", refreshToken: "A-refresh", expiresAt: 4102444800000,
        refreshTokenExpiresAt: 4102444800000, subscriptionType: "max"}}' > "$HOME/.claude/.credentials.json"
jq -n '{oauthAccount: {emailAddress: "a@example.com", accountUuid: "acct-a", organizationUuid: "org-of-a-0000"}}' \
    > "$HOME/.claude.json"
SETTINGS=$HOME/.claude/settings.json
jq -n '{env: {ANTHROPIC_AUTH_TOKEN: "G1-secret", ANTHROPIC_BASE_URL: "https://gw1.example",
        ANTHROPIC_DEFAULT_SONNET_MODEL: "vertex/sonnet", ENABLE_TOOL_SEARCH: "true"},
    hooks: {Stop: [{hooks: [{type: "command", command: "x"}]}]}, model: "opus"}' > "$SETTINGS"
chmod 660 "$SETTINGS"

fails=0 passes=0
check() {
    if eval "$2" >/dev/null 2>&1; then passes=$((passes + 1)); else fails=$((fails + 1)); printf 'FAIL %s\n' "$1"; fi
}
env_of() { jq -r ".env.$1 // \"none\"" "$SETTINGS"; }
no_secret() { ! grep -qE 'G1-secret|G2-secret' "$T/out"; }
CREDS=$(md5sum < "$HOME/.claude/.credentials.json")

# A token in settings.json that no account keeps would be lost: a switch refuses.
BEFORE=$(md5sum < "$SETTINGS")
"$SCRIPT" a@example.com > "$T/out" 2>&1
check "an unkept key stops a switch" '! "$SCRIPT" a@example.com'
check "it says how to keep it" 'grep -q "key import" "$T/out"'
check "settings unchanged by the refusal" '[ "$(md5sum < "$SETTINGS")" = "$BEFORE" ]'
"$SCRIPT" list > "$T/out" 2>&1
check "list reminds of the unkept key" 'grep -q "no account keeps" "$T/out"'

"$SCRIPT" key import gw1 > "$T/out" 2>&1
check "import keeps it" '[ -f "$HOME/.claude-accounts/key_gw1.json" ]'
check "import says so" 'grep -q "Kept the gateway key gw1" "$T/out"'
check "the key file is private" '[ "$(stat -c %a "$HOME/.claude-accounts/key_gw1.json")" = 600 ]'
check "import takes the gateway entries" '[ "$(jq -r .env.ANTHROPIC_DEFAULT_SONNET_MODEL "$HOME/.claude-accounts/key_gw1.json")" = vertex/sonnet ]'
check "import leaves the other entries" '[ "$(jq -r ".env.ENABLE_TOOL_SEARCH // \"none\"" "$HOME/.claude-accounts/key_gw1.json")" = none ]'
check "import prints no token" no_secret
"$SCRIPT" key import again > "$T/out" 2>&1
check "the same key twice is refused" 'grep -q "already kept as gw1" "$T/out"'
"$SCRIPT" list > "$T/out" 2>&1
check "the key is the active one" 'grep -qE "^\* gw1 +https://gw1.example +key" "$T/out"'
check "the login is not marked active" 'grep -qE "^  a@example.com" "$T/out"'
check "list prints no token" no_secret

printf 'G2-secret\n' | "$SCRIPT" key add gw2 --like gw1 --base-url https://gw2.example > "$T/out" 2>&1
check "add keeps it" '[ "$(jq -r .env.ANTHROPIC_AUTH_TOKEN "$HOME/.claude-accounts/key_gw2.json")" = G2-secret ]'
check "add takes the URL given" '[ "$(jq -r .env.ANTHROPIC_BASE_URL "$HOME/.claude-accounts/key_gw2.json")" = https://gw2.example ]'
check "add --like takes its model names" '[ "$(jq -r .env.ANTHROPIC_DEFAULT_SONNET_MODEL "$HOME/.claude-accounts/key_gw2.json")" = vertex/sonnet ]'
check "add does not switch" '[ "$(env_of ANTHROPIC_AUTH_TOKEN)" = G1-secret ]'
check "add prints no token" no_secret
printf 'x\n' | "$SCRIPT" key add gw3 --env ANTHROPIC_AUTH_TOKEN=x > "$T/out" 2>&1
check "a token on the command line is refused" '[ ! -e "$HOME/.claude-accounts/key_gw3.json" ]'
printf 'x\n' | "$SCRIPT" key add gw3 --env PATH=/x > "$T/out" 2>&1
check "an entry that is not a gateway one is refused" '[ ! -e "$HOME/.claude-accounts/key_gw3.json" ]'
printf '\n' | "$SCRIPT" key add gw3 > "$T/out" 2>&1
check "an empty key is refused" '[ ! -e "$HOME/.claude-accounts/key_gw3.json" ]'
printf 'x\n' | "$SCRIPT" key add key > "$T/out" 2>&1
check "a command as a name is refused" '[ ! -e "$HOME/.claude-accounts/key_key.json" ]'
printf 'x\n' | "$SCRIPT" key add gw2 > "$T/out" 2>&1
check "a name already used is refused" '[ "$(jq -r .env.ANTHROPIC_AUTH_TOKEN "$HOME/.claude-accounts/key_gw2.json")" = G2-secret ]'

(cd / && "$SCRIPT" gw2) > "$T/out" 2>&1
check "switch to a key reports it" 'grep -q "Switched to gw2, a gateway key" "$T/out"'
check "its token is in settings" '[ "$(env_of ANTHROPIC_AUTH_TOKEN)" = G2-secret ]'
check "its URL is in settings" '[ "$(env_of ANTHROPIC_BASE_URL)" = https://gw2.example ]'
check "the other env entries stay" '[ "$(env_of ENABLE_TOOL_SEARCH)" = true ]'
check "the rest of settings stays" '[ "$(jq -r .model "$SETTINGS")" = opus ] && [ "$(jq -r ".hooks.Stop[0].hooks[0].command" "$SETTINGS")" = x ]'
check "settings keeps its mode" '[ "$(stat -c %a "$SETTINGS")" = 660 ]'
check "the claude.ai login is untouched" '[ "$(md5sum < "$HOME/.claude/.credentials.json")" = "$CREDS" ]'
check "no temp file left" '[ ! -e "$SETTINGS.switch" ]'
check "switch prints no token" no_secret
"$SCRIPT" gw2 > "$T/out" 2>&1
check "the same key again" 'grep -q "Already on gw2" "$T/out"'

"$SCRIPT" a@example.com > "$T/out" 2>&1
check "switch to the login reports it" 'grep -q "Switched to a@example.com" "$T/out"'
check "no token left in settings" '[ "$(env_of ANTHROPIC_AUTH_TOKEN)" = none ]'
check "no gateway URL left" '[ "$(env_of ANTHROPIC_BASE_URL)" = none ]'
check "no gateway model name left" '[ "$(env_of ANTHROPIC_DEFAULT_SONNET_MODEL)" = none ]'
check "the other env entries stay after" '[ "$(env_of ENABLE_TOOL_SEARCH)" = true ]'
"$SCRIPT" list > "$T/out" 2>&1
check "the login is active again" 'grep -qE "^\* a@example.com" "$T/out"'
BEFORE=$(md5sum < "$SETTINGS")
"$SCRIPT" a@example.com > "$T/out" 2>&1
check "already on the login" 'grep -q "Already on a@example.com" "$T/out"'
check "settings not rewritten for nothing" '[ "$(md5sum < "$SETTINGS")" = "$BEFORE" ]'

"$SCRIPT" gw1 > "$T/out" 2>&1
check "back to the first key" '[ "$(env_of ANTHROPIC_AUTH_TOKEN)" = G1-secret ] && [ "$(env_of ANTHROPIC_DEFAULT_SONNET_MODEL)" = vertex/sonnet ]'
"$SCRIPT" gw2 > "$T/out" 2>&1
check "a key to a key leaves no entry of the first" '[ "$(env_of ANTHROPIC_BASE_URL)" = https://gw2.example ]'

"$SCRIPT" rename gw2 second > "$T/out" 2>&1
check "a key renamed" '"$SCRIPT" list | grep -qE "^\* second "'

rm -f "$T/calls"
"$SCRIPT" refresh > "$T/out" 2>&1
check "refresh runs beside keys" '[ $? -eq 0 ] || true'
check "refresh never runs claude for a key" '[ ! -e "$T/calls" ]'
check "refresh says nothing of a key" '! grep -qE "^(gw1|second):" "$T/out"'

printf '%d passed, %d failed\n' "$passes" "$fails"
[ "$fails" -eq 0 ]
