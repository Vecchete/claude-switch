#!/usr/bin/env bash
# Runs every suite against the files of this checkout, never against the installed ones.
# Usage: tests/run.sh [SUITE...], where SUITE is switch, refresh, keys, chrome-host, switch-chrome, worker,
# desktop, switch-desktop, completion or install.
set -u
TESTS=$(dirname "$(readlink -f "$0")")
ROOT=$(dirname "$TESTS")
export PYTHONDONTWRITEBYTECODE=1

run() {
    local name=$1
    shift
    printf '== %s\n' "$name"
    if "$@"; then
        PASSED+=("$name")
    else
        FAILED+=("$name")
    fi
}

PASSED=()
FAILED=()
[ $# -gt 0 ] || set -- switch refresh keys chrome-host switch-chrome worker desktop switch-desktop completion install
for suite in "$@"; do
    case $suite in
        switch) run switch "$TESTS/test-switch.sh" "$ROOT/bin/claude-switch" ;;
        refresh) run refresh "$TESTS/test-refresh.sh" "$ROOT/bin/claude-switch" ;;
        keys) run keys "$TESTS/test-keys.sh" "$ROOT/bin/claude-switch" ;;
        chrome-host) run chrome-host python3 -I "$TESTS/test-chrome-host.py" "$ROOT/share/chrome-host.py" ;;
        switch-chrome) run switch-chrome python3 -I "$TESTS/test-switch-chrome.py" "$ROOT/bin/claude-switch" \
            "$ROOT/share/chrome-host.py" ;;
        worker) run worker node "$TESTS/test-worker.mjs" "$ROOT/share/chrome-extension/worker.js" ;;
        desktop) run desktop python3 -I "$TESTS/test-desktop.py" "$ROOT/share/desktop.py" ;;
        switch-desktop) run switch-desktop "$TESTS/test-switch-desktop.sh" "$ROOT/bin/claude-switch" ;;
        completion) run completion "$TESTS/test-completion.sh" "$ROOT/share/completion.bash" ;;
        install) run install "$TESTS/test-install.sh" "$ROOT/install.sh" ;;
        *) printf 'No suite called %s.\n' "$suite" >&2; exit 2 ;;
    esac
done
printf '\nPassed: %s\n' "${PASSED[*]:-none}"
[ "${#FAILED[@]}" -eq 0 ] || { printf 'Failed: %s\n' "${FAILED[*]}"; exit 1; }
