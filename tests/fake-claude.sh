#!/usr/bin/env bash
# Fake claude for test-refresh.sh. FAKE_MODE: ok, limit, dead, offline, design. FAKE_STATE holds its log.
state=${FAKE_STATE:?}
mode=${FAKE_MODE:-ok}
printf '%s|cfg=%s|cwd=%s|noupd=%s|quiet=%s|connectors=%s|apikey=%s|claudeenv=%s|args=%s\n' "$mode" "${CLAUDE_CONFIG_DIR:-}" "$PWD" \
    "${DISABLE_AUTOUPDATER:-}" "${CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC:-}" "${ENABLE_CLAUDEAI_MCP_SERVERS:-}" \
    "${ANTHROPIC_API_KEY:-}" "$(env | grep -cE '^(CLAUDECODE|CLAUDE_CODE_SESSION_ID)=')" "$(printf '[%s]' "$@")" >> "$state/calls"
dir=${CLAUDE_CONFIG_DIR:-$HOME/.claude}
creds=$dir/.credentials.json

rotate() {
    local now n
    now=$(date +%s%3N)
    n=$(( $(cat "$state/n" 2>/dev/null || echo 0) + 1 ))
    echo "$n" > "$state/n"
    jq --arg n "$n" --argjson e $((now + 28800000)) '.claudeAiOauth.accessToken = "acc-\($n)"
        | .claudeAiOauth.refreshToken = "ref-\($n)" | .claudeAiOauth.expiresAt = $e' "$creds" > "$creds.t"
    mv "$creds.t" "$creds"
    touch "$state/rotated"
}

# What claude does with an expired access token.
refresh() {
    [ "$(jq -r --argjson now "$(date +%s%3N)" '(.claudeAiOauth.expiresAt // 0) < $now' "$creds")" = true ] || return 0
    [ "${FAKE_DELAY:-0}" = 0 ] || sleep "$FAKE_DELAY"
    if [ "${FAKE_TERM_ROTATE:-0}" = 1 ]; then
        # Still writing the rotated login when the TERM comes.
        trap 'sleep 1; rotate; exit 143' TERM
        touch "$state/started"
        sleep 30 &
        wait $!
        return 0
    fi
    case $mode in
        offline) return 0 ;;
        design)
            # Another grant of the login rotates, claudeAiOauth does not.
            n=$(( $(cat "$state/n" 2>/dev/null || echo 0) + 1 ))
            echo "$n" > "$state/n"
            jq --arg n "$n" '.designOauth.accessToken = "design-acc-\($n)" | .designOauth.refreshToken = "design-ref-\($n)"' \
                "$creds" > "$creds.t"
            mv "$creds.t" "$creds"
            ;;
        dead)
            jq '.claudeAiOauth.accessToken = "" | .claudeAiOauth.refreshToken = ""' "$creds" > "$creds.t"
            mv "$creds.t" "$creds"
            ;;
        *) rotate ;;
    esac
    [ "${FAKE_HANG:-0}" = 0 ] || sleep "$FAKE_HANG"
}

if [ "${1:-} ${2:-}" = "auth login" ]; then
    jq -n --arg t "$FAKE_TOKEN" '{claudeAiOauth: {accessToken: "\($t)-access", refreshToken: "\($t)-refresh",
        expiresAt: 4102444800000, refreshTokenExpiresAt: 4102444800000, scopes: ["user:inference"],
        subscriptionType: "max"}}' > "$creds"
    jq -n --arg e "$FAKE_EMAIL" --arg o "$FAKE_ORG" \
        '{oauthAccount: {emailAddress: $e, accountUuid: "ub", organizationUuid: $o}}' > "$dir/.claude.json"
    exit 0
fi
if [ "${1:-} ${2:-}" = "auth status" ]; then
    [ "${FAKE_STATUS_REFRESHES:-1}" = 0 ] || refresh
    printf '{"loggedIn": true}\n'
    exit 0
fi
for arg in "$@"; do
    [ "$arg" = -p ] || continue
    refresh
    case $mode in
        offline) echo "API Error: Unable to connect to API"; exit 1 ;;
        dead) echo "Failed to authenticate: OAuth session expired and could not be refreshed"; exit 1 ;;
        limit) echo "Claude AI usage limit reached"; exit 1 ;;
    esac
    echo OK
    exit 0
done
echo "fake claude: not expected: $*" >&2
exit 3
