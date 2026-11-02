#!/usr/bin/env python3
"""Smoke tests of the Windows port of claude-switch: switch, login, rename, the reminder, the refresh and the
helpers, with a fake claude, a fake notifier and fake helpers, on a scratch profile. They run on Windows, where they
are the first thing to try, and on Linux. Usage: test-switch.py [SCRIPT], windows/claude-switch.py by default."""
import datetime
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

WINDOWS = os.name == "nt"
HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else HERE.parent / "claude-switch.py").resolve()
FAR = 4102444800000
failures = []
passes = 0


def check(name, condition):
    global passes
    if condition:
        passes += 1
    else:
        failures.append(name)
        print("FAIL", name)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def executable(path, text):
    """A script the port runs: a .cmd on Windows, a file with a #! line elsewhere. Text mode gives the line ends of
    the system."""
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


root = pathlib.Path(tempfile.mkdtemp(prefix="claude-switch-windows."))
try:
    home, bin_, state = root / "home", root / "bin", root / "state"
    share = home / ".local" / "share" / "claude-switch"
    store = home / ".claude-accounts"
    for folder in (home / ".claude", bin_, state, share):
        folder.mkdir(parents=True)
    fake = HERE / "fake-claude.py"
    if WINDOWS:
        executable(bin_ / "claude.cmd", f'@"{sys.executable}" "{fake}" %*\n')
        executable(share / "notify.ps1", 'param($Title, $Body)\n'
                                         'Add-Content -LiteralPath "$env:FAKE_STATE\\notify" -Value "$Title $Body"\n'
                                         'exit [int]$env:FAKE_NOTIFY_RC\n')
    else:
        executable(bin_ / "claude", f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n')
        executable(bin_ / "notify-send", '#!/bin/sh\nprintf "%s\\n" "$*" >> "$FAKE_STATE/notify"\nexit "${FAKE_NOTIFY_RC:-0}"\n')
    # The helpers log what claude-switch asks of them. FAKE_DESKTOP_STATUS is what desktop.py answers.
    helper = ("import os, pathlib, sys\n"
              "with open(pathlib.Path(os.environ['FAKE_STATE']) / '{log}', 'a') as log:\n"
              "    log.write(' '.join(sys.argv[1:]) + '\\n')\n")
    executable(share / "chrome-host.py", f"#!{sys.executable}\n" + helper.format(log="web"))
    executable(share / "desktop.py", f"#!{sys.executable}\n" + helper.format(log="desktop")
               + "sys.exit(int(os.environ.get('FAKE_DESKTOP_STATUS', '0')))\n")

    credentials, config = home / ".claude" / ".credentials.json", home / ".claude.json"
    write(credentials, {"claudeAiOauth": {"accessToken": "A-access", "refreshToken": "A-refresh", "expiresAt": FAR,
                                          "refreshTokenExpiresAt": FAR, "subscriptionType": "max"},
                        "mcpOAuth": {"gitlab|1": {"accessToken": "mcp-token"}}})
    write(config, {"oauthAccount": {"emailAddress": "a@example.com", "accountUuid": "acct-a",
                                    "organizationUuid": "org-a-0000"},
                   "theme": "light", "projects": {"/p": {"t": 1}}, "modelAccessCache": ["old"]})

    base = {key: value for key, value in os.environ.items() if not key.upper().startswith(("CLAUDE", "ANTHROPIC"))}
    base.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(root / "roaming"), LOCALAPPDATA=str(root / "local"),
                PATH=f"{bin_}{os.pathsep}{os.environ['PATH']}", FAKE_STATE=str(state))

    def run(*arguments, **extra):
        return subprocess.run([sys.executable, "-I", str(SCRIPT), *arguments], env=dict(base, **extra),
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", timeout=180)

    def log(name):
        path = state / name
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def account(email):
        return next(path for path in store.glob("*.json") if read(path)["oauthAccount"]["emailAddress"] == email)

    def leftovers():
        return [path.name for path in store.iterdir() if path.name.startswith((".signin.", ".refresh."))]

    def noon_in(days):
        noon = datetime.datetime.combine(datetime.date.today() + datetime.timedelta(days=days), datetime.time(12))
        return int(noon.timestamp() * 1000)

    result = run("list")
    check("list marks the live account", "* a@example.com" in result.stdout)
    check("the store holds no MCP login", "mcp-token" not in account("a@example.com").read_text(encoding="utf-8"))
    check("help lists itself", "  claude-switch help " in run("help").stdout)

    result = run("login", FAKE_EMAIL="b@example.com", FAKE_TOKEN="B", FAKE_ACCOUNT="acct-b", FAKE_ORG="org-b-0000")
    check("login adds the account", result.returncode == 0 and len(list(store.glob("*.json"))) == 2)
    check("login doesn't switch", read(credentials)["claudeAiOauth"]["accessToken"] == "A-access")
    check("login leaves no folder", leftovers() == [])

    run("rename", "b@example.com", "second")
    check("rename", "second" in run("list").stdout)

    result = run("second")
    live, conf = read(credentials), read(config)
    check("switch says it", "Switched to second." in result.stdout)
    check("switch: the login is the new one", live["claudeAiOauth"]["accessToken"] == "B-access")
    check("switch: the account is the new one", conf["oauthAccount"]["emailAddress"] == "b@example.com")
    check("switch: MCP logins stay", live["mcpOAuth"]["gitlab|1"]["accessToken"] == "mcp-token")
    check("switch: the settings stay", conf["theme"] == "light" and conf["projects"] == {"/p": {"t": 1}})
    check("switch: the account caches go", "modelAccessCache" not in conf)
    check("switch: nothing left aside", not list((home / ".claude").glob("*.switch")) and not list(home.glob("*.switch")))
    check("switch: no lock left", not (home / ".claude.lock").exists() and not (home / ".claude.json.lock").exists()
          and not (home / ".claude" / ".oauth_refresh.lock").exists())
    check("switch: Chrome follows", log("web")[-1:] == ["--switch acct-b"])
    check("switch: Desktop follows", log("desktop")[-1:] == ["switch acct-b"])

    live["claudeAiOauth"]["refreshToken"] = "B-rotated"
    write(credentials, live)
    run("a@example.com")
    run("second")
    check("a rotated token survives the way out and back", read(credentials)["claudeAiOauth"]["refreshToken"] == "B-rotated")

    result = run("nobody")
    check("an unknown name is refused", result.returncode == 1 and "No account called nobody" in result.stderr)

    # Ctrl+C right after the first of the two renames of a switch: it ends on one account, never half on each.
    stopper = root / "stop-between.py"
    stopper.write_text(
        "import importlib.util, os, signal, sys\n"
        "spec = importlib.util.spec_from_file_location('claude_switch', sys.argv[1])\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "real = os.replace\n"
        "def replace(source, target):\n"
        "    real(source, target)\n"
        "    if str(target).endswith('.credentials.json'):\n"
        "        signal.raise_signal(signal.SIGINT)\n"
        "os.replace = replace\n"
        "for number in module.STOPS:\n"
        "    signal.signal(number, module.stop)\n"
        "try:\n"
        "    sys.exit(module.main(sys.argv[2:]))\n"
        "finally:\n"
        "    module.cleanup()\n", encoding="utf-8")
    stopped = subprocess.run([sys.executable, "-I", str(stopper), str(SCRIPT), "a@example.com"], env=base,
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180)
    token, email = read(credentials)["claudeAiOauth"]["accessToken"], read(config)["oauthAccount"]["emailAddress"]
    check("a stop between the renames: one account on both files",
          stopped.returncode == 143 and (token, email) in (("A-access", "a@example.com"), ("B-access", "b@example.com")))
    run("second")

    # A switch cut between its two renames: the login of a under the account of b.
    live = read(credentials)
    live["claudeAiOauth"] = read(account("a@example.com"))["login"]["claudeAiOauth"]
    write(credentials, live)
    result = run("list")
    check("a cut switch is completed", "cut short" in result.stderr and read(config)["oauthAccount"]["emailAddress"] == "a@example.com")

    second = account("b@example.com")
    data = read(second)
    data["login"]["claudeAiOauth"]["refreshTokenExpiresAt"] = noon_in(2)
    write(second, data)
    day = (datetime.date.today() + datetime.timedelta(days=2)).strftime("%d/%m/%Y")
    result = run("list")
    check("the reminder says when, with the command", f"claude-switch: The sign-in of second ends on {day}, in 2 days. "
          "Sign in again before then: claude-switch login second" in result.stderr.splitlines())

    # b is not live: its due login is refreshed by the fake, in a folder of its own and with a clean environment.
    data["login"]["claudeAiOauth"].update(expiresAt=int(time.time() * 1000) + 3600000, refreshTokenExpiresAt=FAR)
    write(second, data)
    result = run("refresh", ANTHROPIC_API_KEY="leak", CLAUDECODE="1")
    call = json.loads(log("calls")[-1])
    check("refresh: exit 0", result.returncode == 0)
    check("refresh: rotated", read(second)["login"]["claudeAiOauth"]["accessToken"] == "acc-1")
    check("refresh: auth status, no model", call["args"] == ["auth", "status"])
    check("refresh: in a folder of the store", pathlib.Path(call["config"]).parent == store
          and pathlib.Path(call["config"]).name.startswith(".refresh."))
    check("refresh: nothing of the calling session", call["apiKey"] == "" and not call["session"] and call["noUpdate"] == "1")
    check("refresh: no folder left", leftovers() == [])

    data = read(second)
    data["login"]["claudeAiOauth"]["expiresAt"] = int(time.time() * 1000) + 3600000
    write(second, data)
    result = run("refresh", FAKE_MODE="dead")
    check("refused: exit 1", result.returncode == 1)
    check("refused: marked", "dead" in read(second))
    check("refused: announced with the command", any("Sign in again: claude-switch login second" in line for line in log("notify")))
    run("refresh", FAKE_MODE="dead")
    check("refused: announced once a day", len(log("notify")) == 1)
    result = run("second")
    check("refused: the switch is refused", result.returncode == 1 and "was signed out" in result.stderr)

    result = run("a@example.com", FAKE_DESKTOP_STATUS="3")
    check("open Desktop, no terminal: left alone, said how to bring it along",
          "Close it and run claude-switch a@example.com to bring it along." in result.stdout)
finally:
    shutil.rmtree(root, ignore_errors=True)

print(f"{passes} passed, {len(failures)} failed")
sys.exit(1 if failures else 0)
