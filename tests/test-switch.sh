#!/usr/bin/env bash
# Usage: test-switch.sh SCRIPT. Fake logins on a scratch HOME, the real claude for auth status.
set -u
SCRIPT=$(readlink -f "$1")
HERE=$(dirname "$(readlink -f "$0")")
T=$(mktemp -d "${TMPDIR:-/tmp}/claude-switch-test.XXXXXX")
trap 'rm -rf "$T"' EXIT
export REAL_CLAUDE
REAL_CLAUDE=$(readlink -f "$HOME/.local/bin/claude")
export HOME=$T/home SHELL=/bin/bash
unset CLAUDE_CONFIG_DIR ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN CLAUDE_CODE_OAUTH_TOKEN
mkdir -p "$HOME/.claude/rules" "$HOME/.claude/projects/-x" "$T/bin" "$T/elsewhere"
cp "$HERE/stub-claude.sh" "$T/bin/claude"
chmod +x "$T/bin/claude"
export PATH=$T/bin:$PATH

printf 'rule\n' > "$HOME/.claude/rules/a.md"
printf 'session\n' > "$HOME/.claude/projects/-x/s.jsonl"
printf '{}\n' > "$HOME/.claude/settings.json"
jq -n '{claudeAiOauth: {accessToken: "A-access", refreshToken: "A-refresh", expiresAt: 4102444800000,
        refreshTokenExpiresAt: 4102444800000, scopes: ["user:inference", "user:profile"], subscriptionType: "max"},
    mcpOAuth: {"gitlab|1": {accessToken: "mcp-token"}}}' > "$HOME/.claude/.credentials.json"
jq -n '{oauthAccount: {emailAddress: "a@example.com", accountUuid: "acct-a", organizationUuid: "org-of-a-0000"},
    hasCompletedOnboarding: true, mcpServers: {x: {command: "y"}}, projects: {"/p": {t: 1}}, theme: "light", numStartups: 7,
    modelAccessCache: ["old"], clientDataCacheSlots: {a: 1}}' > "$HOME/.claude.json"
chmod 600 "$HOME/.claude/.credentials.json" "$HOME/.claude.json"

fails=0 passes=0
check() {
    if eval "$2" >/dev/null 2>&1; then passes=$((passes + 1)); else fails=$((fails + 1)); printf 'FAIL %s\n' "$1"; fi
}
tree_hash() {
    (cd "$HOME/.claude" && find rules projects settings.json -type f -exec md5sum {} + | sort | md5sum)
}
live_cred() { jq -r "$1" "$HOME/.claude/.credentials.json"; }
live_conf() { jq -r "$1" "$HOME/.claude.json"; }
status_email() { (cd "$1" && "$REAL_CLAUDE" auth status </dev/null 2>/dev/null | jq -r .email); }

BEFORE=$(tree_hash)
MCP=$(live_cred .mcpOAuth)

"$SCRIPT" list > "$T/out" 2>&1
check "list shows the live account" 'grep -q "\* a@example.com" "$T/out"'
check "store folder is private" '[ "$(stat -c %a "$HOME/.claude-accounts")" = 700 ]'
check "account file is private" '[ "$(stat -c %a "$HOME"/.claude-accounts/a@example.com_*.json)" = 600 ]'
check "mcp logins are not in the account file" '! grep -q mcp-token "$HOME"/.claude-accounts/*.json'

# The command is claude-switch, and every hint it prints names it so.
"$SCRIPT" help > "$T/out" 2>&1
check "help names the command claude-switch" 'grep -q "^  claude-switch NAME " "$T/out"'
check "help lists itself" 'grep -q "^  claude-switch help " "$T/out"'
check "no hint names claude-switch.sh" '! grep -q "claude-switch\.sh" "$SCRIPT"'

STUB_EMAIL=b@example.com STUB_TOKEN=B "$SCRIPT" login > "$T/out" 2>&1
check "login adds the account" '[ "$(ls "$HOME"/.claude-accounts/*.json | wc -l)" = 2 ]'
check "login does not switch" '[ "$(live_cred .claudeAiOauth.accessToken)" = A-access ]'
check "login leaves no folder behind" '[ -z "$(ls -A "$HOME/.claude-accounts" | grep -vxE ".*json|\.lock")" ]'

"$SCRIPT" rename b@example.com second > "$T/out" 2>&1
check "rename" '"$SCRIPT" list | grep -q "second *b@example.com"'

