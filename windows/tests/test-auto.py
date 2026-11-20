#!/usr/bin/env python3
"""Smoke tests of automatic switching in the Windows port: the checks of tests/test-auto.sh, on a scratch profile,
against a stand-in usage endpoint and gateway on this machine, with a stand-in claude, Desktop helper, notifier and
task scheduler. What list and the prompt show, auto on and off, auto order, the watch and the hook Claude Code runs.
Then what only Windows does: the hook runs Python with no shell between, the watch is a scheduled task, a session is
told by the creation time of its process, what a helper says in a hook stays out of what Claude reads, and a profile
whose path isn't ASCII works the same. They run on Windows, where tests/test-auto.sh can't, and on Linux.
Usage: test-auto.py [SCRIPT [USAGE_PY]], windows/claude-switch.py and windows/usage.py by default."""
import csv
import datetime
import http.server
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time

WINDOWS = os.name == "nt"
HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else HERE.parent / "claude-switch.py").resolve()
USAGE_PY = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else HERE.parent / "usage.py").resolve()
# The scratch profile's path isn't ASCII: what this says of it goes out in UTF-8, not in the code page of Windows.
for stream in (sys.stdout, sys.stderr):
    stream.reconfigure(encoding="utf-8", errors="replace")
DAY = 86400000
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
    return json.loads(path.read_text(encoding="utf-8-sig"))


def executable(path, text):
    """A script the port runs: a .cmd on Windows, a file with a #! line elsewhere."""
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def now():
    return int(time.time() * 1000)


def iso(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def started_at_here():
    """When this process started, as Claude Code writes it in procStart: the start time /proc gives it on Linux, the
    creation time of the process on Windows. Read here apart from usage.py, which is what is tested."""
    if not WINDOWS:
        return pathlib.Path(f"/proc/{os.getpid()}/stat").read_text().rpartition(")")[2].split()[19]
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    times = [wintypes.FILETIME() for _ in range(4)]
    kernel.GetProcessTimes(kernel.GetCurrentProcess(), *(ctypes.byref(entry) for entry in times))
    return str(times[0].dwHighDateTime << 32 | times[0].dwLowDateTime)


def user_sid():
    line = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True).stdout
    return next(csv.reader([line.strip()]))[1]


class Server(http.server.BaseHTTPRequestHandler):
    """The usage endpoint and the gateways: what ANSWERS holds for a path and a token, 404 otherwise. Logs each request."""
    answers = {}
    requests = []

    def do_GET(self):
        token = self.headers.get("Authorization", "")[len("Bearer "):]
        Server.requests.append((self.path, token))
        status, body = Server.answers.get((self.path, token), (404, {"error": "not here"}))
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *arguments):
        pass


def week(percent, resets, window=None):
    """A usage answer: the week at PERCENT until RESETS, and a 5-hour window at WINDOW percent when given."""
    limits = [{"kind": "weekly_all", "percent": percent, "resets_at": iso(resets)}]
    if window is not None:
        limits.append({"kind": "session", "percent": window, "resets_at": iso(resets)})
    return {"seven_day": {"utilization": percent, "resets_at": iso(resets)}, "limits": limits}


