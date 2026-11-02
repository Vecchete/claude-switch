#!/usr/bin/env bash
# Usage: test-refresh.sh SCRIPT. The refresh of the inactive accounts, with a fake claude, on scratch homes.
set -u
SCRIPT=$(readlink -f "$1")
HERE=$(dirname "$(readlink -f "$0")")
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/claude-switch-refresh.XXXXXX")
trap 'rm -rf "$ROOT"' EXIT
BASE_PATH=$PATH
unset CLAUDE_CONFIG_DIR ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN CLAUDE_CODE_OAUTH_TOKEN
fails=0 passes=0

check() {
    if eval "$2" >/dev/null 2>&1; then passes=$((passes + 1)); else fails=$((fails + 1)); printf 'FAIL %s\n' "$1"; fi
}
now() { date +%s%3N; }
calls() { cat "$FAKE_STATE/calls" 2>/dev/null | grep -c . ; }
notified() { cat "$FAKE_STATE/notify" 2>/dev/null | grep -c . ; }
stubs_left() { ls -d "$HOME"/.claude-accounts/.refresh.* 2>/dev/null | grep -c . ; }
b() { jq -r "$1" "$B"; }
live() { jq -r "$1" "$HOME/.claude/.credentials.json"; }

# A machine with the live account A in ~/.claude and the account B stored, not live.
# SCENARIO MS_LEFT_ON_B_ACCESS_TOKEN [MS_LEFT_ON_B_SIGN_IN]
setup() {
    local t
    S=$ROOT/$1
    export HOME=$S/home FAKE_STATE=$S/state
    mkdir -p "$HOME/.claude" "$HOME/.claude-accounts" "$FAKE_STATE" "$S/bin"
    chmod 700 "$HOME/.claude-accounts"
    cp "$HERE/fake-claude.sh" "$S/bin/claude"
    cp "$HERE/fake-notify.sh" "$S/bin/notify-send"
    chmod +x "$S/bin/claude" "$S/bin/notify-send"
    export PATH=$S/bin:$BASE_PATH
    unset FAKE_MODE FAKE_DELAY FAKE_HANG FAKE_STATUS_REFRESHES FAKE_TERM_ROTATE FAKE_NOTIFY_FAIL
    t=$(now)
    jq -n --argjson e $((t + 25200000)) '{claudeAiOauth: {accessToken: "A-access", refreshToken: "A-refresh",
        expiresAt: $e, refreshTokenExpiresAt: 4102444800000, scopes: ["user:inference"], subscriptionType: "max"},
        mcpOAuth: {"x|1": {accessToken: "mcp"}}}' > "$HOME/.claude/.credentials.json"
    jq -n '{oauthAccount: {emailAddress: "a@example.com", accountUuid: "ua", organizationUuid: "org-aaaa-0000"},
        theme: "light"}' > "$HOME/.claude.json"
    B=$HOME/.claude-accounts/b@example.com_org-bbbb.json
    jq -n --argjson e $((t + $2)) --argjson ends $((t + ${3:-2592000000})) '{name: "bee",
        login: {claudeAiOauth: {accessToken: "B-access", refreshToken: "B-refresh", expiresAt: $e,
            refreshTokenExpiresAt: $ends, scopes: ["user:inference"], subscriptionType: "max"}},
        oauthAccount: {emailAddress: "b@example.com", accountUuid: "ub", organizationUuid: "org-bbbb-0000"}}' > "$B"
    chmod 600 "$HOME/.claude/.credentials.json" "$HOME/.claude.json" "$B"
}

# Starts a refresh that hangs once it rotated the login, and waits for that moment.
start_hanging_refresh() {
    local tries
    FAKE_HANG=30 setsid "$SCRIPT" refresh > "$S/out" 2>&1 &
    PID=$!
    for tries in $(seq 75); do
        [ ! -f "$FAKE_STATE/rotated" ] || break
        sleep 0.2
    done
}

