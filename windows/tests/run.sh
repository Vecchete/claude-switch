#!/usr/bin/env bash
# Runs the suites of tests/ against the Windows port, on Linux, then the smoke tests of windows/tests. The port keeps
# the behavior of the Linux version, so the same suites hold it, apart from what only Windows does.
# Usage: windows/tests/run.sh
set -u
HERE=$(dirname "$(readlink -f "$0")")
WINDOWS=$(dirname "$HERE")
TESTS=$(dirname "$WINDOWS")/tests
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
run switch "$TESTS/test-switch.sh" "$WINDOWS/claude-switch.py"
run refresh "$TESTS/test-refresh.sh" "$WINDOWS/claude-switch.py"
run keys "$TESTS/test-keys.sh" "$WINDOWS/claude-switch.py"
run usage python3 -I "$TESTS/test-usage.py" "$WINDOWS/usage.py"
run auto "$TESTS/test-auto.sh" "$WINDOWS/claude-switch.py" "$WINDOWS/usage.py"
run switch-desktop "$TESTS/test-switch-desktop.sh" "$WINDOWS/claude-switch.py"
run switch-chrome python3 -I "$TESTS/test-switch-chrome.py" "$WINDOWS/claude-switch.py" "$WINDOWS/chrome-host.py"
run chrome-host python3 -I "$TESTS/test-chrome-host.py" "$WINDOWS/chrome-host.py"
run desktop python3 -I "$TESTS/test-desktop.py" "$WINDOWS/desktop.py"
run smoke python3 -I "$HERE/test-switch.py"
run smoke-keys python3 -I "$HERE/test-keys.py"
run smoke-auto python3 -I "$HERE/test-auto.py"
if command -v pwsh >/dev/null; then
    run powershell pwsh -NoProfile -NonInteractive -File "$HERE/test-powershell.ps1"
else
    printf '== powershell\nskipped: no pwsh in the PATH\n'
fi
printf '\nPassed: %s\n' "${PASSED[*]:-none}"
[ "${#FAILED[@]}" -eq 0 ] || { printf 'Failed: %s\n' "${FAILED[*]}"; exit 1; }