def budget(spend, resets):
    return {"info": {"spend": spend, "max_budget": 25, "budget_reset_at": iso(resets)}}


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Server)
threading.Thread(target=server.serve_forever, daemon=True).start()
PORT = server.server_address[1]
root = pathlib.Path(tempfile.mkdtemp(prefix="claude-switch-auto."))
denied = []
try:
    # The profile's path holds a letter that isn't ASCII, as a Windows user name may: what usage.py says of it comes
    # back to claude-switch whole.
    home, bin_, state = root / "home-ç", root / "bin", root / "state"
    share = home / ".local" / "share" / "claude-switch"
    store = home / ".claude-accounts"
    claude_dir = home / ".claude"
    settings = claude_dir / "settings.json"
    credentials = claude_dir / ".credentials.json"
    config = home / ".claude.json"
    sessions = claude_dir / "sessions"
    for folder in (sessions, bin_, state, share, store):
        folder.mkdir(parents=True, exist_ok=True)
    fake = HERE / "fake-claude.py"
    if WINDOWS:
        executable(bin_ / "claude.cmd", f'@"{sys.executable}" "{fake}" %*\n')
        executable(bin_ / "schtasks.cmd", '@echo %*>>"%FAKE_STATE%\\watch"\n@exit /b 0\n')
        executable(share / "notify.ps1", 'param($Title, $Body)\n'
                                         'Add-Content -LiteralPath "$env:FAKE_STATE\\notify" -Value "$Title $Body" '
                                         '-Encoding UTF8\n')
    else:
        executable(bin_ / "claude", f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n')
        executable(bin_ / "systemctl", '#!/bin/sh\nprintf "%s\\n" "$*" >> "$FAKE_STATE/watch"\n')
        executable(bin_ / "notify-send", '#!/bin/sh\nprintf "%s\\n" "$*" >> "$FAKE_STATE/notify"\n')
    # The Desktop helper logs what claude-switch asks of it, says something, and answers with the statuses
    # FAKE_DESKTOP_SWITCH gives each switch in turn, "3,0" for instance. FAKE_DESKTOP_INSIDE=0: Desktop runs the caller.
    executable(share / "desktop.py", f"#!{sys.executable}\n"
               "import os, pathlib, sys\n"
               "log = pathlib.Path(os.environ['FAKE_STATE']) / 'desktop'\n"
               "with open(log, 'a', encoding='utf-8') as handle:\n"
               "    handle.write(' '.join(sys.argv[1:]) + '\\n')\n"
               "print('desktop helper ran:', ' '.join(sys.argv[1:]), flush=True)\n"
               "if sys.argv[1] == 'switch':\n"
               "    count = sum(line.startswith('switch') for line in log.read_text(encoding='utf-8').splitlines())\n"
               "    statuses = os.environ.get('FAKE_DESKTOP_SWITCH', '0').split(',')\n"
               "    status = statuses[0] if len(statuses) == 1 else statuses[count - 1] if count <= len(statuses) else '0'\n"
               "    sys.exit(int(status or 0))\n"
               "if sys.argv[1] == 'inside':\n"
               "    sys.exit(int(os.environ.get('FAKE_DESKTOP_INSIDE', '1')))\n"
               "sys.exit(int(os.environ.get('FAKE_DESKTOP_QUIT', '0')) if sys.argv[1] == 'quit' else 0)\n")
    shutil.copy2(USAGE_PY, share / "usage.py")
    (share / "usage.py").chmod(0o755)

    env = {key: value for key, value in os.environ.items() if not key.upper().startswith(("CLAUDE", "ANTHROPIC"))}
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        env.pop(key, None)
    env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(root / "roaming"), LOCALAPPDATA=str(root / "local"),
               PATH=f"{bin_}{os.pathsep}{os.environ['PATH']}", FAKE_STATE=str(state), TZ="UTC", no_proxy="127.0.0.1",
               NO_PROXY="127.0.0.1", CLAUDE_SWITCH_ANTHROPIC_URL=f"http://127.0.0.1:{PORT}")

    def run(*arguments, stdin="", **extra):
        return subprocess.run([sys.executable, "-I", str(SCRIPT), *arguments], env=dict(env, **extra), cwd=home,
                              input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=300)

    def out(result):
        return result.stdout + result.stderr

    def log(name):
        path = state / name
        return path.read_text(encoding="utf-8-sig", errors="replace") if path.exists() else ""

    def calls():
        return "".join(f"{line}|" for line in log("desktop").splitlines())

    def live_access():
        return read(credentials)["claudeAiOauth"]["accessToken"]

    def env_token():
        found = read(settings).get("env", {})
        return (found.get("ANTHROPIC_AUTH_TOKEN") or "") + (found.get("ANTHROPIC_API_KEY") or "")

    def rows(result):
        return result.stdout.splitlines()

    def marked(name):
        return any(line.startswith(f"* {name} ") for line in rows(run("list")))

    def usage_file(stem, refused=False):
        return store / ".usage" / f"{stem}{'.refused' if refused else ''}.json"

    def change_reading(stem, **fields):
        data = read(usage_file(stem))
        data.update(fields)
        write(usage_file(stem), data)

    def week_passed(stem):
        """The reading of STEM says its week reset a moment ago, as it does once its reset time passed."""
        data = read(usage_file(stem))
        data["weekly"]["resets"] = now() - 1000
        data["at"] = now() - 2000
        write(usage_file(stem), data)

    def stale(stem):
        """The reading of STEM as old as the last run of the task, so the next watch reads it again."""
        change_reading(stem, at=0)

    def old_switch():
        """The last switch, a minute ago: a hook's session failed after it."""
        write(store / ".switched", f"{now() - 60000}\n")

    def refused(stem):
        return usage_file(stem, refused=True).exists()

    def open_session(started=None):
        write(sessions / f"{os.getpid()}.json", {"pid": os.getpid(), "procStart": started or started_at_here(),
                                                 "kind": "interactive", "status": "busy"})

    def hook(payload, **extra):
        return run("hook", stdin=payload if isinstance(payload, str) else json.dumps(payload), **extra)

    def own_hooks():
        """The StopFailure entries of claude-switch in settings.json, whatever path they run it from."""
        found = []
        for entry in read(settings).get("hooks", {}).get("StopFailure", []):
            handler = entry["hooks"][0]
            arguments = handler.get("args") or []
            if (handler["command"].endswith(" hook") and "claude-switch" in handler["command"]) or \
                    (arguments[-1:] == ["hook"] and any("claude-switch" in part for part in arguments)):
                found.append(entry)
        return found

    def requests_since(count, token):
        return any(request_token == token for _, request_token in Server.requests[count:])

    # A machine on a@example.com (Max, with a 5-hour window), with an Ultra account, a gateway key with a budget and an
    # Anthropic API key kept beside it.
    write(credentials, {"claudeAiOauth": {"accessToken": "A-token", "refreshToken": "A-refresh", "expiresAt": FAR,
                                          "refreshTokenExpiresAt": FAR, "rateLimitTier": "default_claude_max_5x"}})
    write(config, {"oauthAccount": {"emailAddress": "a@example.com", "accountUuid": "acct-a",
                                    "organizationUuid": "org-a-0000", "organizationType": "claude_max",
                                    "organizationRateLimitTier": "default_claude_max_5x"},
                   "hasCompletedOnboarding": True})
    write(store / "u@example.com_org-u-00.json",
          {"name": "ultra", "login": {"claudeAiOauth": {"accessToken": "U-token", "refreshToken": "U-refresh",
                                                        "expiresAt": FAR, "refreshTokenExpiresAt": FAR,
                                                        "rateLimitTier": "default_claude_max_20x"}},
           "oauthAccount": {"emailAddress": "u@example.com", "accountUuid": "acct-u", "organizationUuid": "org-u-0000",
                            "organizationType": "claude_max", "organizationRateLimitTier": "default_claude_max_20x"}})
    write(store / "key_gw.json", {"kind": "key", "name": "gw", "env": {"ANTHROPIC_AUTH_TOKEN": "G-secret",
                                                                     "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{PORT}/gw"}})
    write(store / "key_api.json", {"kind": "key", "name": "api", "env": {"ANTHROPIC_API_KEY": "K-secret"}})
    # Beside other hooks, the hooks of a claude-switch installed elsewhere before, one for a shell and one run with no
    # shell, as Windows writes it.
    write(settings, {"env": {"ENABLE_TOOL_SEARCH": "true"},
                     "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other-stop"}]}],
                               "StopFailure": [
                                   {"matcher": "server_error", "hooks": [{"type": "command", "command": "other-failure"}]},
                                   {"matcher": "rate_limit", "hooks": [{"type": "command",
                                                                        "command": "/old/place/claude-switch hook"}]},
                                   {"matcher": "rate_limit", "hooks": [{"type": "command", "command": "C:\\old\\pyw.exe",
                                                                        "args": ["-3", "-I", "C:\\old\\claude-switch.py",
                                                                                 "hook"]}]}]}})
    if not WINDOWS:
        for path in (credentials, config, settings, *store.glob("*.json")):
            path.chmod(0o600)
        store.chmod(0o700)
    A, U = "a@example.com_org-a-00", "u@example.com_org-u-00"
    Server.answers = {
        ("/api/oauth/usage", "A-token"): (200, week(60, now() + 3 * DAY, 10)),
        ("/api/oauth/usage", "U-token"): (200, week(30, now() + 2 * DAY)),
        ("/key/info", "G-secret"): (200, budget(3.2, now() + 10 * DAY)),
    }

    # What each account has left, in list.
    result = run("list")
    lines = rows(result)
    check("list: the LEFT column", bool(lines) and re.search(r"UNTIL +LEFT$", lines[0]) is not None)
    active_line = next((line for line in lines if line.startswith("* a@example.com ")), "")
    check("list: the week of the active account, under a path that isn't ASCII",
          re.search(r" 40% until [A-Z][a-z]{2} [0-9]{2}:[0-9]{2}$", active_line) is not None)
    check("list: the week of another account", any(line.startswith("  ultra ") and " 70% until " in line for line in lines))
    check("list: a gateway budget", any(line.startswith("  gw ") and "$21.80 until " in line for line in lines))
    check("list: nothing to read for an API key", any(line.startswith("  api ") and line.endswith(" -") for line in lines))
    check("list prints no token", not re.search(r"secret|-token", out(result)))
    if not WINDOWS:
        check("list: the readings are private", stat.S_IMODE((store / ".usage").stat().st_mode) == 0o700
              and stat.S_IMODE(usage_file(U).stat().st_mode) == 0o600)
    shutil.rmtree(store / ".usage")
    result = run("list", CLAUDE_SWITCH_ANTHROPIC_URL="http://127.0.0.1:9")
    check("list offline: still a list", result.returncode == 0
          and any(line.startswith("  ultra ") and line.endswith(" -") for line in rows(result)))
    shutil.rmtree(store / ".usage")
    prompt = root / "prompt.py"
    prompt.write_text(
        "import builtins, importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('claude_switch', sys.argv[1])\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "builtins.input = lambda question='': 'q'\n"
        "try:\n"
        "    module.cmd_prompt()\n"
        "finally:\n"
        "    module.cleanup()\n", encoding="utf-8")
    shown = subprocess.run([sys.executable, "-I", str(prompt), str(SCRIPT)], env=env, cwd=home, input="",
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300).stdout
    check("the prompt: what each account has left", any("ultra" in line and "70% until " in line
                                                         for line in shown.splitlines()))

    # auto on: the flag, the hook, the watch, and the best account at once.
    check("auto: off at first", "Automatic switching is off" in out(run("auto")))
    (state / "desktop").unlink(missing_ok=True)
    result = run("auto", "on", FAKE_DESKTOP_SWITCH="3,0")
    check("auto on: exit 0", result.returncode == 0)
    check("auto on: the flag", (store / ".auto").exists())
    check("auto on: one hook of claude-switch", len(own_hooks()) == 1)
    handler = read(settings)["hooks"]["StopFailure"][-1]["hooks"][0]
    if WINDOWS:
        # The hooks reference: with args, command is spawned with no shell, and must be a real program on Windows.
        runner = pathlib.Path(handler.get("command", "")).name.lower()
        check("auto on: the hook runs Python, with no shell between", runner in ("pyw.exe", "pythonw.exe", "python.exe")
              and "shell" not in handler and pathlib.Path(handler["command"]).is_file())
        check("auto on: the hook runs this claude-switch", handler.get("args", [])[-3:] == ["-I", str(SCRIPT), "hook"])
        check("auto on: no console window, through pyw.exe when it's there",
              runner == "pyw.exe" or not shutil.which("pyw.exe", path=env["PATH"]))
    else:
        check("auto on: the hook runs this claude-switch", handler.get("command") == f"{SCRIPT} hook"
              and "args" not in handler)
    check("auto on: in the background, waking the session", handler.get("asyncRewake") is True
          and handler.get("timeout") == 120)
    check("auto on: on the errors a switch can help",
          read(settings)["hooks"]["StopFailure"][-1]["matcher"] == "rate_limit|billing_error|authentication_failed|unknown")
    check("auto on: the other hooks stay", read(settings)["hooks"]["Stop"][0]["hooks"][0]["command"] == "other-stop"
          and read(settings)["hooks"]["StopFailure"][0]["hooks"][0]["command"] == "other-failure")
    check("auto on: the rest of settings stays", read(settings)["env"]["ENABLE_TOOL_SEARCH"] == "true"
          and (WINDOWS or stat.S_IMODE(settings.stat().st_mode) == 0o600))
    if WINDOWS:
        check("auto on: the watch task", "/Change /TN claude-switch-watch /ENABLE" in log("watch"))
    else:
        check("auto on: the watch timer", "--user enable --now claude-switch-watch.timer" in log("watch").splitlines())
    check("auto on: to Ultra, the best", live_access() == "U-token"
          and "Switched to ultra: ultra is the best account with room." in out(result))
    check("auto on: Desktop restarted without a question", calls() == "switch acct-u|quit|switch acct-u|start|")
    check("auto on: a notification", "Switched to ultra" in log("notify"))
    before = settings.read_bytes()
    result = run("auto", "on")
    check("auto on again: settings unchanged", settings.read_bytes() == before)
    check("auto on again: stays", "It stays on ultra, the best account with room." in out(result))
    result = run("auto")
    check("auto: says it's on, and the best", "Automatic switching is on" in out(result)
          and "The best account with room now is ultra." in out(result))

    # The watch: nothing without an open session, then the best account with room.
    Server.answers[("/api/oauth/usage", "U-token")] = (200, week(100, now() + 2 * DAY))
    stale(U)
    count = len(Server.requests)
    result = run("watch")
    check("watch, no session: nothing read", result.returncode == 0 and len(Server.requests) == count
          and live_access() == "U-token")
    # A session file whose process is another one now, and one whose process ended.
    open_session(started="1")
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()
    # Without procStart, only whether the process runs tells: it ended, though this test still holds it.
    write(sessions / "ended.json", {"pid": ended.pid})
    run("watch")
    check("watch: a session that isn't running counts for nothing", len(Server.requests) == count
          and live_access() == "U-token")
    (sessions / "ended.json").unlink()
    open_session()
    (state / "desktop").unlink(missing_ok=True)
    stale(U)
    result = run("watch")
    check("watch: a full week goes to the next rank, a key", result.returncode == 0 and env_token() == "G-secret")
    check("watch: says why", re.search(r"^Switched to gw: ultra reached its weekly limit, until ", result.stdout,
                                       re.MULTILINE) is not None)
    check("watch: a key leaves Desktop alone", log("desktop") == "")
    count = len(Server.requests)
    # The log past 1 MB keeps its last lines.
    (store / ".auto.log").write_text(("0" * 600 + "\n") * 2200, encoding="utf-8")
    result = run("watch")
    check("watch: on the best already, nothing to say", out(result) == "" and marked("gw"))
    check("watch: a log past 1 MB keeps its last lines", sum(
        line.startswith("0") for line in (store / ".auto.log").read_text(encoding="utf-8").splitlines()) == 2000)
    check("watch: a full week isn't read again before it resets", not requests_since(count, "U-token"))
    change_reading("key_gw", at=now() - 250000)
    count = len(Server.requests)
    run("watch")
    check("watch: what the last run read is read again", requests_since(count, "G-secret"))
    Server.answers[("/api/oauth/usage", "U-token")] = (200, week(5, now() + 7 * DAY))
    week_passed(U)
    run("watch")
    check("watch: back to Ultra once its week reset", live_access() == "U-token" and env_token() == "" and marked("ultra"))

    # A switch typed by hand holds while its account has room.
    old_switch()
    run("a@example.com")
    check("by hand: switched", marked("a@example.com"))
    check("by hand: held", (store / ".hold").read_text(encoding="utf-8").strip() == f"{A}.json")
    check("by hand: the time of the switch, for the hook",
          int((store / ".switched").read_text(encoding="utf-8")) > now() - 30000)
    result = run("watch")
    check("by hand: the watch keeps it", marked("a@example.com") and out(result) == "")
    Server.answers[("/api/oauth/usage", "A-token")] = (200, week(100, now() + 3 * DAY, 10))
    stale(A)
    run("watch")
    check("by hand: let go once it runs out", marked("ultra") and not (store / ".hold").exists())

    # Nothing has room: the machine stays, one notification a day.
    Server.answers[("/api/oauth/usage", "U-token")] = (200, week(100, now() + 2 * DAY))
    week_passed(U)
    Server.answers[("/key/info", "G-secret")] = (200, budget(25.5, now() + 10 * DAY))
    stale("key_gw")
    subprocess.run([sys.executable, "-I", str(share / "usage.py"), "refuse", str(store / "key_api.json"), "billing_error"],
                   env=env, capture_output=True, timeout=120)
    (state / "notify").unlink(missing_ok=True)
    result = run("watch")
    check("nothing has room: stays", result.returncode == 0 and marked("ultra"))
    check("nothing has room: a notification", "No Claude account has room" in log("notify"))
    check("nothing has room: says which comes back first", "The first to have room again: api" in log("notify"))
    run("watch")
    check("nothing has room: once a day", log("notify").count("No Claude account") == 1)

    # The hook: a session meets the limit of the active account.
    for path in (store / ".usage").glob("*.refused.json"):
        path.unlink()
    Server.answers.update({("/api/oauth/usage", "U-token"): (200, week(30, now() + 2 * DAY)),
                           ("/key/info", "G-secret"): (200, budget(3.2, now() + 10 * DAY)),
                           ("/api/oauth/usage", "A-token"): (200, week(60, now() + 3 * DAY, 10))})
    shutil.rmtree(store / ".usage")
    run("watch")
    check("hook setup: on Ultra", marked("ultra"))
    Server.answers[("/api/oauth/usage", "U-token")] = (200, week(100, now() + 2 * DAY))
    # A log past 1 MB, cut before the hook writes to it.
    (store / ".auto.log").write_text(("0" * 600 + "\n") * 2200, encoding="utf-8")
    old_switch()
    failed = now()
    result = hook({"session_id": "s1", "hook_event_name": "StopFailure", "error": "rate_limit",
                   "last_assistant_message": "You've hit your weekly limit"},
                  ANTHROPIC_AUTH_TOKEN="from-the-session", CLAUDE_CODE_OAUTH_TOKEN="from-the-session")
    check("hook: exit 2 wakes the session", result.returncode == 2)
    check("hook: the account that stopped is refused until its week resets",
          refused(U) and read(usage_file(U, refused=True))["until"] > now() + DAY)
    check("hook: the next account is live", env_token() == "G-secret")
    check("hook: one sentence for Claude", len(result.stderr.splitlines()) == 1 and re.fullmatch(
        r"claude-switch moved this machine to gw: ultra reached its weekly limit, until .*\. The request that failed "
        r"can go out again now: carry on with the task where it stopped\.", result.stderr.strip()) is not None)
    check("hook: nothing on stdout", result.stdout == "")
    check("hook: claude-switch said the rest in its log",
          "Switched to gw, a gateway key" in (store / ".auto.log").read_text(encoding="utf-8"))
    check("hook: a log past 1 MB keeps its last lines", sum(
        line.startswith("0") for line in (store / ".auto.log").read_text(encoding="utf-8").splitlines()) == 2000)
    check("hook: a wake counted", len((store / ".woken" / "s1").read_text(encoding="utf-8").split()) == 1)

    # A second session failed before that switch: it only has to send its request again.
    transcript = root / "transcript.jsonl"
    transcript.write_text(json.dumps({"type": "assistant", "isApiErrorMessage": True, "timestamp": datetime.datetime.fromtimestamp(
        failed / 1000 - 5, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")}) + "\n", encoding="utf-8")
    result = hook({"session_id": "s2", "error": "rate_limit", "last_assistant_message": "x",
                   "transcript_path": str(transcript)})
    check("a second session: woken", result.returncode == 2
          and "has moved this machine to gw since the request failed" in result.stderr)
    check("a second session: the new account isn't refused for it", not refused("key_gw") and env_token() == "G-secret")
    # A transcript whose last API error is an hour old doesn't say when this one failed.
    transcript.write_text(json.dumps({"type": "assistant", "isApiErrorMessage": True, "timestamp": datetime.datetime.fromtimestamp(
        time.time() - 3600, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")}) + "\n" + json.dumps({"type": "user"})
                          + "\n", encoding="utf-8")
    Server.answers[("/key/info", "G-secret")] = (200, budget(25.5, now() + 10 * DAY))
    old_switch()
    result = hook({"session_id": "s2", "error": "unknown", "last_assistant_message": "Budget has been exceeded!",
                   "transcript_path": str(transcript)})
    check("an old error in the transcript: this failure still refuses", result.returncode == 2 and refused("key_gw"))
    usage_file("key_gw", refused=True).unlink()
    Server.answers[("/key/info", "G-secret")] = (200, budget(3.2, now() + 10 * DAY))
    run("gw")
    (store / ".hold").unlink(missing_ok=True)

    # A gateway's budget comes back as an unknown error.
    result = hook({"session_id": "s3", "error": "unknown", "last_assistant_message": "API Error: 500 something else"})
    check("unknown error: nothing", result.returncode == 0 and result.stderr == "" and not refused("key_gw"))
    Server.answers[("/key/info", "G-secret")] = (200, budget(25.5, now() + 10 * DAY))
    old_switch()
    result = hook({"session_id": "s3", "error": "unknown",
                   "last_assistant_message": "API Error: 422 Budget has been exceeded! Key=me Current cost: 25.5"})
    check("a spent budget: refused until it resets", result.returncode == 2 and refused("key_gw")
          and read(usage_file("key_gw", refused=True))["until"] > now() + 9 * DAY)
    check("a spent budget: the next key", env_token() == "K-secret")
    (state / "desktop").unlink(missing_ok=True)
    old_switch()
    result = hook({"session_id": "s4", "error": "billing_error", "last_assistant_message": "Credit balance is too low"},
                  FAKE_DESKTOP_SWITCH="3,0")
    check("no credit: refused", result.returncode == 2 and refused("key_api") and "api ran out of credit" in result.stderr)
    check("no credit: on to the plan with a window", live_access() == "A-token" and env_token() == "")
    check("no credit: Desktop follows without a question", calls() == "switch acct-a|quit|switch acct-a|start|inside|")
    # Windows hands a program its standard handles, not the files Python writes to: what the helper said must be in the
    # log, not in what Claude reads.
    check("no credit: what the Desktop helper said is in the log only", result.stdout == ""
          and "desktop helper ran: switch acct-a" not in result.stderr
          and "desktop helper ran: switch acct-a" in (store / ".auto.log").read_text(encoding="utf-8"))
    kept_refusal = usage_file("key_api", refused=True).read_bytes()
    run("key", "renew", "api", stdin="K-secret\n")
    check("no credit: a new key ends the wait", not refused("key_api"))
    usage_file("key_api", refused=True).write_bytes(kept_refusal)

    # Nothing left: no wake, Claude Code waits for the reset itself.
    Server.answers[("/api/oauth/usage", "A-token")] = (200, week(100, now() + 3 * DAY, 10))
    old_switch()
    result = hook({"session_id": "s5", "error": "rate_limit", "last_assistant_message": "x"})
    check("nothing left: no wake", result.returncode == 0 and result.stderr == "")

    # A session every account refuses isn't woken more than 3 times in 10 minutes.
    shutil.rmtree(store / ".usage")
    Server.answers.update({("/api/oauth/usage", "A-token"): (200, week(60, now() + 3 * DAY, 10)),
                           ("/api/oauth/usage", "U-token"): (200, week(30, now() + 2 * DAY))})
    write(store / ".woken" / "s6", "".join(f"{now()}\n" for _ in range(3)))
    result = hook({"session_id": "s6", "error": "rate_limit", "last_assistant_message": "x"})
    check("3 wakes in 10 minutes: no fourth", result.returncode == 0 and result.stderr == "")

    # The order the kinds of account rank in.
    check("auto order: Ultra, then keys, then the other plans at first",
          "in this order: Ultra subscriptions, then API and gateway keys, then Max and the other plans." in out(
              run("auto", "order")))
    run("auto", "order", "ultra")
    check("auto order ultra: to Ultra at once", marked("ultra"))
    result = run("auto", "order", "max")
    check("auto order max: kept, the others after",
          (store / ".auto-order").read_text(encoding="utf-8").strip() == "max ultra key")
    check("auto order max: says it",
          "order now: Max and the other plans, then Ultra subscriptions, then API and gateway keys." in out(result))
    check("auto order max: to the plan with a window at once", marked("a@example.com"))
    check("auto: says the order", "It ranks the accounts in this order: Max and the other plans, then" in out(run("auto")))
    run("auto", "order", "api", "ultra")
    check("auto order api: a key", (store / ".auto-order").read_text(encoding="utf-8").strip() == "key ultra max")
    result = run("auto", "order", "pro")
    check("auto order: an unknown kind is refused", result.returncode != 0
          and "pro is not a kind of account: ultra, max or key." in out(result)
          and (store / ".auto-order").read_text(encoding="utf-8").strip() == "key ultra max")
    result = run("auto", "order", "max", "max")
    check("auto order: a kind twice is refused", result.returncode != 0 and "max is twice in the order." in out(result))
    run("auto", "order", "ultra", "key", "max")

    # A session of Claude Desktop meets the limit: the machine moves on, Desktop keeps its account, and the session isn't
    # woken, since only a restart of Desktop, which would end it, gives it another login.
    subprocess.run([sys.executable, "-I", str(share / "usage.py"), "refuse", str(store / "key_api.json"), "billing_error"],
                   env=env, capture_output=True, timeout=120)
    Server.answers[("/api/oauth/usage", "U-token")] = (200, week(100, now() + 2 * DAY))
    old_switch()
    (state / "desktop").unlink(missing_ok=True)
    (state / "notify").unlink(missing_ok=True)
    result = hook({"session_id": "s8", "error": "rate_limit", "last_assistant_message": "x"},
                  FAKE_DESKTOP_SWITCH="6", FAKE_DESKTOP_INSIDE="0")
    check("a session of Desktop: the machine moves on", marked("a@example.com"))
    check("a session of Desktop: Desktop left alone", calls() == "switch acct-a|inside|")
    check("a session of Desktop: not woken", result.returncode == 0 and result.stderr == ""
          and "it is not woken" in (store / ".auto.log").read_text(encoding="utf-8"))
    check("a session of Desktop: the notification says how to bring Desktop along",
          "Claude Desktop stays on its account: close it and run claude-switch a@example.com to bring it along."
          in log("notify"))

    # auto off: the hook and the watch go, the rest stays, with a hook whose program is the claude-switch.
    data = read(settings)
    data["hooks"]["StopFailure"].append({"matcher": "rate_limit", "hooks": [
        {"type": "command", "command": "C:\\old\\claude-switch\\pythonw.exe", "args": ["-I", "C:\\old\\run.py", "hook"]}]})
    write(settings, data)
    run("auto", "off")
    check("auto off: the flag goes", not (store / ".auto").exists())
    check("auto off: only the hook of claude-switch goes",
          [entry["hooks"][0]["command"] for entry in read(settings)["hooks"]["StopFailure"]] == ["other-failure"]
          and read(settings)["hooks"]["Stop"][0]["hooks"][0]["command"] == "other-stop")
    if WINDOWS:
        check("auto off: the watch task stops", "/Change /TN claude-switch-watch /DISABLE" in log("watch"))
    else:
        check("auto off: the timer stops", "--user disable --now claude-switch-watch.timer" in log("watch").splitlines())
    before = live_access()
    result = hook({"session_id": "s7", "error": "rate_limit", "last_assistant_message": "x"})
    check("auto off: the hook does nothing", result.returncode == 0 and live_access() == before)
    # The Ultra has room again: a watch that ran would switch to it.
    usage_file(U, refused=True).unlink(missing_ok=True)
    Server.answers[("/api/oauth/usage", "U-token")] = (200, week(30, now() + 2 * DAY))
    stale(U)
    result = run("watch")
    check("auto off: the watch does nothing", out(result) == "" and live_access() == before)
    run("auto", "off")
    check("auto off twice: settings left alone", len(read(settings)["hooks"]["StopFailure"]) == 1)

    # A settings.json in a folder this user can't write: auto on refuses before anything changes.
    if WINDOWS:
        subprocess.run(["icacls", str(claude_dir), "/deny", f"*{user_sid()}:(WD,AD)"], capture_output=True, check=True)
    else:
        claude_dir.chmod(0o555)
    denied.append(claude_dir)
    result = run("auto", "on")
    check("read-only settings: refused", result.returncode != 0 and "can't write" in out(result))
    check("read-only settings: automatic switching stays off", not (store / ".auto").exists())
finally:
    for folder in denied:
        if WINDOWS:
            subprocess.run(["icacls", str(folder), "/remove:d", f"*{user_sid()}"], capture_output=True)
        else:
            folder.chmod(0o755)
    server.shutdown()
    shutil.rmtree(root, ignore_errors=True)

print(f"{passes} passed, {len(failures)} failed")
sys.exit(1 if failures else 0)