(cd / && "$SCRIPT" second) > "$T/out" 2>&1
check "switch reports it" 'grep -q "Switched to second" "$T/out"'
check "no warning from the real status" '! grep -q "reports" "$T/out"'
check "login is the new one" '[ "$(live_cred .claudeAiOauth.accessToken)" = B-access ]'
check "account is the new one" '[ "$(live_conf .oauthAccount.emailAddress)" = b@example.com ]'
check "mcp logins stay" '[ "$(live_cred .mcpOAuth)" = "$MCP" ]'
check "mcp servers stay" '[ "$(live_conf .mcpServers.x.command)" = y ]'
check "projects stay" '[ "$(live_conf ".projects[\"/p\"].t")" = 1 ]'
check "settings stay" '[ "$(live_conf .theme)" = light ]'
check "account caches cleared" '[ "$(live_conf .modelAccessCache)" = null ]'
check "the rest of ~/.claude is untouched" '[ "$(tree_hash)" = "$BEFORE" ]'
check "credentials stay private" '[ "$(stat -c %a "$HOME/.claude/.credentials.json")" = 600 ]'
check "config stays private" '[ "$(stat -c %a "$HOME/.claude.json")" = 600 ]'
check "no lock left" '[ ! -e "$HOME/.claude.lock" ] && [ ! -e "$HOME/.claude.json.lock" ] && [ ! -e "$HOME/.claude/.oauth_refresh.lock" ]'
check "real status from one folder" '[ "$(status_email /)" = b@example.com ]'
check "real status from another folder" '[ "$(status_email "$T/elsewhere")" = b@example.com ]'
check "no temp file left" '[ ! -e "$HOME/.claude.json.switch" ] && [ ! -e "$HOME/.claude/.credentials.json.switch" ]'

jq '.claudeAiOauth.refreshToken = "B-rotated"' "$HOME/.claude/.credentials.json" > "$T/c" && cp "$T/c" "$HOME/.claude/.credentials.json"
"$SCRIPT" a@example.com > "$T/out" 2>&1
check "back to the first by email" '[ "$(live_cred .claudeAiOauth.accessToken)" = A-access ]'
"$SCRIPT" second > "$T/out" 2>&1
check "a rotated token survives the way out and back" '[ "$(live_cred .claudeAiOauth.refreshToken)" = B-rotated ]'

# Max 20x is called Ultra since 30/10/2026, and claude still says max for it. The profile, which claude
# fetches again at each start, comes before the login, which a refresh leaves as it was. The plan is the
# word after the email: an account with no name shows it twice.
plan() { "$SCRIPT" list | awk -v who="$1" '{ for (i = 1; i < NF; i++) if ($i == who) found = $(i + 1) } END { print found }'; }
edit() { jq "$1" "$2" > "$T/c" && cp "$T/c" "$2"; }
edit '.claudeAiOauth.rateLimitTier = "default_claude_max_5x"' "$HOME/.claude/.credentials.json"
edit '.oauthAccount += {organizationType: "claude_max", organizationRateLimitTier: "default_claude_max_20x"}' "$HOME/.claude.json"
for f in "$HOME"/.claude-accounts/b@example.com_*.json; do
    edit '.login.claudeAiOauth.rateLimitTier = "default_claude_max_5x"
        | .oauthAccount += {organizationType: "claude_max", organizationRateLimitTier: "default_claude_max_20x"}' "$f"
done
for f in "$HOME"/.claude-accounts/a@example.com_*.json; do
    edit '.login.claudeAiOauth.rateLimitTier = "default_claude_max_20x"
        | .oauthAccount += {organizationType: "claude_max", organizationRateLimitTier: "default_claude_max_5x"}' "$f"
done
check "plan: the 20x tier of the profile is Ultra, before the 5x of the login" '[ "$(plan b@example.com)" = ultra ]'
check "plan: the 5x tier of the profile is Max, before the 20x of the login" '[ "$(plan a@example.com)" = max ]'
for f in "$HOME"/.claude-accounts/a@example.com_*.json; do edit 'del(.oauthAccount.organizationRateLimitTier)' "$f"; done
check "plan: with no tier in the profile, the one of the login" '[ "$(plan a@example.com)" = ultra ]'
for f in "$HOME"/.claude-accounts/a@example.com_*.json; do edit '.oauthAccount.organizationType = "claude_pro"' "$f"; done
check "plan: the profile names the plan before the login" '[ "$(plan a@example.com)" = pro ]'

