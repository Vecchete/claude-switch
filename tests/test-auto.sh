#!/usr/bin/env bash
# Usage: test-auto.sh SCRIPT USAGE_PY. Automatic switching on a scratch HOME: what list shows, auto on and off,
# the watch and the hook Claude Code runs, against a stand-in usage endpoint and gateway on this machine.
set -u
SCRIPT=$(readlink -f "$1")
USAGE_PY=$(readlink -f "$2")
HERE=$(dirname "$(readlink -f "$0")")
T=$(mktemp -d "${TMPDIR:-/tmp}/claude-switch-auto.XXXXXX")
trap 'kill $(jobs -p) 2>/dev/null; rm -rf "$T"' EXIT
export HOME=$T/home SHELL=/bin/bash TZ=UTC FAKE_STATE=$T FAKE_DESKTOP_LOG=$T/desktop.log \
    FAKE_SYSTEMCTL_LOG=$T/systemctl.log no_proxy=127.0.0.1
unset CLAUDE_CONFIG_DIR ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL CLAUDE_CODE_OAUTH_TOKEN \
    CLAUDE_CODE_USE_BEDROCK CLAUDE_CODE_USE_VERTEX CLAUDE_CODE_USE_FOUNDRY ANTHROPIC_PROFILE http_proxy https_proxy \
    HTTP_PROXY HTTPS_PROXY
SHARE=$HOME/.local/share/claude-switch
STORE=$HOME/.claude-accounts
SETTINGS=$HOME/.claude/settings.json
mkdir -p "$HOME/.claude/sessions" "$STORE" "$SHARE" "$T/bin"
chmod 700 "$STORE"
# claude runs here only for a sign-in, which the stand-in makes with STUB_EMAIL and STUB_TOKEN. Any other call is
# logged.
printf '#!/bin/sh\necho "$*" >> %q\n' "$T/calls" > "$T/bin/claude-log"
cp "$HERE/stub-claude.sh" "$T/bin/claude"
export REAL_CLAUDE=$T/bin/claude-log
cp "$HERE/fake-systemctl.sh" "$T/bin/systemctl"
cp "$HERE/fake-notify.sh" "$T/bin/notify-send"
cp "$USAGE_PY" "$SHARE/usage.py"
cp "$HERE/fake-desktop.sh" "$SHARE/desktop.py"
chmod +x "$T/bin/"* "$SHARE/usage.py" "$SHARE/desktop.py"
export PATH=$T/bin:$PATH

printf '{}\n' > "$T/answers.json"
python3 -I "$HERE/fake-usage.py" "$T/port" "$T/answers.json" "$T/requests" &
for _ in $(seq 100); do [ -s "$T/port" ] && break; sleep 0.1; done
PORT=$(cat "$T/port")
export CLAUDE_SWITCH_ANTHROPIC_URL=http://127.0.0.1:$PORT

