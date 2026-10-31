#!/usr/bin/env python3
"""Breaks one guard at a time and runs the suite that must catch it. A mutant that stays green is a guard
no test holds. Every mutant undoes a behaviour that a review or a live incident asked for.

Usage: tests/mutants.py [-j JOBS] [NAME_OR_TARGET...]   (targets: switch, host, install)
"""
import argparse
import concurrent.futures
import os
import pathlib
import subprocess
import sys
import tempfile

TESTS = pathlib.Path(__file__).resolve().parent
ROOT = TESTS.parent
TARGETS = {
    "switch": ROOT / "bin/claude-switch",
    "host": ROOT / "share/chrome-host.py",
    "install": ROOT / "install.sh",
}

# (name, target, suite, text of the target, what replaces it)
MUTANTS = [
    # The switch itself.
    ("apply-not-atomic", "switch", "switch",
     '    jq --slurpfile account "$1" "$DROP_CACHES | .oauthAccount = \\$account[0].oauthAccount" "$CONFIG" > "$CONFIG.switch"\n'
     "    # A stop between the renames would leave the login of one account under the name of the other, so\n"
     "    # it waits until both are done.\n"
     "    trap 'stopped=1' TERM INT HUP\n"
     '    mv "$CREDENTIALS.switch" "$CREDENTIALS"\n',
     "    # A stop between the renames would leave the login of one account under the name of the other, so\n"
     "    # it waits until both are done.\n"
     "    trap 'stopped=1' TERM INT HUP\n"
     '    mv "$CREDENTIALS.switch" "$CREDENTIALS"\n'
     '    jq --slurpfile account "$1" "$DROP_CACHES | .oauthAccount = \\$account[0].oauthAccount" "$CONFIG" > "$CONFIG.switch"\n'),
    ("first-match-wins", "switch", "switch", '    [ "${#found[@]}" -eq 1 ] || return 2\n', ""),
    ("no-key-canary", "switch", "switch", "    warn_environment\n    check_login_keys\n", "    warn_environment\n"),
    ("login-no-email", "switch", "switch", '        set -- --email "$wanted_email" "$@"\n', ""),
    ("login-no-warning", "switch", "switch",
     '        warn "The browser signed in $EMAIL, not $wanted_email. $EMAIL is kept, $wanted_email is unchanged."\n',
     "        :\n"),
    ("login-no-way-back", "switch", "switch",
     '    rm -f "$CREDENTIALS.switch" "$CONFIG.switch"\n    [ -z "$WEB_AWAY" ] || web_back\n',
     '    rm -f "$CREDENTIALS.switch" "$CONFIG.switch"\n'),
    ("empty-login-is-live", "switch", "switch",
     '    jq -e "$SIGNED_IN" "$CREDENTIALS" >/dev/null 2>&1 || return 1',
     "    jq -e '.claudeAiOauth.accessToken' \"$CREDENTIALS\" >/dev/null 2>&1 || return 1"),
    ("ambiguous-name-allowed", "switch", "switch",
     '    if [ "$status" -eq 2 ] || { [ "$status" -eq 0 ] && [ "$other" != "${2:-}" ]; }; then',
     '    if [ "$status" -eq 0 ] && [ "$other" != "${2:-}" ]; then'),
    ("stop-not-deferred", "switch", "switch", "    trap 'stopped=1' TERM INT HUP\n", ""),
    ("no-repair", "switch", "switch", "recover_refreshes\nrepair_live\nsave_live\n", "recover_refreshes\nsave_live\n"),
    ("device-keys-stay", "switch", "switch",
     "LOGIN_KEYS='[\"claudeAiOauth\", \"designOauth\", \"cloudSessionsOauth\", \"organizationUuid\", \"trustedDeviceToken\",\n"
     "    \"trustedDeviceEnrolledAfterRefusalAtMs\", \"enterpriseGateway\"]'",
     "LOGIN_KEYS='[\"claudeAiOauth\", \"designOauth\", \"cloudSessionsOauth\"]'"),
    ("old-name-in-hints", "switch", "switch", "Add it with: claude-switch login", "Add it with: claude-switch.sh login"),
    ("web-by-organization", "switch", "switch-chrome",
     ".oauthAccount.accountUuid // empty", ".oauthAccount.organizationUuid // empty"),
    # The refresh of the accounts that are not live.
    ("env-claude-kept", "switch", "refresh", "            CLAUDE*|ANTHROPIC*) CLEAN_ENV+=", "            ANTHROPIC*) CLEAN_ENV+="),
    ("signins-not-tidied", "switch", "refresh", "    tidy_signins\n    unlock_store\n", "    unlock_store\n"),
    ("older-signin-imported", "switch", "refresh", '            if [ "$ends" -gt "$stored" ]; then', "            if true; then"),
    ("young-signin-taken", "switch", "refresh",
     '        [ -n "$(find "$dir" -maxdepth 0 -mmin +60 2>/dev/null)" ] || continue\n', ""),
    ("notify-recorded-first", "switch", "refresh",
     '    notify-send --app-name="Claude accounts" "$2" "$3" 9>&- 2>/dev/null || return 0\n',
     '    notify-send --app-name="Claude accounts" "$2" "$3" 9>&- 2>/dev/null || true\n'),
    ("recovery-only-in-refresh", "switch", "refresh",
     "lock_store\nrecover_refreshes\nrepair_live\n", "lock_store\nrepair_live\n"),
    ("fingerprint-claude-only", "switch", "refresh",
     '    jq -c "[$2.claudeAiOauth, $2.designOauth, $2.cloudSessionsOauth] | map(.accessToken, .refreshToken)" "$1" \\\n',
     '    jq -c "[$2.claudeAiOauth] | map(.accessToken, .refreshToken)" "$1" \\\n'),
    # The Chrome host.
    ("no-saved-match", "host", "chrome-host",
     '    for account, saved in saved_sessions():\n        if value(saved, "sessionKey") == key:',
     '    for account, saved in []:\n        if value(saved, "sessionKey") == key:'),
    ("any-json-refusal-signed-out", "host", "chrome-host",
     '        return json.loads(error.read())["error"]["details"]["error_code"] == "account_session_invalid"',
     "        return True"),
    ("no-migration", "host", "chrome-host", "    migrate()\n", ""),
    ("expired-restored", "host", "chrome-host",
     '    if not key.get("session") and key.get("expirationDate", 0) < time.time():', "    if False:"),
    ("socket-always-removed", "host", "chrome-host",
     "        if own_socket is not None and SOCKET.stat().st_ino == own_socket:", "        if True:"),
    ("pending-tried-once", "host", "chrome-host", "        time.sleep(PENDING_PAUSE)\n", "        return\n"),
    ("pending-outlives-switch", "host", "chrome-host",
     '                        PENDING.unlink(missing_ok=True)\n                        reply = {"result": switch(',
     '                        reply = {"result": switch('),
    ("change-during-switch-ignored", "host", "chrome-host",
     '        if value(cookies, "sessionKey") != value(before, "sessionKey"):', "        if False:"),
    ("deaf-host-stays", "host", "chrome-host",
     "    finally:\n        release_socket()\n        os._exit(0)\n",
     "    except EOFError:\n        pass\n    release_socket()\n    os._exit(0)\n"),
    ("no-rebind", "host", "chrome-host",
     "            if not SOCKET.exists():\n                server.close()\n                server = bind()\n", "            pass\n"),
    ("stale-socket-kept", "host", "chrome-host",
     "            if isinstance(error, ConnectionRefusedError):\n                SOCKET.unlink(missing_ok=True)\n", ""),
    ("block-left", "host", "chrome-host",
     '    with switching:\n        try:\n            ask("release")\n        except (OSError, RuntimeError, queue.Empty):\n'
     "            pass\n", ""),
    # The installer.
    ("old-name-kept", "install", "install", '    rm -f "$OLD"\n', "    :\n"),
    ("wrong-extension-id", "install", "install", 'chr(ord("a") + int(c, 16))', 'chr(ord("b") + int(c, 16))'),
    ("mode-ignored", "install", "install", ' && [ "$(stat -c %a "$target")" = "$mode" ]', ""),
    ("check-installs", "install", "install",
     "    if [ -n \"$CHECK\" ]; then\n        printf 'differs: %s\\n' \"$target\"",
     "    if false; then\n        printf 'differs: %s\\n' \"$target\""),
    ("units-not-reloaded", "install", "install", "    systemctl --user daemon-reload\n", "    :\n"),
    ("every-browser", "install", "install", '    [ -d "$HOME/.config/$browser" ] || continue\n', ""),
    ("check-ignores-old-name", "install", "install",
     "        printf 'left over: %s\\n' \"$OLD\"\n        DIFFERS=1\n", "        :\n"),
]