"$SCRIPT" nobody > "$T/out" 2>&1
check "unknown name fails" '[ $? -ne 0 ] || grep -q "No account called nobody" "$T/out"'
check "unknown name: the hint names claude-switch login" 'grep -q "Add it with: claude-switch login$" "$T/out"'
check "unknown name changes nothing" '[ "$(live_cred .claudeAiOauth.accessToken)" = B-access ]'

jq '.login.claudeAiOauth.refreshTokenExpiresAt = 1000' "$HOME"/.claude-accounts/a@example.com_*.json > "$T/c"
cp "$T/c" "$HOME"/.claude-accounts/a@example.com_*.json
"$SCRIPT" a@example.com > "$T/out" 2>&1
check "a dead login is refused" 'grep -q "ended on" "$T/out"'
check "a dead login: the hint names it" 'grep -q "Sign in again: claude-switch login a@example.com$" "$T/out"'
check "a dead login changes nothing" '[ "$(live_cred .claudeAiOauth.accessToken)" = B-access ]'

STUB_LOGIN_RC=1 STUB_EMAIL=c@example.com STUB_TOKEN=C "$SCRIPT" login > "$T/out" 2>&1
check "failed sign-in adds nothing" '[ "$(ls "$HOME"/.claude-accounts/*.json | wc -l)" = 2 ]'
check "failed sign-in leaves no folder" '[ -z "$(ls -A "$HOME/.claude-accounts" | grep -vxE ".*json|\.lock")" ]'

STUB_EMAIL=b@example.com STUB_TOKEN=B2 "$SCRIPT" login > "$T/out" 2>&1
check "renewing the live account reaches the live login" '[ "$(live_cred .claudeAiOauth.accessToken)" = B2-access ]'
check "renewing keeps the name" '"$SCRIPT" list | grep -q "second"'
check "renewing keeps the mcp logins" '[ "$(live_cred .mcpOAuth)" = "$MCP" ]'

mkdir "$HOME/.claude.json.lock"
touch -d "2 minutes ago" "$HOME/.claude.json.lock"
jq '.login.claudeAiOauth.refreshTokenExpiresAt = 4102444800000' "$HOME"/.claude-accounts/a@example.com_*.json > "$T/c"
cp "$T/c" "$HOME"/.claude-accounts/a@example.com_*.json
"$SCRIPT" a@example.com > "$T/out" 2>&1
check "a dead lock is taken over" '[ "$(live_cred .claudeAiOauth.accessToken)" = A-access ]'

printf '2\n' | script -qfc "$SCRIPT" /dev/null > "$T/out" 2>&1
check "the prompt shows the accounts" 'grep -q "Claude Code accounts" "$T/out"'
check "the prompt marks the active one" 'grep -a "a@example.com" "$T/out" | grep -q active'
check "the prompt switches" '[ "$(live_cred .claudeAiOauth.accessToken)" = B2-access ]'

# A switch that cannot write the account leaves no half switch.
cp "$HOME/.claude.json" "$T/config"
printf 'not json\n' > "$HOME/.claude.json"
"$SCRIPT" a@example.com > "$T/out" 2>&1; rc=$?
check "broken config: refused" '[ "$rc" -ne 0 ]'
check "broken config: login unchanged" '[ "$(live_cred .claudeAiOauth.accessToken)" = B2-access ]'
cp "$T/config" "$HOME/.claude.json"

# A login key a later claude adds is announced, since the switch does not move it.
jq '.futureLogin = {token: "x"}' "$HOME/.claude/.credentials.json" > "$T/c" && cp "$T/c" "$HOME/.claude/.credentials.json"
"$SCRIPT" a@example.com > "$T/out" 2>&1
check "unknown login key: announced" 'grep -q futureLogin "$T/out"'
jq 'del(.futureLogin)' "$HOME/.claude/.credentials.json" > "$T/c" && cp "$T/c" "$HOME/.claude/.credentials.json"

# What claude's own logout drops with the login belongs to the account: it moves with it.
jq '.trustedDeviceToken = "device-of-a" | .organizationUuid = "org-of-a-0000"' "$HOME/.claude/.credentials.json" > "$T/c"
cp "$T/c" "$HOME/.claude/.credentials.json"
"$SCRIPT" second > "$T/out" 2>&1
check "device token: stays with its account" '[ "$(live_cred .trustedDeviceToken)" = null ]'
"$SCRIPT" a@example.com > "$T/out" 2>&1
check "device token: comes back with it" '[ "$(live_cred .trustedDeviceToken)" = device-of-a ]'
check "device token: no warning" '! grep -q "also holds" "$T/out"'