fails=0 passes=0
check() {
    if eval "$2" >/dev/null 2>&1; then passes=$((passes + 1)); else fails=$((fails + 1)); printf 'FAIL %s\n' "$1"; fi
}
now() { date +%s%3N; }
iso() { date -u -d "@$(($1 / 1000))" +%Y-%m-%dT%H:%M:%S+00:00; }
DAY=86400000
# What the stand-in answers for PATH and TOKEN. PATH TOKEN STATUS BODY
answer() {
    jq --arg key "$1 $2" --argjson status "$3" --argjson body "$4" '.[$key] = [$status, $body]' "$T/answers.json" \
        > "$T/answers.tmp" && mv "$T/answers.tmp" "$T/answers.json"
}
# A usage answer: the week at PERCENT until MS, and a 5-hour window at WINDOW percent when given. PERCENT MS [WINDOW]
week() {
    jq -n --argjson used "$1" --arg resets "$(iso "$2")" --arg window "${3:-}" '{seven_day: {utilization: $used,
        resets_at: $resets}, limits: ([{kind: "weekly_all", percent: $used, resets_at: $resets}] + (if $window == ""
        then [] else [{kind: "session", percent: ($window | tonumber), resets_at: $resets}] end))}'
}
budget() {
    jq -n --argjson spend "$1" --arg resets "$(iso "$2")" '{info: {spend: $spend, max_budget: 25, budget_reset_at: $resets}}'
}
requests() { grep -c . "$T/requests" 2>/dev/null || true; }
live_access() { jq -r .claudeAiOauth.accessToken "$HOME/.claude/.credentials.json"; }
env_token() { jq -r '.env // {} | (.ANTHROPIC_AUTH_TOKEN // "") + (.ANTHROPIC_API_KEY // "")' "$SETTINGS" 2>/dev/null; }
marked() { "$SCRIPT" list 2>/dev/null | grep -qE "^\* $1 "; }
calls() { tr '\n' '|' < "$T/desktop.log" 2>/dev/null; }
reading() { jq -r "$2" "$STORE/.usage/$1.json"; }
# Makes the reading of STEM say its week reset a moment ago, as it does once its reset time passed.
week_passed() {
    jq --argjson past $(($(now) - 1000)) '.weekly.resets = $past | .at = $past - 1000' "$STORE/.usage/$1.json" > "$T/r" \
        && mv "$T/r" "$STORE/.usage/$1.json"
}
# A Claude Code session open here: this shell, with the start time /proc gives it.
open_session() {
    jq -n --argjson pid $$ --arg started "$(sed 's/.*) //' "/proc/$$/stat" | cut -d' ' -f20)" \
        '{pid: $pid, procStart: $started, kind: "interactive", status: "busy"}' > "$HOME/.claude/sessions/$$.json"
}
close_session() { rm -f "$HOME/.claude/sessions/$$.json"; }
# Runs the hook as Claude Code does, with the JSON on stdin. The exit status in RC, stderr in $T/err.
hook() {
    printf '%s' "$1" | "$SCRIPT" hook > "$T/out" 2> "$T/err"
    RC=$?
}
refused() { [ -f "$STORE/.usage/$1.refused.json" ]; }
# The reading of STEM as old as the last run of the timer, so the next watch reads it again.
stale() { jq '.at = 0' "$STORE/.usage/$1.json" > "$T/r" && mv "$T/r" "$STORE/.usage/$1.json"; }
# The last switch, a minute ago: a hook's session failed after it.
old_switch() { printf '%s\n' $(($(now) - 60000)) > "$STORE/.switched"; }

# A machine on a@example.com (Max, with a 5-hour window), with an Ultra account, a gateway key with a budget
# and an Anthropic API key kept beside it.
jq -n '{claudeAiOauth: {accessToken: "A-token", refreshToken: "A-refresh", expiresAt: 4102444800000,
        refreshTokenExpiresAt: 4102444800000, rateLimitTier: "default_claude_max_5x"}}' > "$HOME/.claude/.credentials.json"
jq -n '{oauthAccount: {emailAddress: "a@example.com", accountUuid: "acct-a", organizationUuid: "org-a-0000",
        organizationType: "claude_max", organizationRateLimitTier: "default_claude_max_5x"}, hasCompletedOnboarding: true}' \
    > "$HOME/.claude.json"
jq -n '{name: "ultra", login: {claudeAiOauth: {accessToken: "U-token", refreshToken: "U-refresh", expiresAt: 4102444800000,
        refreshTokenExpiresAt: 4102444800000, rateLimitTier: "default_claude_max_20x"}},
        oauthAccount: {emailAddress: "u@example.com", accountUuid: "acct-u", organizationUuid: "org-u-0000",
        organizationType: "claude_max", organizationRateLimitTier: "default_claude_max_20x"}}' > "$STORE/u@example.com_org-u-00.json"
jq -n --arg base "http://127.0.0.1:$PORT/gw" '{kind: "key", name: "gw", env: {ANTHROPIC_AUTH_TOKEN: "G-secret",
        ANTHROPIC_BASE_URL: $base}}' > "$STORE/key_gw.json"