# 1. Due: claude auth status refreshes it in a folder of its own, without a model request.
setup due 3600000
ANTHROPIC_API_KEY=leak CLAUDECODE=1 CLAUDE_CODE_SESSION_ID=parent "$SCRIPT" refresh > "$S/out" 2>&1; rc=$?
check "due: exit 0" '[ "$rc" = 0 ]'
check "due: access token rotated" '[ "$(b .login.claudeAiOauth.accessToken)" = acc-1 ]'
check "due: refresh token rotated" '[ "$(b .login.claudeAiOauth.refreshToken)" = ref-1 ]'
check "due: good for hours again" '[ "$(b .login.claudeAiOauth.expiresAt)" -gt $(( $(now) + 25000000 )) ]'
check "due: keeps its name" '[ "$(b .name)" = bee ]'
check "due: keeps its account" '[ "$(b .oauthAccount.emailAddress)" = b@example.com ]'
check "due: stays private" '[ "$(stat -c %a "$B")" = 600 ]'
check "due: one call, no model" '[ "$(calls)" = 1 ] && grep -q "args=\[auth\]\[status\]" "$FAKE_STATE/calls"'
check "due: in a folder of the store" 'grep -q "cfg=$HOME/.claude-accounts/.refresh\." "$FAKE_STATE/calls"'
check "due: run from that folder" 'grep -q "cwd=$HOME/.claude-accounts/.refresh\." "$FAKE_STATE/calls"'
check "due: no update, no extra traffic, no connectors, no API key, nothing of a calling session" \
    'grep -q "noupd=1|quiet=1|connectors=false|apikey=|claudeenv=0|args=" "$FAKE_STATE/calls"'
check "due: no folder left" '[ "$(stubs_left)" = 0 ]'
check "due: live login untouched" '[ "$(live .claudeAiOauth.accessToken)" = A-access ] && [ "$(live ".mcpOAuth[\"x|1\"].accessToken")" = mcp ]'
check "due: live account untouched" '[ "$(jq -r .oauthAccount.emailAddress "$HOME/.claude.json")" = a@example.com ]'
check "due: says it" 'grep -q "^bee: refreshed" "$S/out"'
"$SCRIPT" refresh > "$S/out" 2>&1
check "due: the next run leaves it" '[ "$(calls)" = 1 ] && grep -q "^bee: .*nothing to do" "$S/out"'

# 2. The live account is never refreshed, even when its stored copy expired: its sessions own it.
setup live 25200000
jq --argjson e $(( $(now) - 1000 )) '.claudeAiOauth.expiresAt = $e' "$HOME/.claude/.credentials.json" > "$S/c"
cp "$S/c" "$HOME/.claude/.credentials.json"
"$SCRIPT" refresh > "$S/out" 2>&1
check "live: never refreshed" '[ "$(calls)" = 0 ]'
check "live: its login untouched" '[ "$(live .claudeAiOauth.accessToken)" = A-access ]'
check "live: B not due" 'grep -q "^bee: .*nothing to do" "$S/out"'

# 3. --force refreshes one that is not due.
setup force 25200000
"$SCRIPT" refresh --force > "$S/out" 2>&1
check "force: rotated" '[ "$(b .login.claudeAiOauth.accessToken)" = acc-1 ]'

# 4. When auth status does not refresh, the cheapest model does, with nothing loaded.
setup model 3600000
FAKE_STATUS_REFRESHES=0 "$SCRIPT" refresh > "$S/out" 2>&1
check "model: rotated" '[ "$(b .login.claudeAiOauth.accessToken)" = acc-1 ]'
check "model: status first, then one request" '[ "$(calls)" = 2 ] && tail -1 "$FAKE_STATE/calls" | grep -q "\[-p\]"'
check "model: haiku" 'tail -1 "$FAKE_STATE/calls" | grep -q "\[--model\]\[haiku\]"'
check "model: no tools" 'tail -1 "$FAKE_STATE/calls" | grep -q "\[--tools\]\[\]"'
check "model: no MCP" 'tail -1 "$FAKE_STATE/calls" | grep -q "\[--strict-mcp-config\]"'
check "model: no settings" 'tail -1 "$FAKE_STATE/calls" | grep -q "\[--setting-sources\]\[local\]"'
check "model: no session" 'tail -1 "$FAKE_STATE/calls" | grep -q "\[--no-session-persistence\]"'