# An interrupt between the two renames of a switch: it ends on one account, never half on each.
mkdir -p "$T/mvbin"
cp "$HERE/fake-mv.sh" "$T/mvbin/mv"
chmod +x "$T/mvbin/mv"
B_FILE=$(ls "$HOME"/.claude-accounts/b@example.com_*.json)
PATH=$T/mvbin:$PATH FAKE_MV_TARGET=$HOME/.claude/.credentials.json "$SCRIPT" second > "$T/out" 2>&1
check "interrupted switch: one account on both files" \
    '[ "$(live_conf .oauthAccount.emailAddress)" = b@example.com ] && [ "$(live_cred .claudeAiOauth.refreshToken)" = "$(jq -r .login.claudeAiOauth.refreshToken "$B_FILE")" ]'
"$SCRIPT" a@example.com > "$T/out" 2>&1
check "interrupted switch: the way back works" '[ "$(live_conf .oauthAccount.emailAddress)" = a@example.com ]'

# login NAME asks the sign-in page for that account, and says when another one signed in.
STUB_ARGS_LOG=$T/args STUB_EMAIL=b@example.com STUB_TOKEN=B4 "$SCRIPT" login second > "$T/out" 2>&1
check "login NAME: asks for its email" 'grep -q -- "--email b@example.com" "$T/args"'
check "login NAME: renews it" '[ "$(jq -r .login.claudeAiOauth.accessToken "$HOME"/.claude-accounts/b@example.com_*.json)" = B4-access ]'
STUB_EMAIL=c@example.com STUB_TOKEN=C2 "$SCRIPT" login second > "$T/out" 2>&1
check "login NAME: says another account signed in" 'grep -q "c@example.com" "$T/out" && grep -q "not b@example.com" "$T/out"'
"$SCRIPT" login nobody > "$T/out" 2>&1; rc=$?
check "login NAME: unknown name refused" '[ "$rc" -ne 0 ] && grep -q "No account called nobody" "$T/out"'

# claude.ai in Chrome goes to the account of the sign-in, and back to the live one however it ends.
mkdir -p "$HOME/.local/share/claude-switch"
cp "$HERE/fake-chrome-host.sh" "$HOME/.local/share/claude-switch/chrome-host.py"
chmod +x "$HOME/.local/share/claude-switch/chrome-host.py"
FAKE_WEB_LOG=$T/web STUB_LOGIN_RC=1 "$SCRIPT" login second > "$T/out" 2>&1
check "login NAME given up: Chrome went to it" '[ "$(sed -n 1p "$T/web")" = "--switch acct" ]'
check "login NAME given up: Chrome came back" '[ "$(sed -n 2p "$T/web")" = "--switch acct-a" ] && [ "$(wc -l < "$T/web")" = 2 ]'
rm -f "$T/web"
FAKE_WEB_LOG=$T/web STUB_EMAIL=b@example.com STUB_TOKEN=B5 "$SCRIPT" login second > "$T/out" 2>&1
check "login NAME: Chrome came back once" '[ "$(sed -n 2p "$T/web")" = "--switch acct-a" ] && [ "$(wc -l < "$T/web")" = 2 ]'
rm -f "$T/web" "$HOME/.local/share/claude-switch/chrome-host.py"

# Two accounts with one email: the email alone does not choose.
STUB_ORG=org-of-x-0000 STUB_EMAIL=a@example.com STUB_TOKEN=AX "$SCRIPT" login > "$T/out" 2>&1
live_before=$(live_cred .claudeAiOauth.accessToken)
"$SCRIPT" a@example.com > "$T/out" 2>&1; rc=$?
check "same email twice: refused" '[ "$rc" -ne 0 ] && grep -q "Several accounts" "$T/out"'
check "same email twice: nothing changed" '[ "$(live_cred .claudeAiOauth.accessToken)" = "$live_before" ]'
"$SCRIPT" rename second a@example.com > "$T/out" 2>&1; rc=$?
check "rename onto an email two accounts share: refused" '[ "$rc" -ne 0 ] && grep -q "Another account is already called" "$T/out"'

# A switch cut between its two renames by a kill: the login of b under the name of a. The next run
# completes it, and a keeps its own login.
A_FILE=$(ls "$HOME"/.claude-accounts/a@example.com_org-of-a*.json)
A_TOKEN=$(jq -r .login.claudeAiOauth.refreshToken "$A_FILE")
jq --slurpfile b "$B_FILE" '.claudeAiOauth = $b[0].login.claudeAiOauth' "$HOME/.claude/.credentials.json" > "$T/c"
cp "$T/c" "$HOME/.claude/.credentials.json"
"$SCRIPT" list > "$T/out" 2>&1
check "cut switch: completed" '[ "$(live_conf .oauthAccount.emailAddress)" = b@example.com ] && grep -q "cut short" "$T/out"'
check "cut switch: the other account keeps its login" '[ "$(jq -r .login.claudeAiOauth.refreshToken "$A_FILE")" = "$A_TOKEN" ]'