jq -n '{kind: "key", name: "api", env: {ANTHROPIC_API_KEY: "K-secret"}}' > "$STORE/key_api.json"
chmod 600 "$HOME/.claude/.credentials.json" "$HOME/.claude.json" "$STORE"/*.json
# Beside other hooks, the hook of a claude-switch installed elsewhere before, and the hook as Windows writes it.
WINDOWS_HOOK='{matcher: "rate_limit", hooks: [{type: "command", command: "C:\\Python\\pyw.exe",
    args: ["-3", "-I", "C:\\Users\\me\\.local\\share\\claude-switch\\claude-switch.py", "hook"]}]}'
jq -n '{env: {ENABLE_TOOL_SEARCH: "true"}, hooks: {Stop: [{hooks: [{type: "command", command: "other-stop"}]}],
        StopFailure: [{matcher: "server_error", hooks: [{type: "command", command: "other-failure"}]},
            {matcher: "rate_limit", hooks: [{type: "command", command: "/old/place/claude-switch hook"}]},
            '"$WINDOWS_HOOK"']}}' > "$SETTINGS"
chmod 600 "$SETTINGS"
A=a@example.com_org-a-00 U=u@example.com_org-u-00
answer /api/oauth/usage A-token 200 "$(week 60 $(($(now) + 3 * DAY)) 10)"
answer /api/oauth/usage U-token 200 "$(week 30 $(($(now) + 2 * DAY)))"
answer /key/info G-secret 200 "$(budget 3.2 $(($(now) + 10 * DAY)))"

# What each account has left, in list.
"$SCRIPT" list > "$T/out" 2>&1
check "list: the LEFT column" 'head -1 "$T/out" | grep -qE "UNTIL +LEFT$"'
check "list: the week of the active account" 'grep -E "^\* a@example.com " "$T/out" | grep -qE " 40% until [A-Z][a-z]{2} [0-9]{2}:[0-9]{2}$"'
check "list: the week of another account" 'grep -E "^  ultra " "$T/out" | grep -qE " 70% until "'
check "list: a gateway budget" 'grep -E "^  gw " "$T/out" | grep -q "\$21.80 until "'
check "list: an Anthropic API key is not readable" 'grep -E "^  api " "$T/out" | grep -qE " not readable$"'
check "list: says once why it is not readable" \
    '[ "$(grep -c "^The credit an Anthropic API key has left is not readable: the Billing page of the Claude Console shows it.$" "$T/out")" = 1 ]'
check "list prints no token" '! grep -qE "secret|-token" "$T/out"'
check "list: the readings are private" '[ "$(stat -c %a "$STORE/.usage")" = 700 ] && [ "$(stat -c %a "$STORE/.usage/$U.json")" = 600 ]'
rm -rf "$STORE/.usage"
CLAUDE_SWITCH_ANTHROPIC_URL=http://127.0.0.1:9 "$SCRIPT" list > "$T/out" 2>&1; rc=$?
check "list offline: still a list" '[ "$rc" -eq 0 ] && grep -E "^  ultra " "$T/out" | grep -qE " -$"'
rm -rf "$STORE/.usage"
printf 'q\n' | script -qfc "$SCRIPT" /dev/null > "$T/out" 2>&1
check "the prompt: what each account has left" 'grep -a "ultra" "$T/out" | grep -q "70% until "'
check "the prompt: says why an API key is not readable" 'grep -aq "The credit an Anthropic API key has left is not readable" "$T/out"'

# auto on: the flag, the hook, the timer, and the best account at once.
"$SCRIPT" auto > "$T/out" 2>&1
check "auto: off at first" 'grep -q "Automatic switching is off" "$T/out"'
rm -f "$T/desktop.log"
FAKE_DESKTOP_SWITCH=3,0 "$SCRIPT" auto on > "$T/out" 2>&1; rc=$?
check "auto on: exit 0" '[ "$rc" -eq 0 ]'
check "auto on: the flag" '[ -f "$STORE/.auto" ]'
check "auto on: one hook of claude-switch" '[ "$(jq "[.hooks.StopFailure[] | select(.hooks[0].command | endswith(\" hook\"))] | length" "$SETTINGS")" = 1 ]'
check "auto on: the hook as Windows writes it is replaced too" '[ "$(jq "[.hooks.StopFailure[] | select(.hooks[0].args)] | length" "$SETTINGS")" = 0 ]'
check "auto on: the hook runs this claude-switch" '[ "$(jq -r ".hooks.StopFailure[-1].hooks[0].command" "$SETTINGS")" = "$(printf %q "$SCRIPT") hook" ]'
check "auto on: in the background, waking the session" '[ "$(jq -r ".hooks.StopFailure[-1].hooks[0].asyncRewake" "$SETTINGS")" = true ]'
check "auto on: on the errors a switch can help" '[ "$(jq -r ".hooks.StopFailure[-1].matcher" "$SETTINGS")" = "rate_limit|billing_error|authentication_failed|unknown" ]'
check "auto on: the other hooks stay" '[ "$(jq -r ".hooks.Stop[0].hooks[0].command, .hooks.StopFailure[0].hooks[0].command" "$SETTINGS" | tr "\n" " ")" = "other-stop other-failure " ]'
check "auto on: the rest of settings stays" '[ "$(jq -r .env.ENABLE_TOOL_SEARCH "$SETTINGS")" = true ] && [ "$(stat -c %a "$SETTINGS")" = 600 ]'
check "auto on: the watch timer" 'grep -qx -- "--user enable --now claude-switch-watch.timer" "$T/systemctl.log"'
check "auto on: to Ultra, the best" '[ "$(live_access)" = U-token ] && grep -q "Switched to ultra: ultra is the best account with room." "$T/out"'
check "auto on: Desktop restarted without a question" '[ "$(calls)" = "switch acct-u|quit|switch acct-u|start|" ]'
check "auto on: a notification" 'grep -q "Switched to ultra" "$T/notify"'
BEFORE=$(md5sum < "$SETTINGS")
"$SCRIPT" auto on > "$T/out" 2>&1
check "auto on again: settings unchanged" '[ "$(md5sum < "$SETTINGS")" = "$BEFORE" ]'
check "auto on again: stays" 'grep -q "It stays on ultra, the best account with room." "$T/out"'
"$SCRIPT" auto > "$T/out" 2>&1
check "auto: says it's on, and the best" 'grep -q "Automatic switching is on" "$T/out" && grep -q "The best account with room now is ultra." "$T/out"'

# The watch: nothing without an open session, then the best account with room.
answer /api/oauth/usage U-token 200 "$(week 100 $(($(now) + 2 * DAY)))"
stale "$U"
before=$(requests)
"$SCRIPT" watch > "$T/out" 2>&1; rc=$?
check "watch, no session: nothing read" '[ "$rc" -eq 0 ] && [ "$(requests)" = "$before" ] && [ "$(live_access)" = U-token ]'
open_session
rm -f "$T/desktop.log"
stale "$U"
"$SCRIPT" watch > "$T/out" 2>&1; rc=$?
check "watch: a full week goes to the next rank, a key" '[ "$rc" -eq 0 ] && [ "$(env_token)" = G-secret ]'
check "watch: says why" 'grep -qE "^Switched to gw: ultra reached its weekly limit, until " "$T/out"'
check "watch: a key leaves Desktop alone" '[ ! -s "$T/desktop.log" ]'
before=$(requests)
# The log past 1 MB keeps its last lines.
yes "$(printf '%0600d' 0)" | head -n 2200 > "$STORE/.auto.log"
"$SCRIPT" watch > "$T/out" 2>&1
check "watch: on the best already, nothing to say" '[ ! -s "$T/out" ] && marked gw'
check "watch: a log past 1 MB keeps its last lines" '[ "$(grep -c "^0" "$STORE/.auto.log")" = 2000 ]'
check "watch: a full week isn't read again before it resets" '! tail -n +$((before + 1)) "$T/requests" | grep -q U-token'
jq --argjson at $(($(now) - 250000)) '.at = $at' "$STORE/.usage/key_gw.json" > "$T/r" && mv "$T/r" "$STORE/.usage/key_gw.json"
before=$(requests)
"$SCRIPT" watch > "$T/out" 2>&1
check "watch: what the last run read is read again" 'tail -n +$((before + 1)) "$T/requests" | grep -q G-secret'
answer /api/oauth/usage U-token 200 "$(week 5 $(($(now) + 7 * DAY)))"
week_passed "$U"
"$SCRIPT" watch > "$T/out" 2>&1
check "watch: back to Ultra once its week reset" '[ "$(live_access)" = U-token ] && [ -z "$(env_token)" ] && marked ultra'

# A switch typed by hand holds while its account has room.
old_switch
"$SCRIPT" a@example.com > "$T/out" 2>&1
check "by hand: switched" 'marked a@example.com'
check "by hand: held" '[ "$(cat "$STORE/.hold")" = "$A.json" ]'
check "by hand: the time of the switch, for the hook" '[ "$(cat "$STORE/.switched")" -gt $(($(now) - 30000)) ]'
"$SCRIPT" watch > "$T/out" 2>&1
check "by hand: the watch keeps it" 'marked a@example.com && [ ! -s "$T/out" ]'
answer /api/oauth/usage A-token 200 "$(week 100 $(($(now) + 3 * DAY)) 10)"
stale "$A"
"$SCRIPT" watch > "$T/out" 2>&1
check "by hand: let go once it runs out" 'marked ultra && [ ! -e "$STORE/.hold" ]'

# Nothing has room: the machine stays, one notification a day.
answer /api/oauth/usage U-token 200 "$(week 100 $(($(now) + 2 * DAY)))"
week_passed "$U"
answer /key/info G-secret 200 "$(budget 25.5 $(($(now) + 10 * DAY)))"
stale key_gw
"$SHARE/usage.py" refuse "$STORE/key_api.json" billing_error >/dev/null
: > "$T/notify"
"$SCRIPT" watch > "$T/out" 2>&1; rc=$?
check "nothing has room: stays" '[ "$rc" -eq 0 ] && marked ultra'
check "nothing has room: a notification" 'grep -q "No Claude account has room" "$T/notify"'
check "nothing has room: says which comes back first" 'grep -q "The first to have room again: api" "$T/notify"'
"$SCRIPT" watch > "$T/out" 2>&1
check "nothing has room: once a day" '[ "$(grep -c "No Claude account" "$T/notify")" = 1 ]'

# The hook: a session meets the limit of the active account.
rm -f "$STORE/.usage"/*.refused.json
answer /api/oauth/usage U-token 200 "$(week 30 $(($(now) + 2 * DAY)))"
answer /key/info G-secret 200 "$(budget 3.2 $(($(now) + 10 * DAY)))"
answer /api/oauth/usage A-token 200 "$(week 60 $(($(now) + 3 * DAY)) 10)"
rm -rf "$STORE/.usage"
"$SCRIPT" watch > /dev/null 2>&1
check "hook setup: on Ultra" 'marked ultra'
answer /api/oauth/usage U-token 200 "$(week 100 $(($(now) + 2 * DAY)))"
# A log past 1 MB, cut before the hook writes to it.
yes "$(printf '%0600d' 0)" | head -n 2200 > "$STORE/.auto.log"
old_switch
FAILED_AT=$(now)
ANTHROPIC_AUTH_TOKEN=from-the-session CLAUDE_CODE_OAUTH_TOKEN=from-the-session \
    hook '{"session_id": "s1", "hook_event_name": "StopFailure", "error": "rate_limit", "last_assistant_message": "You'"'"'ve hit your weekly limit"}'
check "hook: exit 2 wakes the session" '[ "$RC" -eq 2 ]'
check "hook: the account that stopped is refused until its week resets" 'refused "$U" && [ "$(jq -r .until "$STORE/.usage/$U.refused.json")" -gt $(($(now) + DAY)) ]'
check "hook: the next account is live" '[ "$(env_token)" = G-secret ]'
check "hook: one sentence for Claude" '[ "$(grep -c . "$T/err")" = 1 ] && grep -qE "^claude-switch moved this machine to gw: ultra reached its weekly limit, until .*\. The request that failed can go out again now: carry on with the task where it stopped\.$" "$T/err"'
check "hook: nothing on stdout" '[ ! -s "$T/out" ]'
check "hook: claude-switch said the rest in its log" 'grep -q "Switched to gw, a gateway key" "$STORE/.auto.log"'
check "hook: a log past 1 MB keeps its last lines" '[ "$(grep -c "^0" "$STORE/.auto.log")" = 2000 ]'
check "hook: a wake counted" '[ "$(grep -c . "$STORE/.woken/s1")" = 1 ]'

# A second session failed before that switch: it only has to send its request again.
printf '{"type":"assistant","isApiErrorMessage":true,"timestamp":"%s"}\n' "$(date -u -d "@$((FAILED_AT / 1000 - 5))" +%Y-%m-%dT%H:%M:%S.000Z)" \
    > "$T/transcript.jsonl"
hook "$(jq -nc --arg path "$T/transcript.jsonl" '{session_id: "s2", error: "rate_limit", last_assistant_message: "x", transcript_path: $path}')"
check "a second session: woken" '[ "$RC" -eq 2 ] && grep -q "has moved this machine to gw since the request failed" "$T/err"'
check "a second session: the new account isn't refused for it" '! refused key_gw && [ "$(env_token)" = G-secret ]'
# A transcript whose last API error is an hour old doesn't say when this one failed.
printf '{"type":"assistant","isApiErrorMessage":true,"timestamp":"%s"}\n{"type":"user"}\n' \
    "$(date -u -d "1 hour ago" +%Y-%m-%dT%H:%M:%S.000Z)" > "$T/transcript.jsonl"
answer /key/info G-secret 200 "$(budget 25.5 $(($(now) + 10 * DAY)))"
old_switch
hook "$(jq -nc --arg path "$T/transcript.jsonl" '{session_id: "s2", error: "unknown", last_assistant_message: "Budget has been exceeded!", transcript_path: $path}')"
check "an old error in the transcript: this failure still refuses" '[ "$RC" -eq 2 ] && refused key_gw'
rm -f "$STORE/.usage/key_gw.refused.json"
answer /key/info G-secret 200 "$(budget 3.2 $(($(now) + 10 * DAY)))"
"$SCRIPT" gw > /dev/null 2>&1
rm -f "$STORE/.hold"

# A gateway's budget comes back as an unknown error.
hook '{"session_id": "s3", "error": "unknown", "last_assistant_message": "API Error: 500 something else"}'
check "unknown error: nothing" '[ "$RC" -eq 0 ] && [ ! -s "$T/err" ] && ! refused key_gw'
answer /key/info G-secret 200 "$(budget 25.5 $(($(now) + 10 * DAY)))"
old_switch
hook '{"session_id": "s3", "error": "unknown", "last_assistant_message": "API Error: 422 Budget has been exceeded! Key=me Current cost: 25.5"}'
check "a spent budget: refused until it resets" '[ "$RC" -eq 2 ] && refused key_gw && [ "$(jq -r .until "$STORE/.usage/key_gw.refused.json")" -gt $(($(now) + 9 * DAY)) ]'
check "a spent budget: the next key" '[ "$(env_token)" = K-secret ]'
rm -f "$T/desktop.log"
old_switch
FAKE_DESKTOP_SWITCH=3,0 hook '{"session_id": "s4", "error": "billing_error", "last_assistant_message": "Credit balance is too low"}'
check "no credit: refused" '[ "$RC" -eq 2 ] && refused key_api && grep -q "api ran out of credit" "$T/err"'
check "no credit: on to the plan with a window" '[ "$(live_access)" = A-token ] && [ -z "$(env_token)" ]'
check "no credit: Desktop follows without a question" '[ "$(calls)" = "switch acct-a|quit|switch acct-a|start|inside|" ]'
"$SCRIPT" list > "$T/out" 2>&1
check "list: a refused API key says until when, and no note" \
    'grep -E "^  api " "$T/out" | grep -q " refused until " && ! grep -q "not readable" "$T/out"'
cp "$STORE/.usage/key_api.refused.json" "$T/api.refused"
printf 'K-secret\n' | "$SCRIPT" key renew api > "$T/out" 2>&1
check "no credit: a new key ends the wait" '! refused key_api'
cp "$T/api.refused" "$STORE/.usage/key_api.refused.json"

# Nothing left: no wake, Claude Code waits for the reset itself.
answer /api/oauth/usage A-token 200 "$(week 100 $(($(now) + 3 * DAY)) 10)"
old_switch
hook '{"session_id": "s5", "error": "rate_limit", "last_assistant_message": "x"}'
check "nothing left: no wake" '[ "$RC" -eq 0 ] && [ ! -s "$T/err" ]'

# A session every account refuses isn't woken more than 3 times in 10 minutes.
rm -f "$STORE/.usage"/*.refused.json
rm -rf "$STORE/.usage"
answer /api/oauth/usage A-token 200 "$(week 60 $(($(now) + 3 * DAY)) 10)"
answer /api/oauth/usage U-token 200 "$(week 30 $(($(now) + 2 * DAY)))"
mkdir -p "$STORE/.woken"
for _ in 1 2 3; do now >> "$STORE/.woken/s6"; done
hook '{"session_id": "s6", "error": "rate_limit", "last_assistant_message": "x"}'
check "3 wakes in 10 minutes: no fourth" '[ "$RC" -eq 0 ] && [ ! -s "$T/err" ]'

# The order the kinds of account rank in.
"$SCRIPT" auto order > "$T/out" 2>&1
check "auto order: Ultra, then keys, then the other plans at first" \
    'grep -q "in this order: Ultra subscriptions, then API and gateway keys, then Max and the other plans." "$T/out"'
"$SCRIPT" auto order ultra > "$T/out" 2>&1
check "auto order ultra: to Ultra at once" 'marked ultra'
"$SCRIPT" auto order max > "$T/out" 2>&1
check "auto order max: kept, the others after" '[ "$(cat "$STORE/.auto-order")" = "max ultra key" ]'
check "auto order max: says it" 'grep -q "order now: Max and the other plans, then Ultra subscriptions, then API and gateway keys." "$T/out"'
check "auto order max: to the plan with a window at once" 'marked a@example.com'
"$SCRIPT" auto > "$T/out" 2>&1
check "auto: says the order" 'grep -q "It ranks the accounts in this order: Max and the other plans, then" "$T/out"'
"$SCRIPT" auto order api ultra > "$T/out" 2>&1
check "auto order api: a key" '[ "$(cat "$STORE/.auto-order")" = "key ultra max" ]'
"$SCRIPT" auto order pro > "$T/out" 2>&1; rc=$?
check "auto order: an unknown kind is refused" '[ "$rc" -ne 0 ] && grep -q "pro is not a kind of account: ultra, max or key." "$T/out" && [ "$(cat "$STORE/.auto-order")" = "key ultra max" ]'
"$SCRIPT" auto order max max > "$T/out" 2>&1; rc=$?
check "auto order: a kind twice is refused" '[ "$rc" -ne 0 ] && grep -q "max is twice in the order." "$T/out"'
"$SCRIPT" auto order ultra key max > /dev/null 2>&1

# A session of Claude Desktop meets the limit: the machine moves on, Desktop keeps its account, and the session isn't
# woken, since only a restart of Desktop, which would end it, gives it another login.
"$SHARE/usage.py" refuse "$STORE/key_api.json" billing_error > /dev/null
answer /api/oauth/usage U-token 200 "$(week 100 $(($(now) + 2 * DAY)))"
old_switch
rm -f "$T/desktop.log"
: > "$T/notify"
FAKE_DESKTOP_SWITCH=6 FAKE_DESKTOP_INSIDE=0 hook '{"session_id": "s8", "error": "rate_limit", "last_assistant_message": "x"}'
check "a session of Desktop: the machine moves on" 'marked a@example.com'
check "a session of Desktop: Desktop left alone" '[ "$(calls)" = "switch acct-a|inside|" ]'
check "a session of Desktop: not woken" '[ "$RC" -eq 0 ] && [ ! -s "$T/err" ] && grep -q "it is not woken" "$STORE/.auto.log"'
check "a session of Desktop: the notification says how to bring Desktop along" \
    'grep -q "Claude Desktop stays on its account: close it and run claude-switch a@example.com to bring it along." "$T/notify"'

# Adding with automatic switching on: the new account ranks with the others at once, and between two accounts of the
# same kind the one the machine is on stays until it runs out.
rm -f "$STORE/.usage/$U.refused.json"
answer /api/oauth/usage U-token 200 "$(week 30 $(($(now) + 2 * DAY)))"
stale "$U"
"$SCRIPT" watch > /dev/null 2>&1
check "adding: on the Ultra first" 'marked ultra'
answer /api/oauth/usage U2-access 200 "$(week 10 $(($(now) + DAY)))"
before=$(requests)
rm -f "$T/desktop.log"
STUB_EMAIL=u2@example.com STUB_TOKEN=U2 STUB_ORG=org-u2-0000 "$SCRIPT" login > "$T/out" 2>&1; rc=$?
check "adding: the new account is read at once" '[ "$rc" -eq 0 ] && tail -n +$((before + 1)) "$T/requests" | grep -q U2-access'
check "adding: says the machine stays" 'grep -q "^Added u2@example.com. Automatic switching ranks it with the others:$" "$T/out" \
    && grep -q "^It stays on ultra, the best account with room.$" "$T/out"'
check "a tie: the Ultra in use stays before one whose week resets sooner" 'marked ultra && [ ! -s "$T/desktop.log" ]'
answer /api/oauth/usage U-token 200 "$(week 100 $(($(now) + 2 * DAY)))"
stale "$U"
"$SCRIPT" watch > /dev/null 2>&1
check "a tie: the next Ultra once the one in use runs out" 'marked u2@example.com'
answer /api/oauth/usage U-token 200 "$(week 5 $(($(now) + DAY / 2)))"
week_passed "$U"
"$SCRIPT" watch > "$T/out" 2>&1
check "a tie: the Ultra in use stays, though another resets sooner" 'marked u2@example.com && [ ! -s "$T/out" ]'

# An account added with --switch is used at once, and nothing holds it there, not even the account a switch by hand
# held before: the next check moves the machine on when another account comes first.
"$SCRIPT" a@example.com > /dev/null 2>&1
check "adding with --switch: a hold before" '[ -f "$STORE/.hold" ]'
answer /key/info G3-secret 200 "$(budget 1.0 $(($(now) + 10 * DAY)))"
printf 'G3-secret\n' | "$SCRIPT" key add gw3 --base-url "http://127.0.0.1:$PORT/gw3" --switch > "$T/out" 2>&1
check "adding with --switch: on the new key at once" '[ "$(env_token)" = G3-secret ] && grep -q "^Kept the gateway key gw3.$" "$T/out"'
check "adding with --switch: nothing holds it, nor the account held before" '[ ! -e "$STORE/.hold" ]'
check "adding with --switch: says the next check may move on" \
    'grep -q "at its next check it goes to the best account with room, which may be another one." "$T/out"'
# Neither Ultra is the one in use: the one whose week resets first goes first.
"$SCRIPT" watch > "$T/out" 2>&1
check "adding with --switch: the next check moves on to the best" \
    'marked ultra && [ -z "$(env_token)" ] && grep -q "^Switched to ultra: " "$T/out"'
# Added without a switch while an account is held: it stays held.
"$SCRIPT" a@example.com > /dev/null 2>&1
printf 'G4-secret\n' | "$SCRIPT" key add gw4 --base-url "http://127.0.0.1:$PORT/gw4" > "$T/out" 2>&1
check "adding while an account is held: it stays" \
    'marked a@example.com && grep -q "^It stays on a@example.com, chosen by hand, until it runs out.$" "$T/out"'

# auto off: the hook and the timer go, the rest stays, with a hook as Windows writes it.
jq ".hooks.StopFailure += [$WINDOWS_HOOK]" "$SETTINGS" > "$T/s" && cat "$T/s" > "$SETTINGS"
"$SCRIPT" auto off > "$T/out" 2>&1
check "auto off: the flag goes" '[ ! -e "$STORE/.auto" ]'
check "auto off: only the hook of claude-switch goes" '[ "$(jq -c "[.hooks.StopFailure[].hooks[0].command]" "$SETTINGS")" = "[\"other-failure\"]" ] && [ "$(jq -r ".hooks.Stop[0].hooks[0].command" "$SETTINGS")" = other-stop ]'
check "auto off: the timer stops" 'grep -qx -- "--user disable --now claude-switch-watch.timer" "$T/systemctl.log"'
before=$(live_access)
hook '{"session_id": "s7", "error": "rate_limit", "last_assistant_message": "x"}'
check "auto off: the hook does nothing" '[ "$RC" -eq 0 ] && [ "$(live_access)" = "$before" ]'
# The Ultra has room again: a watch that ran would switch to it.
rm -f "$STORE/.usage/$U.refused.json"
answer /api/oauth/usage U-token 200 "$(week 30 $(($(now) + 2 * DAY)))"
stale "$U"
"$SCRIPT" watch > "$T/out" 2>&1
check "auto off: the watch does nothing" '[ ! -s "$T/out" ] && [ "$(live_access)" = "$before" ]'
"$SCRIPT" auto off > "$T/out" 2>&1
check "auto off twice: settings left alone" '[ "$(jq "[.hooks.StopFailure[]] | length" "$SETTINGS")" = 1 ]'

# A settings.json in a folder this user can't write: auto on refuses before anything changes.
mkdir -p "$HOME/store"
mv "$SETTINGS" "$HOME/store/settings.json"
ln -s "$HOME/store/settings.json" "$SETTINGS"
chmod 555 "$HOME/store"
"$SCRIPT" auto on > "$T/out" 2>&1; rc=$?
check "read-only settings: refused" '[ "$rc" -ne 0 ] && grep -q "can.t write" "$T/out"'
check "read-only settings: automatic switching stays off" '[ ! -e "$STORE/.auto" ]'
chmod 755 "$HOME/store"
close_session

printf '%d passed, %d failed\n' "$passes" "$fails"
[ "$fails" -eq 0 ]