# 5. Over the usage limit: the request fails after the refresh, the rotated login is kept.
setup limit 3600000
FAKE_STATUS_REFRESHES=0 FAKE_MODE=limit "$SCRIPT" refresh > "$S/out" 2>&1; rc=$?
check "limit: rotated login kept" '[ "$(b .login.claudeAiOauth.refreshToken)" = ref-1 ]'
check "limit: exit 0" '[ "$rc" = 0 ]'
check "limit: not signed out" '[ "$(b .dead)" = null ]'

# 6. Refused: claude empties the tokens. The stored ones stay, the account is marked and announced.
setup dead 3600000
FAKE_MODE=dead "$SCRIPT" refresh > "$S/out" 2>&1; rc=$?
check "dead: exit 1" '[ "$rc" = 1 ]'
check "dead: stored login kept" '[ "$(b .login.claudeAiOauth.refreshToken)" = B-refresh ]'
check "dead: marked" '[ "$(b .dead)" != null ]'
check "dead: announced" '[ "$(notified)" = 1 ] && grep -q bee "$FAKE_STATE/notify"'
check "dead: the notification names the command" 'grep -q "Sign in again: claude-switch login bee$" "$FAKE_STATE/notify"'
check "dead: says it" 'grep -q "^bee: the login was refused, sign in again: claude-switch login bee$" "$S/out"'
check "dead: listed as signed out" '"$SCRIPT" list | grep bee | grep -q "signed out"'
"$SCRIPT" bee > "$S/out" 2>&1
check "dead: switch refused" 'grep -q "signed out" "$S/out" && [ "$(live .claudeAiOauth.accessToken)" = A-access ]'
FAKE_MODE=dead "$SCRIPT" refresh > "$S/out" 2>&1
check "dead: not tried again" '[ "$(calls)" = 1 ]'
check "dead: still says it" 'grep -q "^bee: signed out, sign in again: claude-switch login bee$" "$S/out"'
check "dead: announced once a day" '[ "$(notified)" = 1 ]'
FAKE_EMAIL=b@example.com FAKE_ORG=org-bbbb-0000 FAKE_TOKEN=new "$SCRIPT" login > "$S/out" 2>&1
check "dead: a new sign-in clears it" '[ "$(b .dead)" = null ] && [ "$(b .login.claudeAiOauth.accessToken)" = new-access ]'
check "dead: and keeps the name" '[ "$(b .name)" = bee ]'

# 7. Offline: nothing changes, nothing is marked, the next run tries again.
setup offline 3600000
FAKE_MODE=offline "$SCRIPT" refresh > "$S/out" 2>&1; rc=$?
check "offline: exit 0" '[ "$rc" = 0 ]'
check "offline: login unchanged" '[ "$(b .login.claudeAiOauth.accessToken)" = B-access ] && [ "$(b .login.claudeAiOauth.expiresAt)" -gt "$(now)" ]'
check "offline: not marked" '[ "$(b .dead)" = null ]'
check "offline: says why" 'grep -q "^bee: not refreshed.*Unable to connect" "$S/out"'
check "offline: no folder left" '[ "$(stubs_left)" = 0 ]'

# 8. A switch during a refresh waits, then puts the rotated login live, never the one being rotated.
setup lock 3600000
FAKE_DELAY=3 "$SCRIPT" refresh > "$S/out" 2>&1 &
sleep 1
"$SCRIPT" bee > "$S/out2" 2>&1
wait
check "lock: the switch waited" 'grep -q "Waiting" "$S/out2"'
check "lock: switched" 'grep -q "Switched to bee" "$S/out2"'
check "lock: the rotated login is live" '[ "$(live .claudeAiOauth.accessToken)" = acc-1 ]'

