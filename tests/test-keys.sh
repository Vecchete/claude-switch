#!/usr/bin/env bash
# Usage: test-keys.sh SCRIPT. Gateway key accounts beside claude.ai accounts, on scratch HOMEs, with the real
# claude for auth status.
set -u
SCRIPT=$(readlink -f "$1")
T=$(mktemp -d "${TMPDIR:-/tmp}/claude-switch-keys.XXXXXX")
trap 'kill $(jobs -p) 2>/dev/null; rm -rf "$T"' EXIT
REAL_CLAUDE=$(readlink -f "$HOME/.local/bin/claude")
export HOME=$T/home SHELL=/bin/bash
unset CLAUDE_CONFIG_DIR ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL CLAUDE_CODE_OAUTH_TOKEN \
    CLAUDE_CODE_USE_BEDROCK CLAUDE_CODE_USE_VERTEX CLAUDE_CODE_USE_FOUNDRY ANTHROPIC_PROFILE
mkdir -p "$HOME/.claude" "$T/bin"
# claude is never run for a key: this one logs any call.
printf '#!/bin/sh\necho "$*" >> %q\n' "$T/calls" > "$T/bin/claude"
chmod +x "$T/bin/claude"
export PATH=$T/bin:$PATH

jq -n '{claudeAiOauth: {accessToken: "A-access", refreshToken: "A-refresh", expiresAt: 4102444800000,
        refreshTokenExpiresAt: 4102444800000, scopes: ["user:inference", "user:profile"], subscriptionType: "max"}}' \
    > "$HOME/.claude/.credentials.json"
jq -n '{oauthAccount: {emailAddress: "a@example.com", accountUuid: "acct-a", organizationUuid: "org-of-a-0000"},
        hasCompletedOnboarding: true}' > "$HOME/.claude.json"
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
# The gateway entries NAMES are in settings.json, emptied. NAME...
emptied() {
    local name
    for name in "$@"; do
        [ "$(jq -c --arg name "$name" '.env[$name]' "$SETTINGS")" = '""' ] || return 1
    done
}
no_secret() { ! grep -q -- '-secret' "$T/out"; }
marked() { "$SCRIPT" list 2>/dev/null | grep -qE "^\* $1 "; }
# What the real claude takes to authenticate with this HOME.
auth_method() { (cd / && "$REAL_CLAUDE" auth status </dev/null 2>/dev/null | jq -r .authMethod); }
CREDS=$(md5sum < "$HOME/.claude/.credentials.json")

# A token in settings.json that no account keeps would be lost: a switch refuses.
BEFORE=$(md5sum < "$SETTINGS")
"$SCRIPT" a@example.com > "$T/out" 2>&1
check "an unkept key stops a switch" '! "$SCRIPT" a@example.com'
check "it says how to keep it" 'grep -q "key import" "$T/out"'
check "and how to forget it" 'grep -q "key remove" "$T/out"'
check "settings unchanged by the refusal" '[ "$(md5sum < "$SETTINGS")" = "$BEFORE" ]'
"$SCRIPT" list > "$T/out" 2>&1
check "list reminds of the unkept key" 'grep -q "no account keeps" "$T/out"'
check "list marks no account for an unkept key" '! grep -q "^\*" "$T/out"'

"$SCRIPT" key import gw1 > "$T/out" 2>&1
check "import keeps it" '[ -f "$HOME/.claude-accounts/key_gw1.json" ]'
check "import says so" 'grep -q "Kept the gateway key gw1" "$T/out"'
check "the key file is private" '[ "$(stat -c %a "$HOME/.claude-accounts/key_gw1.json")" = 600 ]'
check "import takes the gateway entries" '[ "$(jq -r .env.ANTHROPIC_DEFAULT_SONNET_MODEL "$HOME/.claude-accounts/key_gw1.json")" = vertex/sonnet ]'
check "import leaves the other entries" '[ "$(jq -r ".env.ENABLE_TOOL_SEARCH // \"none\"" "$HOME/.claude-accounts/key_gw1.json")" = none ]'
check "import prints no token" no_secret
check "claude takes the key before the login" '[ "$(auth_method)" = oauth_token ]'
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
printf 'x\n' | "$SCRIPT" key add gw3 --env ANTHROPIC_MODEL= > "$T/out" 2>&1
check "an entry without a value is refused" '[ ! -e "$HOME/.claude-accounts/key_gw3.json" ]'
printf 'x\n' | "$SCRIPT" key add gw3 --like a@example.com > "$T/out" 2>&1
check "--like a claude.ai account is refused" '[ ! -e "$HOME/.claude-accounts/key_gw3.json" ]'
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
# Running sessions keep a variable that is gone from settings.json, and take in one that changes.
check "the gateway entries are emptied, not taken out" 'emptied ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL ANTHROPIC_DEFAULT_SONNET_MODEL'
check "the other env entries stay after" '[ "$(env_of ENABLE_TOOL_SEARCH)" = true ]'
check "claude takes the emptied entries as unset" '[ "$(auth_method)" = claude.ai ]'
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
check "a key renamed" 'marked second'