def suite_command(suite, target, path):
    files = dict(TARGETS)
    files[target] = path
    commands = {
        "switch": ["bash", TESTS / "test-switch.sh", files["switch"]],
        "refresh": ["bash", TESTS / "test-refresh.sh", files["switch"]],
        "chrome-host": [sys.executable, "-I", TESTS / "test-chrome-host.py", files["host"]],
        "switch-chrome": [sys.executable, "-I", TESTS / "test-switch-chrome.py", files["switch"], files["host"]],
        "install": ["bash", TESTS / "test-install.sh", files["install"]],
    }
    return [str(part) for part in commands[suite]]


def run(mutant, folder):
    name, target, suite, old, new = mutant
    source = TARGETS[target].read_text()
    if source.count(old) != 1:
        return name, suite, f"stale: the text is found {source.count(old)} times in {TARGETS[target].name}"
    path = pathlib.Path(folder) / f"{name}{TARGETS[target].suffix}"
    if target == "install":
        # The installer finds what it installs next to itself.
        path = pathlib.Path(folder) / name / "install.sh"
        path.parent.mkdir()
        for part in ("bin", "share", "systemd"):
            (path.parent / part).symlink_to(ROOT / part)
    path.write_text(source.replace(old, new))
    path.chmod(0o755)
    result = subprocess.run(suite_command(suite, target, path), capture_output=True, timeout=1200,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    return name, suite, "SURVIVED" if result.returncode == 0 else "killed"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-j", "--jobs", type=int, default=1)
    parser.add_argument("only", nargs="*")
    wanted = parser.parse_args()
    chosen = [m for m in MUTANTS if not wanted.only or m[0] in wanted.only or m[1] in wanted.only]
    chosen = [m for m in chosen if TARGETS[m[1]].exists()]
    bad = []
    with tempfile.TemporaryDirectory(prefix="claude-switch-mutants.") as folder:
        with concurrent.futures.ThreadPoolExecutor(wanted.jobs) as pool:
            for name, suite, outcome in pool.map(lambda m: run(m, folder), chosen):
                print(f"{outcome:8} {name} ({suite})", flush=True)
                if outcome != "killed":
                    bad.append(name)
    print(f"{len(chosen) - len(bad)} of {len(chosen)} killed" + (f", not killed: {', '.join(bad)}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