# 9. A refresh killed after the rotation leaves its folder, and the next run brings the login back.
setup killed 3600000
start_hanging_refresh
kill -KILL -- -"$PID"
wait "$PID" 2>/dev/null
check "killed: a folder is left" '[ "$(stubs_left)" = 1 ]'
check "killed: the store still has the old login" '[ "$(b .login.claudeAiOauth.accessToken)" = B-access ]'
"$SCRIPT" refresh > "$S/out" 2>&1
check "killed: the next run brings the rotated login back" '[ "$(b .login.claudeAiOauth.accessToken)" = acc-1 ]'
check "killed: without refreshing again" '[ "$(calls)" = 1 ]'
check "killed: no folder left" '[ "$(stubs_left)" = 0 ]'

# 9b. A switch after a killed refresh brings the rotated login back first, and puts that one live.
setup killed-switch 3600000
start_hanging_refresh
kill -KILL -- -"$PID"
wait "$PID" 2>/dev/null
"$SCRIPT" bee > "$S/out" 2>&1
check "killed then switch: the rotated login is live" '[ "$(live .claudeAiOauth.accessToken)" = acc-1 ]'
check "killed then switch: no folder left" '[ "$(stubs_left)" = 0 ]'

# 10. A left folder older than the store is dropped: the store moved on since.
setup stale 3600000
start_hanging_refresh
kill -KILL -- -"$PID"
wait "$PID" 2>/dev/null
jq --argjson e $(( $(now) + 25200000 )) '.login.claudeAiOauth.accessToken = "signed-in-again"
    | .login.claudeAiOauth.refreshToken = "fresh" | .login.claudeAiOauth.expiresAt = $e' "$B" > "$S/c"
cp "$S/c" "$B"
"$SCRIPT" refresh > "$S/out" 2>&1
check "stale: the store keeps the newer login" '[ "$(b .login.claudeAiOauth.accessToken)" = signed-in-again ]'
check "stale: the folder is dropped" '[ "$(stubs_left)" = 0 ]'

# 11. Stopped by systemd, a TERM to the whole group: the rotated login comes back before it exits.
setup stopped 3600000
start_hanging_refresh
kill -TERM -- -"$PID"
wait "$PID" 2>/dev/null
check "stopped: rotated login kept" '[ "$(b .login.claudeAiOauth.accessToken)" = acc-1 ]'
check "stopped: no folder left" '[ "$(stubs_left)" = 0 ]'

# 11b. Stopped while claude still writes the rotated login: kept, because the script waits for claude.
setup stopped-writing 3600000
FAKE_TERM_ROTATE=1 setsid "$SCRIPT" refresh > "$S/out" 2>&1 &
PID=$!
for tries in $(seq 75); do
    [ ! -f "$FAKE_STATE/started" ] || break
    sleep 0.2
done
kill -TERM -- -"$PID"
wait "$PID" 2>/dev/null
check "stopped while writing: rotated login kept" '[ "$(b .login.claudeAiOauth.accessToken)" = acc-1 ]'
check "stopped while writing: no folder left" '[ "$(stubs_left)" = 0 ]'

# 12. A sign-in that ends within 3 days is announced, once a day, with the command that renews it.
setup ending 25200000 $(( $(date -d "$(date +%F) 12:00 2 days" +%s)000 - $(now) ))
"$SCRIPT" refresh > "$S/out" 2>&1
check "ending: announced" '[ "$(notified)" = 1 ] && grep -q bee "$FAKE_STATE/notify"'
check "ending: the notification names the command" 'grep -q "claude-switch login bee$" "$FAKE_STATE/notify"'
check "ending: says it" \
    'grep -qx "The sign-in of bee ends on $(date -d "2 days" +%d/%m/%Y), in 2 days. Sign in again before then: claude-switch login bee" "$S/out"'
"$SCRIPT" refresh > "$S/out" 2>&1
check "ending: once a day" '[ "$(notified)" = 1 ]'
check "ending: no refresh for that" '[ "$(calls)" = 0 ]'

# 13. The live account's sign-in is announced too.
setup ending-live 25200000
jq --argjson ends $(( $(now) + 86400000 )) '.claudeAiOauth.refreshTokenExpiresAt = $ends' "$HOME/.claude/.credentials.json" > "$S/c"
cp "$S/c" "$HOME/.claude/.credentials.json"
"$SCRIPT" refresh > "$S/out" 2>&1
check "ending live: announced" 'grep -q a@example.com "$FAKE_STATE/notify"'