# A login claude emptied after a refused refresh is not saved over the stored one.
jq '.claudeAiOauth.accessToken = "" | .claudeAiOauth.refreshToken = ""' "$HOME/.claude/.credentials.json" > "$T/c"
cp "$T/c" "$HOME/.claude/.credentials.json"
"$SCRIPT" list > "$T/out" 2>&1
check "emptied live login: the stored one stays" '[ -n "$(jq -r .login.claudeAiOauth.refreshToken "$B_FILE")" ]'
check "emptied live login: no account shown active" '! grep -q "^\*" "$T/out"'

# Only a new sign-in moves the date a sign-in ends. From 7 days before, list, the prompt and a switch
# remind it with the command that renews the account, the days counted on the calendar.
C_FILE=$(ls "$HOME"/.claude-accounts/c@example.com_*.json)
ends_at() {
    jq --argjson ends "$2" '.login.claudeAiOauth.refreshTokenExpiresAt = $ends' "$1" > "$T/c" && cp "$T/c" "$1"
}
noon_in() {
    printf '%s000' "$(date -d "$(date +%F) 12:00 $1 days" +%s)"
}
day_in() {
    date -d "$1 days" +%d/%m/%Y
}
ends_at "$B_FILE" "$(noon_in 2)"
ends_at "$C_FILE" "$(noon_in 10)"
"$SCRIPT" list > "$T/out" 2>&1
check "reminder: list says when, with the command" \
    'grep -qx "claude-switch: The sign-in of second ends on $(day_in 2), in 2 days. Sign in again before then: claude-switch login second" "$T/out"'
check "reminder: not 10 days ahead" '! grep -q "c@example.com ends" "$T/out"'
"$SCRIPT" c@example.com > "$T/out" 2>&1
check "reminder: after a switch" \
    'grep -q "Switched to c@example.com" "$T/out" && grep -q "The sign-in of second ends on $(day_in 2), in 2 days" "$T/out"'
printf 'q\n' | script -qfc "$SCRIPT" /dev/null > "$T/out" 2>&1
check "reminder: in the prompt" 'grep -aq "The sign-in of second ends on $(day_in 2), in 2 days" "$T/out"'
ends_at "$B_FILE" "$(noon_in 1)"
"$SCRIPT" list > "$T/out" 2>&1
check "reminder: tomorrow" 'grep -q "The sign-in of second ends on $(day_in 1), tomorrow\. " "$T/out"'
ends_at "$B_FILE" $(( $(date +%s%3N) + 60000 ))
when=today
[ "$(date -d "1 minute" +%F)" = "$(date +%F)" ] || when=tomorrow
"$SCRIPT" list > "$T/out" 2>&1
check "reminder: today" 'grep -q "The sign-in of second ends on .*, $when\. " "$T/out"'
ends_at "$B_FILE" $(( $(date +%s%3N) - 86400000 ))
"$SCRIPT" list > "$T/out" 2>&1
check "reminder: ended" \
    'grep -qx "claude-switch: The sign-in of second ended on $(day_in -1). Sign in again: claude-switch login second" "$T/out"'
jq '.dead = "01/11/2026"' "$B_FILE" > "$T/c" && cp "$T/c" "$B_FILE"
"$SCRIPT" list > "$T/out" 2>&1
check "reminder: signed out" 'grep -qx "claude-switch: second is signed out. Sign in again: claude-switch login second" "$T/out"'
jq 'del(.dead)' "$B_FILE" > "$T/c" && cp "$T/c" "$B_FILE"
# The dates and days of the clock here: midnight in Tokyo is the day before in UTC, and less than two
# whole days away.
ends_at "$B_FILE" "$(TZ=JST-9 date -d "$(TZ=JST-9 date +%F) 00:00 2 days" +%s)000"
TZ=JST-9 "$SCRIPT" list > "$T/out" 2>&1
check "reminder: local dates, calendar days" \
    'grep -q "The sign-in of second ends on $(TZ=JST-9 date -d "2 days" +%d/%m/%Y), in 2 days" "$T/out"'

printf '%d passed, %d failed\n' "$passes" "$fails"
[ "$fails" -eq 0 ]