rm -f "$T/calls"
"$SCRIPT" refresh > "$T/out" 2>&1
status=$?
check "refresh runs beside keys" '[ "$status" -eq 0 ]'
check "refresh never runs claude for a key" '[ ! -e "$T/calls" ]'
check "refresh says nothing of a key" '! grep -qE "^(gw1|second):" "$T/out"'

# A new HOME: the claude.ai account a live, b kept, and settings.json with the gateway key G1, kept as gw1,
# and G2 at another URL kept as gw2. NAME
fresh() {
    export HOME=$T/$1
    SETTINGS=$HOME/.claude/settings.json
    mkdir -p "$HOME/.claude" "$HOME/.claude-accounts"
    chmod 700 "$HOME/.claude-accounts"
    jq -n '{claudeAiOauth: {accessToken: "A-access", refreshToken: "A-refresh", expiresAt: 4102444800000,
            refreshTokenExpiresAt: 4102444800000, scopes: ["user:inference", "user:profile"], subscriptionType: "max"}}' \
        > "$HOME/.claude/.credentials.json"
    jq -n '{oauthAccount: {emailAddress: "a@example.com", accountUuid: "acct-a", organizationUuid: "org-of-a-0000"},
            hasCompletedOnboarding: true}' > "$HOME/.claude.json"
    jq -n '{name: "", login: {claudeAiOauth: {accessToken: "B-access", refreshToken: "B-refresh",
            expiresAt: 4102444800000, refreshTokenExpiresAt: 4102444800000, scopes: ["user:inference", "user:profile"],
            subscriptionType: "pro"}},
            oauthAccount: {emailAddress: "b@example.com", accountUuid: "acct-b", organizationUuid: "org-of-b-0000"}}' \
        > "$HOME/.claude-accounts/b@example.com_org-of-b.json"
    chmod 600 "$HOME/.claude/.credentials.json" "$HOME/.claude.json" "$HOME"/.claude-accounts/*.json
    jq -n '{env: {ANTHROPIC_AUTH_TOKEN: "G1-secret", ANTHROPIC_BASE_URL: "https://gw1.example",
            ANTHROPIC_DEFAULT_SONNET_MODEL: "vertex/sonnet", ENABLE_TOOL_SEARCH: "true"}, model: "opus"}' > "$SETTINGS"
    chmod 600 "$SETTINGS"
    "$SCRIPT" key import gw1 >/dev/null 2>&1
    printf 'G2-secret\n' | "$SCRIPT" key add gw2 --like gw1 --base-url https://gw2.example >/dev/null 2>&1
}
live_access() { jq -r .claudeAiOauth.accessToken "$HOME/.claude/.credentials.json"; }
set_env() { jq "$1" "$SETTINGS" > "$SETTINGS.edit" && mv "$SETTINGS.edit" "$SETTINGS"; }

# From a key to a claude.ai account other than the live login: the login changes and the key goes out.
fresh to-other
"$SCRIPT" gw2 >/dev/null 2>&1
"$SCRIPT" b@example.com > "$T/out" 2>&1
check "key to another login: switched" '[ "$(live_access)" = B-access ]'
check "key to another login: the key is emptied" 'emptied ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL'
check "key to another login: marked" 'marked b@example.com'
check "key to another login: claude takes the login" '[ "$(auth_method)" = claude.ai ]'

# A key switch never writes over a token no account keeps.
fresh unkept
set_env '.env.ANTHROPIC_AUTH_TOKEN = "G9-secret"'
BEFORE=$(md5sum < "$SETTINGS")
"$SCRIPT" gw2 > "$T/out" 2>&1
check "unkept: a switch to a key refuses" '! "$SCRIPT" gw2'
check "unkept: settings unchanged" '[ "$(md5sum < "$SETTINGS")" = "$BEFORE" ]'
check "unkept: says how to keep it" 'grep -q "key import" "$T/out"'

# Two keys with the same token at two gateways are two accounts.
fresh same-token
printf 'G1-secret\n' | "$SCRIPT" key add gw3 --like gw1 --base-url https://gw3.example > "$T/out" 2>&1
check "same token: kept" '[ -f "$HOME/.claude-accounts/key_gw3.json" ]'
"$SCRIPT" gw3 >/dev/null 2>&1
check "same token: the one switched to is marked" 'marked gw3'
"$SCRIPT" gw1 > "$T/out" 2>&1
check "same token: a switch back is no 'Already on'" 'grep -q "Switched to gw1" "$T/out"'
check "same token: its URL is back" '[ "$(env_of ANTHROPIC_BASE_URL)" = https://gw1.example ]'
printf 'G1-secret\n' | "$SCRIPT" key add gw4 --like gw1 > "$T/out" 2>&1
check "the same key twice under two names is refused" 'grep -q "already kept as gw1" "$T/out"'
check "and not kept" '[ ! -e "$HOME/.claude-accounts/key_gw4.json" ]'

# Entries changed by hand in settings.json belong to the active key.
fresh edits
set_env '.env.ANTHROPIC_DEFAULT_SONNET_MODEL = "vertex/sonnet-2"'
check "hand edit: the key stays active" 'marked gw1'
check "hand edit: saved to the key" '[ "$(jq -r .env.ANTHROPIC_DEFAULT_SONNET_MODEL "$HOME/.claude-accounts/key_gw1.json")" = vertex/sonnet-2 ]'
"$SCRIPT" gw2 >/dev/null 2>&1
"$SCRIPT" gw1 >/dev/null 2>&1
check "hand edit: back after a round trip" '[ "$(env_of ANTHROPIC_DEFAULT_SONNET_MODEL)" = vertex/sonnet-2 ]'
set_env '.env.ANTHROPIC_BASE_URL = "https://gw1-moved.example"'
"$SCRIPT" gw2 > "$T/out" 2>&1
check "a moved URL: the switch goes on" 'grep -q "Switched to gw2" "$T/out"'
"$SCRIPT" gw1 >/dev/null 2>&1
check "a moved URL: kept by its key" '[ "$(env_of ANTHROPIC_BASE_URL)" = https://gw1-moved.example ]'

# A running session keeps the ANTHROPIC_MODEL it started on.
fresh model
set_env '.env.ANTHROPIC_MODEL = "vertex/main"'
"$SCRIPT" a@example.com > "$T/out" 2>&1
check "a key's ANTHROPIC_MODEL: a switch says running sessions keep it" 'grep -q "started on the model vertex/main keeps it" "$T/out"'
"$SCRIPT" gw1 > "$T/out" 2>&1
check "to a key with ANTHROPIC_MODEL: nothing said" '! grep -q "keeps it" "$T/out"'
"$SCRIPT" gw2 > "$T/out" 2>&1
check "from it to a key without: said" 'grep -q "started on the model vertex/main keeps it" "$T/out"'

# A gateway key has no sign-in.
fresh login
rm -f "$T/calls"
"$SCRIPT" login gw1 > "$T/out" 2>&1
check "login with a key: refused" '! "$SCRIPT" login gw1'
check "login with a key: says what renews it" 'grep -q "key renew gw1" "$T/out"'
check "login with a key: claude not run" '[ ! -e "$T/calls" ]'

# A new token for a key.
fresh renew
"$SCRIPT" gw2 >/dev/null 2>&1
printf 'G2-new-secret\n' | "$SCRIPT" key renew gw2 > "$T/out" 2>&1
check "renew: the key has its new token" '[ "$(jq -r .env.ANTHROPIC_AUTH_TOKEN "$HOME/.claude-accounts/key_gw2.json")" = G2-new-secret ]'
check "renew: the active key's token is in settings too" '[ "$(env_of ANTHROPIC_AUTH_TOKEN)" = G2-new-secret ]'
check "renew: the rest stays" '[ "$(env_of ANTHROPIC_BASE_URL)" = https://gw2.example ]'
check "renew: the key is still active" 'marked gw2'
check "renew prints no token" no_secret
printf 'G1-new-secret\n' | "$SCRIPT" key renew gw1 > "$T/out" 2>&1
check "renew another key: its file only" '[ "$(jq -r .env.ANTHROPIC_AUTH_TOKEN "$HOME/.claude-accounts/key_gw1.json")" = G1-new-secret ] && [ "$(env_of ANTHROPIC_AUTH_TOKEN)" = G2-new-secret ]'
printf 'x\n' | "$SCRIPT" key renew a@example.com > "$T/out" 2>&1
check "renew a claude.ai account: refused" 'grep -q "claude-switch login" "$T/out"'

# Forgetting a key.
fresh remove
"$SCRIPT" key remove gw2 > "$T/out" 2>&1
check "remove: the key is gone" '[ ! -e "$HOME/.claude-accounts/key_gw2.json" ]'
check "remove another key: settings stays" '[ "$(env_of ANTHROPIC_AUTH_TOKEN)" = G1-secret ]'
"$SCRIPT" key remove gw1 > "$T/out" 2>&1
check "remove the active key: gone" '[ ! -e "$HOME/.claude-accounts/key_gw1.json" ]'
check "remove the active key: emptied in settings" 'emptied ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL ANTHROPIC_DEFAULT_SONNET_MODEL'
check "remove the active key: the login is active" 'marked a@example.com'
"$SCRIPT" key remove b@example.com > "$T/out" 2>&1
check "remove a claude.ai account: refused" '[ -f "$HOME/.claude-accounts/b@example.com_org-of-b.json" ]'
fresh removed-by-hand
rm "$HOME/.claude-accounts/key_gw1.json"
"$SCRIPT" b@example.com > "$T/out" 2>&1
check "the active key's file removed by hand: switches refuse" '[ "$(live_access)" = A-access ] && grep -q "key remove" "$T/out"'
"$SCRIPT" key import gone >/dev/null 2>&1
"$SCRIPT" key remove gone >/dev/null 2>&1
"$SCRIPT" b@example.com > "$T/out" 2>&1
check "the way out it gives works" '[ "$(live_access)" = B-access ]'

# settings.json as it can be found.
fresh empty
: > "$SETTINGS"
"$SCRIPT" b@example.com >/dev/null 2>&1
check "empty settings: a login switch leaves it alone" '[ "$(live_access)" = B-access ] && [ ! -s "$SETTINGS" ]'
"$SCRIPT" gw2 > "$T/out" 2>&1
check "empty settings: a key switch fills it" '[ "$(env_of ANTHROPIC_AUTH_TOKEN)" = G2-secret ]'
check "empty settings: then marked" 'marked gw2'
fresh missing
rm "$SETTINGS"
"$SCRIPT" gw2 >/dev/null 2>&1
check "no settings: a key switch creates it" '[ "$(env_of ANTHROPIC_AUTH_TOKEN)" = G2-secret ]'
check "no settings: private" '[ "$(stat -c %a "$SETTINGS")" = 600 ]'
fresh invalid
printf '{"env": {' > "$SETTINGS"
"$SCRIPT" gw2 > "$T/out" 2>&1
check "invalid settings: a key switch refuses" 'grep -q "can.t be read as JSON" "$T/out" && [ "$(cat "$SETTINGS")" = "{\"env\": {" ]'
fresh link
mkdir -p "$HOME/dotfiles"
mv "$SETTINGS" "$HOME/dotfiles/settings.json"
ln -s "$HOME/dotfiles/settings.json" "$SETTINGS"
"$SCRIPT" gw2 >/dev/null 2>&1
check "a symbolic link stays one" '[ -L "$SETTINGS" ]'
check "the file it points to has the key" '[ "$(jq -r .env.ANTHROPIC_AUTH_TOKEN "$HOME/dotfiles/settings.json")" = G2-secret ]'
check "no temp file beside it" '[ -z "$(ls -A "$HOME/dotfiles" | grep -v "^settings.json$")" ]'
fresh read-only
mkdir -p "$HOME/store"
mv "$SETTINGS" "$HOME/store/settings.json"
ln -s "$HOME/store/settings.json" "$SETTINGS"
chmod 555 "$HOME/store"
"$SCRIPT" b@example.com > "$T/out" 2>&1
check "a link to a folder that can't be written: a login switch refuses first" '[ "$(live_access)" = A-access ] && grep -q "can.t write" "$T/out"'
"$SCRIPT" gw2 >/dev/null 2>&1
check "a link to a folder that can't be written: a key switch refuses" '[ "$(jq -r .env.ANTHROPIC_AUTH_TOKEN "$HOME/store/settings.json")" = G1-secret ]'
chmod 755 "$HOME/store"
group=$(id -Gn | tr ' ' '\n' | grep -vxF "$(id -gn)" | head -1)
if [ -n "$group" ]; then
    fresh group
    chgrp "$group" "$SETTINGS" && chmod 660 "$SETTINGS"
    "$SCRIPT" gw2 >/dev/null 2>&1
    check "settings keeps its group" '[ "$(stat -c "%a %G" "$SETTINGS")" = "660 $group" ]'
else
    printf 'skipped: settings keeps its group, this user is in no other group\n'
fi

# A gateway that takes its key in headers.
fresh headers
jq -n '{env: {ANTHROPIC_BASE_URL: "https://hdr.example", ANTHROPIC_CUSTOM_HEADERS: "x-portkey-api-key: H-secret",
        ENABLE_TOOL_SEARCH: "true"}}' > "$SETTINGS"
BEFORE=$(md5sum < "$SETTINGS")
"$SCRIPT" b@example.com > "$T/out" 2>&1
check "headers no account keeps: no refusal" '[ "$(live_access)" = B-access ]'
check "headers no account keeps: left alone" '[ "$(md5sum < "$SETTINGS")" = "$BEFORE" ]'
"$SCRIPT" key import hdr > "$T/out" 2>&1
check "headers: kept" '[ "$(jq -r .env.ANTHROPIC_CUSTOM_HEADERS "$HOME/.claude-accounts/key_hdr.json")" = "x-portkey-api-key: H-secret" ]'
check "headers: marked" 'marked hdr'
"$SCRIPT" a@example.com >/dev/null 2>&1
check "headers: emptied on a switch to a login" 'emptied ANTHROPIC_BASE_URL ANTHROPIC_CUSTOM_HEADERS'
"$SCRIPT" hdr >/dev/null 2>&1
check "headers: back" '[ "$(env_of ANTHROPIC_CUSTOM_HEADERS)" = "x-portkey-api-key: H-secret" ]'

# An Anthropic API key goes in ANTHROPIC_API_KEY, which gateways and the API take as x-api-key.
fresh api-key
jq -n '{env: {ANTHROPIC_API_KEY: "K1-secret", ANTHROPIC_BASE_URL: "https://xkey.example"}}' > "$SETTINGS"
"$SCRIPT" key import k1 >/dev/null 2>&1
printf 'K2-secret\n' | "$SCRIPT" key add k2 --like k1 >/dev/null 2>&1
check "--like an API key: the token goes in ANTHROPIC_API_KEY" '[ "$(jq -c ".env | keys" "$HOME/.claude-accounts/key_k2.json")" = "[\"ANTHROPIC_API_KEY\",\"ANTHROPIC_BASE_URL\"]" ]'
printf 'K3-secret\n' | "$SCRIPT" key add k3 --api-key --base-url https://api.example >/dev/null 2>&1
check "--api-key: the token goes in ANTHROPIC_API_KEY" '[ "$(jq -r .env.ANTHROPIC_API_KEY "$HOME/.claude-accounts/key_k3.json")" = K3-secret ]'
"$SCRIPT" gw1 >/dev/null 2>&1
"$SCRIPT" k1 >/dev/null 2>&1
check "an API key after a bearer token: the token is emptied" 'emptied ANTHROPIC_AUTH_TOKEN'
check "an API key after a bearer token: marked" 'marked k1'
check "an API key after a bearer token: claude takes the API key" '[ "$(auth_method)" = api_key ]'

# A key add waits for its token with the store unlocked.
fresh unlocked
mkfifo "$T/fifo"
"$SCRIPT" key add slow < "$T/fifo" > "$T/out-slow" 2>&1 &
exec 7>"$T/fifo"
sleep 1
timeout 10 "$SCRIPT" list > "$T/out" 2>&1
status=$?
check "while a key add waits: list runs" '[ "$status" -eq 0 ] && ! grep -q Waiting "$T/out"'
printf 'S-secret\n' >&7
exec 7>&-
wait
check "then the key add goes on" '[ -f "$HOME/.claude-accounts/key_slow.json" ]'

# Each run removes only what it was writing itself, so other runs never break a switch.
fresh parallel
( while :; do "$SCRIPT" help >/dev/null 2>&1; done ) &
noise=$!
failed=0
for run in $(seq 1 30); do
    target=gw1
    [ $((run % 2)) -eq 0 ] || target=gw2
    "$SCRIPT" "$target" >/dev/null 2>&1 || failed=$((failed + 1))
done
kill "$noise"
wait "$noise" 2>/dev/null
check "switches beside other runs: none fails" '[ "$failed" -eq 0 ]'
check "switches beside other runs: settings whole" 'jq -e .env "$SETTINGS"'
# What a switch killed on the way left aside goes at the next run.
printf 'partial' > "$SETTINGS.switch"
printf 'partial' > "$HOME/.claude/.credentials.json.switch"
"$SCRIPT" list >/dev/null 2>&1
check "what a killed switch left aside is removed" '[ ! -e "$SETTINGS.switch" ] && [ ! -e "$HOME/.claude/.credentials.json.switch" ]'

# A variable of the shell is only worth a warning when it goes before the account switched to.
fresh shell
ANTHROPIC_AUTH_TOKEN=shell-token "$SCRIPT" gw2 > "$T/out" 2>&1
check "a token in the shell, a key switch: no warning, settings goes first" '! grep -q "set in this shell" "$T/out"'
CLAUDE_CODE_OAUTH_TOKEN=shell-token "$SCRIPT" gw1 > "$T/out" 2>&1
check "an OAuth token in the shell, a key switch: no warning, the key goes first" '! grep -q "set in this shell" "$T/out"'
CLAUDE_CODE_OAUTH_TOKEN=shell-token "$SCRIPT" b@example.com > "$T/out" 2>&1
check "an OAuth token in the shell, a login switch: warned" 'grep -q "CLAUDE_CODE_OAUTH_TOKEN is set in this shell" "$T/out"'
ANTHROPIC_AUTH_TOKEN=shell-token "$SCRIPT" a@example.com > "$T/out" 2>&1
check "a token in the shell that the emptied entry cancels: no warning" '! grep -q "set in this shell" "$T/out"'
CLAUDE_CONFIG_DIR=/elsewhere "$SCRIPT" gw1 > "$T/out" 2>&1
check "CLAUDE_CONFIG_DIR: warned for a key too" 'grep -q "CLAUDE_CONFIG_DIR is set in this shell" "$T/out"'
fresh shell-api-key
jq -n '{env: {ANTHROPIC_API_KEY: "K1-secret"}}' > "$SETTINGS"
"$SCRIPT" key import k1 >/dev/null 2>&1
"$SCRIPT" b@example.com >/dev/null 2>&1
ANTHROPIC_AUTH_TOKEN=shell-token "$SCRIPT" k1 > "$T/out" 2>&1
check "a bearer token in the shell, a switch to an API key: warned" 'grep -q "ANTHROPIC_AUTH_TOKEN is set in this shell" "$T/out"'

# What settings.json sets that claude takes before the account.
fresh overrides
"$SCRIPT" b@example.com >/dev/null 2>&1
set_env '.apiKeyHelper = "echo helper"'
"$SCRIPT" list > "$T/out" 2>&1
check "apiKeyHelper before a login: list says so" 'grep -q "sets apiKeyHelper, which claude uses before b@example.com" "$T/out"'
"$SCRIPT" gw2 > "$T/out" 2>&1
check "apiKeyHelper after a gateway token: nothing said" '! grep -q apiKeyHelper "$T/out"'
set_env '.env.CLAUDE_CODE_USE_BEDROCK = "1"'
"$SCRIPT" list > "$T/out" 2>&1
check "a cloud provider before a key: list says so" 'grep -q "sets CLAUDE_CODE_USE_BEDROCK, which claude uses before gw2" "$T/out"'

# The prompt marks what list marks.
fresh prompt
"$SCRIPT" gw2 >/dev/null 2>&1
printf 'q\n' | script -qfc "$SCRIPT" /dev/null > "$T/out" 2>&1
check "the prompt marks the active key" 'grep -a "gw2" "$T/out" | grep -q active'
check "the prompt doesn't mark the login" '! grep -a "a@example.com" "$T/out" | grep -q active'
set_env '.env.ANTHROPIC_AUTH_TOKEN = "G9-secret"'
printf 'q\n' | script -qfc "$SCRIPT" /dev/null > "$T/out" 2>&1
check "the prompt reminds of an unkept key" 'grep -aq "no account keeps" "$T/out"'
check "the prompt prints no token" no_secret

printf '%d passed, %d failed\n' "$passes" "$fails"
[ "$fails" -eq 0 ]