# 15. Sign-in folders left behind: a finished one newer than the store is kept, the rest goes.
setup signins 25200000
make_signin() {
    mkdir -p "$1"
    jq -n --argjson ends "$2" '{claudeAiOauth: {accessToken: "signin-access", refreshToken: "signin-refresh",
        expiresAt: 4102444800000, refreshTokenExpiresAt: $ends, scopes: ["user:inference"], subscriptionType: "max"}}' \
        > "$1/.credentials.json"
    jq -n '{oauthAccount: {emailAddress: "b@example.com", accountUuid: "ub", organizationUuid: "org-bbbb-0000"}}' > "$1/.claude.json"
    touch -d "2 hours ago" "$1"
}
make_signin "$HOME/.claude-accounts/.signin.done" 4102444900000
make_signin "$HOME/.claude-accounts/.signin.older" 1000
mkdir -p "$HOME/.claude-accounts/.signin.abandoned" "$HOME/.claude-accounts/.signin.fresh"
touch -d "2 days ago" "$HOME/.claude-accounts/.signin.abandoned"
# Finished a moment ago: its login command is about to store it.
make_signin "$HOME/.claude-accounts/.signin.justdone" 4102444950000
touch "$HOME/.claude-accounts/.signin.justdone"
"$SCRIPT" refresh > "$S/out" 2>&1
check "signins: a finished one is kept" '[ "$(b .login.claudeAiOauth.accessToken)" = signin-access ]'
check "signins: and keeps the name" '[ "$(b .name)" = bee ]'
check "signins: its folder is gone" '[ ! -d "$HOME/.claude-accounts/.signin.done" ]'
check "signins: an older one never replaces a newer login" '[ "$(b .login.claudeAiOauth.refreshTokenExpiresAt)" = 4102444900000 ]'
check "signins: the older one is gone" '[ ! -d "$HOME/.claude-accounts/.signin.older" ]'
check "signins: an abandoned one is gone" '[ ! -d "$HOME/.claude-accounts/.signin.abandoned" ]'
check "signins: one in progress stays" '[ -d "$HOME/.claude-accounts/.signin.fresh" ]'
check "signins: one just finished is left to its login" \
    '[ -d "$HOME/.claude-accounts/.signin.justdone" ] && [ "$(b .login.claudeAiOauth.refreshTokenExpiresAt)" != 4102444950000 ]'

# 16. A notification that could not be shown is tried again on the next run.
setup notifyfail 25200000 $((2 * 86400000))
FAKE_NOTIFY_FAIL=1 "$SCRIPT" refresh > "$S/out" 2>&1
check "notify failed: not counted as shown" '! grep -q "^ends:" "$HOME/.claude-accounts/.notified" 2>/dev/null'
"$SCRIPT" refresh > "$S/out" 2>&1
check "notify failed: tried again" '[ "$(notified)" = 2 ]'
"$SCRIPT" refresh > "$S/out" 2>&1
check "notify failed: once shown, not again" '[ "$(notified)" = 2 ]'

# 17. Another grant of the login that claude rotated comes back too.
setup design 3600000
jq '.login.designOauth = {accessToken: "design-access", refreshToken: "design-refresh", expiresAt: 4102444800000}' "$B" > "$S/c"
cp "$S/c" "$B"
FAKE_MODE=design "$SCRIPT" refresh > "$S/out" 2>&1
check "design: its rotation is kept" '[ "$(b .login.designOauth.refreshToken)" = design-ref-1 ]'

# 14. Nothing stored yet: it saves the live account and refreshes nothing.
setup empty 25200000
rm -f "$B"
"$SCRIPT" refresh > "$S/out" 2>&1; rc=$?
check "empty: exit 0" '[ "$rc" = 0 ]'
check "empty: live account saved" '[ -f "$HOME/.claude-accounts/a@example.com_org-aaaa.json" ]'
check "empty: no call" '[ "$(calls)" = 0 ]'

printf '%d passed, %d failed\n' "$passes" "$fails"
[ "$fails" -eq 0 ]
