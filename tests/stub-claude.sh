#!/usr/bin/env bash
# Stands in for claude auth login only. Everything else goes to the real binary.
if [ "${1:-}" = auth ] && [ "${2:-}" = login ]; then
    [ -z "${STUB_ARGS_LOG:-}" ] || printf '%s\n' "$*" >> "$STUB_ARGS_LOG"
    [ "${STUB_LOGIN_RC:-0}" -eq 0 ] || exit "$STUB_LOGIN_RC"
    jq -n --arg t "$STUB_TOKEN" '{claudeAiOauth: {accessToken: ($t + "-access"), refreshToken: ($t + "-refresh"),
        expiresAt: 4102444800000, refreshTokenExpiresAt: 4102444800000, scopes: ["user:inference", "user:profile"], subscriptionType: "max"}}' \
        > "$CLAUDE_CONFIG_DIR/.credentials.json"
    jq -n --arg e "$STUB_EMAIL" --arg o "${STUB_ORG:-org-of-b-0000}" \
        '{oauthAccount: {emailAddress: $e, accountUuid: "acct", organizationUuid: $o}}' > "$CLAUDE_CONFIG_DIR/.claude.json"
    exit 0
fi
exec "$REAL_CLAUDE" "$@"
