#!/usr/bin/env -S python3 -I
"""Switches the Claude Code account of the whole machine, from any folder. The Windows port of bin/claude-switch.

Only the account changes: the login in ~/.claude/.credentials.json and the account in ~/.claude.json, which are
%USERPROFILE%\\.claude\\.credentials.json and %USERPROFILE%\\.claude.json on Windows. There is one ~/.claude, so the
configuration, rules, MCP servers and their logins, sessions and memory stay as they are. Never use
CLAUDE_CONFIG_DIR for this: it splits all of that.

Each account is one file in ~/.claude-accounts. It holds the login, so only you can read it.

This file follows bin/claude-switch function by function and keeps its messages, so the suites in tests/ check it
on Linux too. What only Windows does is marked TODO(windows) until it ran there, see windows/README.md.

The accounts that are not active stay signed in through `claude-switch refresh`, which the claude-switch-refresh
task runs at logon and every 4 hours. claude refreshes each of them in a folder of its own where no session runs,
and the rotated login comes back to its file. That renews the login, not the sign-in: only a new sign-in moves the
date a sign-in ends, so that is announced.

claude.ai in Chrome follows a switch when the companion in ~/.local/share/claude-switch is installed: its extension
swaps the claude.ai session cookies, see chrome-host.py there. Claude Desktop follows too, through desktop.py
there, once it is closed.
"""
import contextlib
import datetime
import hashlib
import json
import os
import pathlib
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import types
import unicodedata

WINDOWS = os.name == "nt"
if WINDOWS:
    import msvcrt
else:
    import fcntl
    import select

HOME = pathlib.Path.home()
CREDENTIALS = HOME / ".claude" / ".credentials.json"
CONFIG = HOME / ".claude.json"
STORE = HOME / ".claude-accounts"
SHARE = HOME / ".local" / "share" / "claude-switch"
CHROME_HOST = SHARE / "chrome-host.py"
DESKTOP_HELPER = SHARE / "desktop.py"
NOTIFY = SHARE / "notify.ps1"

# What belongs to the account in .credentials.json: what claude's own logout deletes with the login (2.1.314).
# mcpOAuth and the rest never move.
LOGIN_KEYS = ("claudeAiOauth", "designOauth", "cloudSessionsOauth", "organizationUuid", "trustedDeviceToken",
              "trustedDeviceEnrolledAfterRefusalAtMs", "enterpriseGateway")

# What claude itself clears in .claude.json when the account changes. It fetches them again.
CACHES = ("managedSettingsNoticeSeenOrg", "additionalModelOptionsCache", "additionalModelOptionsAnsweredAt",
          "additionalModelCostsCache", "modelAccessCache", "orgModelDefaultCache", "cachedArtifactRoster",
          "artifactRosterDenied", "lastSeenOrgDefaultUpdatedAt", "clientDataCache", "clientDataCacheSlots",
          "autoCompactWindowsCache", "cachedUsageUtilization", "metricsStatusCache", "metricsStatusCacheByPrincipal",
          "githubWebConnectionStatusCache", "unusedLimitResetStatusCache", "startupPrefetchedAt")

COMMANDS = ("list", "login", "rename", "refresh", "help")

# A stored access token with less than this left is refreshed. The task runs every 4 hours.
REFRESH_AHEAD_MS = 270 * 60 * 1000
# A sign-in that ends within this is announced.
WARN_AHEAD_MS = 3 * 24 * 3600 * 1000
# A sign-in that ends within this is reminded of by list, the prompt and a switch.
REMIND_AHEAD_MS = 7 * 24 * 3600 * 1000
# Offline, claude retries without end, so each run of it gets this long.
CLAUDE_SECONDS = 60

# What stops the script: TERM, INT and HUP here, Ctrl+C and Ctrl+Break on Windows.
STOPS = tuple(getattr(signal, name) for name in ("SIGTERM", "SIGINT", "SIGHUP", "SIGBREAK") if hasattr(signal, name))
# A claude run in the background opens no console window on Windows.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if WINDOWS else 0
# Windows: a Remote Control server, and the programs of Claude Desktop, which has a sign-in of its own.
REMOTE_CONTROL = re.compile(r"\sremote-control(\s|$)")
DESKTOP_PROGRAM = re.compile(r"\\WindowsApps\\Claude_|\\AnthropicClaude\\|\\Claude-Data\\[^\\]+\\claude-code\\|"
                             r"\\Packages\\Claude_[^\\]+\\LocalCache\\|\\Roaming\\Claude\\claude-code\\", re.IGNORECASE)
CREDMAN = ("Claude Code keeps its login in the Windows Credential Manager now (its flag tengu_windows_credman is on), "
           "and claude-switch switches .credentials.json only.")

LOCKS = []
SIGNIN = None
STUB = None
DEAD = False
# Set while claude.ai in Chrome is on the account of a sign-in rather than on the live one.
WEB_AWAY = False
# The open file that holds the store lock.
STORE_LOCK = None

USAGE = """\
Usage:
  claude-switch                          Prompt to switch, add or rename.
  claude-switch NAME                     Switch the whole machine to NAME, a name or an email.
  claude-switch list                     Print the accounts. * marks the active one.
  claude-switch login [ACCOUNT] [OPTIONS]
                                         Sign in an account and keep it here. It doesn't switch.
                                         With ACCOUNT, a name or an email already here, it renews
                                         that one: claude.ai in Chrome goes to it first and the sign-in
                                         page asks for its email. OPTIONS go to claude auth login.
  claude-switch rename NAME NEW_NAME     Rename an account.
  claude-switch refresh [--force]        Refresh the login of the accounts that are not active.
                                         The claude-switch-refresh task runs it at logon and every
                                         4 hours. --force refreshes them even when not due.
  claude-switch help                     Print this help, as -h and --help do.

A switch changes the account only, wherever you call it. New sessions start on it and running
sessions move to it at their next request. claude.ai in Chrome follows it too, once the companion
extension in ~/.local/share/claude-switch/chrome-extension is loaded in Chrome. Claude Desktop
follows it as well: an open Desktop is restarted when you answer yes in a terminal. A Remote Control
server (claude remote-control) is stopped when you answer yes, so what keeps it running starts it
again on the new account.

A sign-in lasts about 28 days, and only a new sign-in moves its end. From 7 days before, list, the
prompt and a switch say when it ends, with the claude-switch login that renews it.

Don't use /logout on an account you want to keep: it revokes the login saved here too.
To forget an account, remove its file in ~/.claude-accounts.
"""


def die(message):
    print(f"claude-switch: {message}", file=sys.stderr)
    sys.exit(1)


def warn(message):
    print(f"claude-switch: {message}", file=sys.stderr)


def stop(number, frame):
    sys.exit(143)


@contextlib.contextmanager
def stops_deferred():
    """A stop that comes during the block waits until it is done, then ends the script as it would have."""
    caught = []
    previous = {number: signal.signal(number, lambda number, frame: caught.append(number)) for number in STOPS}
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    if caught:
        sys.exit(143)


def dig(data, *keys):
    """DATA[key][key]..., None as soon as a level is not an object."""
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def alt(value, default):
    """jq's VALUE // DEFAULT: null and false don't count."""
    return default if value is None or value is False else value


def number(value):
    """A time in ms of the store, 0 when there is none."""
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def now_ms():
    return int(time.time() * 1000)


def load(path):
    """The JSON in PATH. A BOM a Windows editor may have added is skipped."""
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8-sig"))


def load_or_none(path):
    try:
        return load(path)
    except (OSError, ValueError):
        return None


def dumps(data):
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def write_private(path, text):
    """TEXT in PATH, readable only by you. UTF-8 without a BOM and with Linux line ends, as claude writes it."""
    pathlib.Path(path).unlink(missing_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def replace(source, target):
    """os.replace. On Windows a file another program reads can't be replaced for a moment, so it tries again."""
    tries = 50 if WINDOWS else 1
    while True:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            tries -= 1
            if tries <= 0:
                raise
            time.sleep(0.1)


def save(path, data):
    """DATA in PATH, written aside first and renamed after."""
    temporary = pathlib.Path(f"{path}.tmp")
    write_private(temporary, dumps(data))
    replace(temporary, path)


def make_store():
    STORE.mkdir(parents=True, exist_ok=True)
    if not WINDOWS:
        STORE.chmod(0o700)


def accounts():
    """The account files, in the order of the list."""
    return sorted(path for path in STORE.glob("*.json") if path.is_file() and not path.name.startswith("."))


def quote(text):
    """TEXT as one argument of a command to type: as is when nothing in it needs quotes, else quoted for PowerShell
    on Windows and for the shell elsewhere. Windows PowerShell may split a word at a comma, and cmd expands %."""
    if not WINDOWS:
        return shlex.quote(text)
    if re.fullmatch(r"[\w.:/=+-][\w@.:/=+-]*", text):
        return text
    return "'" + text.replace("'", "''") + "'"


def is_dead(data):
    return alt(dig(data, "dead"), None) is not None


def signed_in(credentials):
    """A login with a token in it. claude empties both tokens when the server refuses a refresh."""
    token = dig(credentials, "claudeAiOauth", "accessToken")
    return isinstance(token, str) and len(token) > 0


def account_file(config_path):
    """The file of the account in a .claude.json, None when it holds no account."""
    account = dig(load_or_none(config_path), "oauthAccount")
    email = dig(account, "emailAddress")
    if email is None:
        return None
    organization = str(alt(dig(account, "organizationUuid"), "none"))[:8]
    return STORE / (re.sub(r"[^\w@.-]", "_", f"{email}_{organization}") + ".json")


def live_file():
    """The file of the live account, None when nobody is signed in."""
    if not signed_in(load_or_none(CREDENTIALS)):
        return None
    return account_file(CONFIG)


def pick_login(credentials):
    return {key: value for key, value in credentials.items() if key in LOGIN_KEYS and value is not None}


def store_account(credentials_path, config_path, file):
    """The account of CREDENTIALS_PATH and CONFIG_PATH in FILE. The name stays when the file exists."""
    make_store()
    name = dig(load_or_none(file), "name") if file.is_file() else ""
    credentials = load(credentials_path)
    save(file, {"name": name if isinstance(name, str) else "", "login": pick_login(credentials),
                "oauthAccount": dig(load(config_path), "oauthAccount")})


def fingerprint(path, under_login):
    """A fingerprint of the tokens of every grant in PATH, never the tokens themselves. None when PATH can't be read."""
    data = load_or_none(path)
    if data is None:
        return None
    if under_login:
        data = dig(data, "login")
    tokens = []
    for grant in ("claudeAiOauth", "designOauth", "cloudSessionsOauth"):
        tokens += [dig(data, grant, "accessToken"), dig(data, grant, "refreshToken")]
    return hashlib.sha256(json.dumps(tokens).encode()).hexdigest()


def holder_of_live(file):
    """The stored account, other than FILE, whose tokens are the live ones. Two accounts never share a token."""
    live = fingerprint(CREDENTIALS, False)
    if live is None:
        return None
    return next((other for other in accounts() if other != file and fingerprint(other, True) == live), None)


def save_live():
    """Refresh tokens rotate, so the live login is saved before anything replaces it."""
    file = live_file()
    if file is not None:
        store_account(CREDENTIALS, CONFIG, file)


def repair_live():
    """A switch cut between its two renames, by a kill or a power cut, leaves the login of one account under the name
    of the other. Every command completes it before it saves the live login."""
    file = live_file()
    other = holder_of_live(file) if file is not None else None
    if other is None:
        return
    lock_live()
    apply_account(other)
    release_locks()
    warn(f"A switch to {read_account(other).label} had been cut short, it is completed.")


def local_date(ms):
    seconds = number(ms) // 1000
    return datetime.datetime.fromtimestamp(seconds).strftime("%d/%m/%Y") if seconds > 0 else "-"


def read_account(file):
    """name, email, plan, ends (the date the sign-in ends or "signed out") and label of an account file."""
    data = load(file)
    login = dig(data, "login", "claudeAiOauth")
    name = dig(data, "name")
    name = name if isinstance(name, str) else ""
    email = dig(data, "oauthAccount", "emailAddress")
    email = email if isinstance(email, str) else ""
    ends = "signed out" if is_dead(data) else local_date(alt(dig(login, "refreshTokenExpiresAt"), 0))
    return types.SimpleNamespace(data=data, name=name, email=email, plan=str(alt(dig(login, "subscriptionType"), "-")),
                                 ends=ends, label=name or email)


def login_hint(label):
    """The command that renews the account called LABEL."""
    return f"claude-switch login {quote(label)}"


def find_account(who):
    """The files of the accounts called WHO, by name or email."""
    found = []
    for file in accounts():
        data = load_or_none(file)
        if dig(data, "name") == who or dig(data, "oauthAccount", "emailAddress") == who:
            found.append(file)
    return found


def require_account(who):
    """The file of the account called WHO, or the reason it can't be chosen and an exit."""
    found = find_account(who)
    if len(found) > 1:
        die(f"Several accounts match {who}. Name them in the prompt (r) and use the name.")
    if not found:
        die(f"No account called {who}. Add it with: claude-switch login")
    return found[0]


def name_problem(name, file=None):
    """What is wrong with NAME for FILE, None when it's fine."""
    if name == "" or name.startswith("-") or any(unicodedata.category(char) == "Cc" for char in name):
        return "A name must not be empty, start with - or hold control characters."
    if name in COMMANDS:
        return f"{name} is a command of this script, choose another name."
    if re.fullmatch(r"[0-9]+", name):
        return f"{name} can only be a number in the list, the prompt selects by number."
    found = find_account(name)
    if len(found) > 1 or (len(found) == 1 and found[0] != file):
        return f"Another account is already called {name}."
    return None


def set_name(file, name):
    data = load(file)
    data["name"] = name
    save(file, data)


def try_lock(handle):
    try:
        if WINDOWS:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def lock_store():
    """One switch, sign-in or refresh at a time on the store. A refresh holds it while its folder has the login, so a
    switch never puts live a token that is being rotated. flock here, a byte range lock on Windows."""
    global STORE_LOCK
    unlock_store()
    make_store()
    handle = open(STORE / ".lock", "a+b")
    if not try_lock(handle):
        warn("Waiting for the background refresh of an account...")
        deadline = time.monotonic() + 300
        while not try_lock(handle):
            if time.monotonic() > deadline:
                handle.close()
                die("The accounts stayed locked for 5 minutes, nothing changed.")
            time.sleep(0.2)
    STORE_LOCK = handle


def unlock_store():
    global STORE_LOCK
    if STORE_LOCK is not None:
        STORE_LOCK.close()
        STORE_LOCK = None


def take_lock(path):
    """The locks claude takes itself, with mkdir. A holder touches its lock every 5 seconds, so one older than 15 is
    dead: claude auth status leaves them behind. Without these locks a session in the middle of a write brings the
    previous account back. The Windows claude takes the same ones (.oauth_refresh.lock through proper-lockfile's
    <file>.lock folders, read in the 2.1.302 binary on 09/11/2026)."""
    tries = 0
    while True:
        try:
            os.mkdir(path)
            break
        except OSError:
            pass
        with contextlib.suppress(OSError):
            if time.time() - os.stat(path).st_mtime > 15:
                os.rmdir(path)
                continue
        tries += 1
        if tries >= 200:
            die(f"A running session holds {path}, nothing changed. Try again.")
        time.sleep(0.1)
    LOCKS.append(path)


def release_locks():
    for path in LOCKS:
        with contextlib.suppress(OSError):
            os.rmdir(path)
    LOCKS.clear()


def lock_live():
    take_lock(HOME / ".claude" / ".oauth_refresh.lock")
    take_lock(HOME / ".claude.lock")
    take_lock(pathlib.Path(f"{CONFIG}.lock"))


def apply_account(file):
    """Puts FILE in place of the live login. Nothing but the account changes. Both files are written aside first and
    renamed after, since running sessions read them at any moment: a failure on the way leaves the previous account
    whole, never half a switch."""
    for path in (CREDENTIALS, CONFIG):
        if not path.exists():
            write_private(path, "{}\n")
    account = load(file)
    credentials = {key: value for key, value in load(CREDENTIALS).items() if key not in LOGIN_KEYS}
    credentials.update(dig(account, "login") or {})
    config = load(CONFIG)
    for key in CACHES:
        config.pop(key, None)
    config["oauthAccount"] = dig(account, "oauthAccount")
    write_private(f"{CREDENTIALS}.switch", dumps(credentials))
    write_private(f"{CONFIG}.switch", dumps(config))
    # A stop between the renames would leave the login of one account under the name of the other, so it waits
    # until both are done.
    with stops_deferred():
        replace(f"{CREDENTIALS}.switch", CREDENTIALS)
        replace(f"{CONFIG}.switch", CONFIG)


def check_login_keys():
    """A login a later claude keeps under a new key would stay behind on a switch, so it is announced."""
    credentials = load_or_none(CREDENTIALS)
    unknown = sorted(set(credentials) - {"mcpOAuth"} - set(LOGIN_KEYS)) if isinstance(credentials, dict) else []
    if unknown:
        warn(f"~/.claude/.credentials.json also holds {', '.join(unknown)}, which a switch does not move.")


def claude_env():
    """The environment without what a calling Claude Code session or an API key would pass on to claude."""
    return {key: value for key, value in os.environ.items() if not key.upper().startswith(("CLAUDE", "ANTHROPIC"))}


def claude():
    return shutil.which("claude") or "claude"


def helper_command(path):
    """How to run a helper of SHARE, None when it is not installed. Windows runs a .py through this Python."""
    if WINDOWS:
        return [sys.executable, "-I", str(path)] if path.is_file() else None
    return [str(path)] if path.is_file() and os.access(path, os.X_OK) else None


def run(command):
    """The status of COMMAND, run in this terminal."""
    try:
        return subprocess.run(command).returncode
    except OSError:
        return 127


def web_switch(file):
    """claude.ai in Chrome follows the switch, when the Chrome companion is installed."""
    helper = helper_command(CHROME_HOST)
    account = dig(load_or_none(file), "oauthAccount", "accountUuid") if helper else None
    if not account:
        return
    if run(helper + ["--switch", account]) != 0:
        warn("Switch claude.ai in Chrome by hand this time.")


def web_back():
    """claude.ai in Chrome back on the account Claude Code is on."""
    global WEB_AWAY
    WEB_AWAY = False
    live = live_file()
    if live is not None:
        web_switch(live)


def terminal():
    return bool(sys.stdin and sys.stdout and sys.stdin.isatty() and sys.stdout.isatty())


def ask(question, seconds):
    """The answer typed in the terminal, "" when none comes within SECONDS."""
    sys.stdout.write(question)
    sys.stdout.flush()
    if WINDOWS:
        # TODO(windows): try the question in Windows Terminal, with PowerShell and with cmd.
        answer = ""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if not msvcrt.kbhit():
                time.sleep(0.05)
                continue
            char = msvcrt.getwche()
            if char in "\r\n":
                print()
                return answer.strip()
            if char == "\x03":
                sys.exit(143)
            if char == "\b":
                answer = answer[:-1]
                sys.stdout.write(" \b")
                sys.stdout.flush()
            else:
                answer += char
        print()
        return ""
    if not select.select([sys.stdin], [], [], seconds)[0]:
        print()
        return ""
    return sys.stdin.readline().strip()


def desktop_switch(file, label):
    """Claude Desktop follows the switch, when it is installed. It must be closed for that, so an open one is
    restarted when the terminal says yes."""
    helper = helper_command(DESKTOP_HELPER)
    account = dig(load_or_none(file), "oauthAccount", "accountUuid") if helper else None
    if not account or run(helper + ["switch", account]) != 3:
        return
    answer = ""
    if terminal():
        answer = ask(f"Claude Desktop is open. Restart it on {label}? Code and Cowork tasks running in it stop. [y/N] ", 60)
    if answer not in ("y", "Y", "yes", "Yes"):
        print(f"Claude Desktop is open, so it stays on its account. Close it and run claude-switch {quote(label)} "
              "to bring it along.")
        return
    if run(helper + ["quit"]) != 0:
        warn("Claude Desktop didn't close, it stays on its account.")
        return
    # It was open before, so it opens again whatever the switch says.
    run(helper + ["switch", account])
    if run(helper + ["start"]) != 0:
        warn("Start Claude Desktop again by hand.")


def remote_control_servers():
    """Windows: the pids of the Remote Control servers here, claude remote-control run outside Claude Desktop."""
    if not WINDOWS:
        return []
    command = ("[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; "
               "ConvertTo-Json -Compress -InputObject @(Get-CimInstance Win32_Process -Filter \"Name = 'claude.exe'\" | "
               "Select-Object ProcessId, ExecutablePath, CommandLine)")
    try:
        output = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                                capture_output=True, timeout=60, creationflags=NO_WINDOW).stdout
        processes = json.loads(output.decode("utf-8") or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []
    return [process["ProcessId"] for process in (processes if isinstance(processes, list) else [processes])
            if isinstance(process, dict) and REMOTE_CONTROL.search(process.get("CommandLine") or "")
            and not DESKTOP_PROGRAM.search(process.get("ExecutablePath") or "")]


def remote_control_switch(label):
    """Windows: a Remote Control server registered itself with the account it started on, so after a switch it serves
    that account still. In a terminal it stops when you say so, and whatever keeps it running starts it on LABEL."""
    servers = remote_control_servers()
    if not servers:
        return
    answer = ""
    if terminal():
        answer = ask("Remote Control serves this PC on the account it started on. Stop its server, so it starts again "
                     f"on {label}? Sessions started from the phone stop. [y/N] ", 60)
    if answer not in ("y", "Y", "yes", "Yes"):
        print("Remote Control stays on the account it started on, until its server starts again.")
        return
    for pid in servers:
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30,
                           creationflags=NO_WINDOW)
    print(f"Remote Control: its server stopped. A keep-alive starts it again on {label}, else run claude remote-control.")


def credential_manager():
    """Windows: True when Claude Code keeps its login in the Windows Credential Manager instead of .credentials.json,
    which its flag tengu_windows_credman turns on (read in the 2.1.302 binary, off on the PC on 09/11/2026). The files
    a switch moves would then not be the login."""
    return WINDOWS and dig(load_or_none(CONFIG), "cachedGrowthBookFeatures", "tengu_windows_credman") is True


def warn_environment():
    for var in ("CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
        if os.environ.get(var):
            warn(f"{var} is set in this shell, a claude started from here doesn't follow the switch.")


def switch_to(file):
    account = read_account(file)
    if file == live_file():
        print(f"Already on {account.label}.")
        web_switch(file)
        desktop_switch(file, account.label)
        return
    if is_dead(account.data):
        die(f"{account.label} was signed out, nothing changed. Sign in again: {login_hint(account.label)}")
    ends = number(dig(account.data, "login", "claudeAiOauth", "refreshTokenExpiresAt"))
    if 0 < ends < now_ms():
        die(f"The login of {account.label} ended on {account.ends}, nothing changed. "
            f"Sign in again: {login_hint(account.label)}")
    lock_live()
    save_live()
    apply_account(file)
    release_locks()
    if live_file() != file:
        die("A running session wrote over the switch. Run it again.")
    warn_environment()
    check_login_keys()
    print(f"Switched to {account.label}.")
    web_switch(file)
    desktop_switch(file, account.label)
    remote_control_switch(account.label)


def cmd_login(arguments):
    """Signs in beside the store and not in ~/.claude, so the live account changes only on a switch. No session ever
    runs from that folder, it's gone when this ends."""
    global SIGNIN, WEB_AWAY
    wanted = wanted_email = None
    if arguments and not arguments[0].startswith("-"):
        wanted = require_account(arguments[0])
        wanted_email = read_account(wanted).email
        # The sign-in page uses the claude.ai session of the browser, so that session goes first. It comes back
        # however the sign-in ends.
        WEB_AWAY = True
        web_switch(wanted)
        arguments = ["--email", wanted_email] + arguments[1:]
    make_store()
    SIGNIN = pathlib.Path(tempfile.mkdtemp(prefix=".signin.", dir=STORE))
    print("If the browser offers an account you already use here, switch it there or open the link in a private window.")
    if subprocess.run([claude(), "auth", "login", *arguments], env=dict(claude_env(), CLAUDE_CONFIG_DIR=str(SIGNIN))
                      ).returncode != 0:
        die("The sign-in failed, nothing changed.")
    # A Claude Code that keeps its login in the Windows Credential Manager writes no .credentials.json here. main()
    # stops before that when its flag is on, and this says it if the login moves there some other way.
    file = account_file(SIGNIN / ".claude.json") if signed_in(load_or_none(SIGNIN / ".credentials.json")) else None
    if file is None:
        die("The sign-in returned, but it left no Claude login. Nothing changed.")
    lock_store()
    known = file.is_file()
    store_account(SIGNIN / ".credentials.json", SIGNIN / ".claude.json", file)
    account = read_account(file)
    if wanted is not None and file != wanted:
        warn(f"The browser signed in {account.email}, not {wanted_email}. {account.email} is kept, "
             f"{wanted_email} is unchanged.")
    if WEB_AWAY:
        web_back()
    if known:
        if file == live_file():
            lock_live()
            apply_account(file)
            release_locks()
        print(f"{account.label} was already here, its login is renewed.")
        return
    unlock_store()
    if sys.stdin and sys.stdin.isatty():
        while True:
            try:
                answer = input(f"Name for this account (Enter to keep {account.email}): ").strip()
            except EOFError:
                break
            if not answer:
                break
            problem = name_problem(answer, file)
            if problem is None:
                lock_store()
                set_name(file, answer)
                unlock_store()
                break
            print(problem)
        account = read_account(file)
    print(f"Added {account.label}. Switch to it with: claude-switch {quote(account.label)}")


def rename_account(file, new):
    new = new.strip()
    problem = name_problem(new, file)
    if problem is not None:
        die(problem)
    old = read_account(file).label
    set_name(file, new)
    print(f"Renamed {old} to {new}.")


def truncated(dividend, divisor):
    """Integer division toward zero, as the shell does it."""
    quotient = abs(dividend) // divisor
    return quotient if dividend >= 0 else -quotient


def hours(ms):
    whole = truncated(ms, 3600000)
    return f"{whole}.{truncated(ms - whole * 3600000, 360000)}h"


def stub_moved(stub):
    """True when claude changed the tokens in STUB."""
    with contextlib.suppress(OSError):
        return fingerprint(stub / ".credentials.json", False) != (stub / ".base").read_text(encoding="utf-8").strip()
    return True


def show_notification(title, body):
    """True once the desktop showed it: notify-send here, a toast through Windows PowerShell on Windows."""
    if WINDOWS:
        # The toast shows on the PC. TODO(windows): see one the task shows itself, with no console window.
        if not NOTIFY.is_file():
            return False
        command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                   str(NOTIFY), "-Title", title, "-Body", body]
    else:
        if not shutil.which("notify-send"):
            return False
        command = ["notify-send", "--app-name=Claude accounts", title, body]
    try:
        return subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              creationflags=NO_WINDOW, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def notify_once(key, title, body):
    """A desktop notification, once a day for each KEY. One that could not be shown, at boot before the desktop is up
    for instance, is tried again on the next run."""
    today = datetime.date.today().isoformat()
    notified = STORE / ".notified"
    try:
        lines = notified.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    if f"{key} {today}" in lines or not show_notification(title, body):
        return
    kept = [line for line in lines if line.endswith(f" {today}")] + [f"{key} {today}"]
    write_private(STORE / ".notified.tmp", "".join(f"{line}\n" for line in kept))
    replace(STORE / ".notified.tmp", notified)


def stub_claude(stub, seconds, *arguments):
    """Runs claude on the folder STUB alone, never on ~/.claude: no update, no extra traffic, no connectors, and
    nothing of a calling Claude Code session or an API key in place of the login. A stop waits for claude to end,
    so a login it is writing is kept."""
    env = dict(claude_env(), CLAUDE_CONFIG_DIR=str(stub), DISABLE_AUTOUPDATER="1",
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", ENABLE_CLAUDEAI_MCP_SERVERS="false", PWD=str(stub))
    with open(stub / ".out", "ab") as output, stops_deferred():
        try:
            process = subprocess.Popen([claude(), *arguments], cwd=stub, env=env, stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        except OSError:
            return
        try:
            process.wait(seconds)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait()


def first_line(path):
    """The first line of claude's output that is not JSON."""
    with contextlib.suppress(OSError):
        lines = path.read_bytes().decode("utf-8", "replace").splitlines()
        return next((line for line in lines if not line.startswith("{")), "")
    return ""


def finish_stub(stub):
    """Brings the login claude left in STUB back to its account, or says why not. Only while the account still holds
    the tokens STUB started from: a folder left by a killed run may be older than the store."""
    global DEAD
    if not stub.is_dir():
        return
    try:
        name = (stub / ".account").read_text(encoding="utf-8").strip()
        base = (stub / ".base").read_text(encoding="utf-8").strip()
    except OSError:
        name = base = ""
    file = STORE / name
    if not name or pathlib.Path(name).name != name or not file.is_file() or fingerprint(file, True) != base:
        shutil.rmtree(stub, ignore_errors=True)
        return
    account = read_account(file)
    hint = login_hint(account.label)
    credentials = load_or_none(stub / ".credentials.json")
    login = dig(credentials, "claudeAiOauth")
    if alt(login, None) is None:
        print(f"{account.label}: not refreshed, claude left no login")
    elif alt(dig(login, "accessToken"), "") == "" or alt(dig(login, "refreshToken"), "") == "":
        # claude empties the tokens when the server refuses the refresh. Only a new sign-in helps.
        data = load(file)
        data["dead"] = datetime.date.today().strftime("%d/%m/%Y")
        save(file, data)
        print(f"{account.label}: the login was refused, sign in again: {hint}")
        notify_once(f"dead:{file.name}", f"Claude account {account.label} is signed out", f"Sign in again: {hint}")
        DEAD = True
    elif not stub_moved(stub):
        print(f"{account.label}: not refreshed this time: {first_line(stub / '.out')}")
    else:
        oauth = dig(load_or_none(stub / ".claude.json"), "oauthAccount")
        data = load(file)
        data["login"] = pick_login(credentials)
        if isinstance(oauth, dict) and oauth.get("accountUuid") == dig(data, "oauthAccount", "accountUuid"):
            data["oauthAccount"] = oauth
        save(file, data)
        left = number(dig(data, "login", "claudeAiOauth", "expiresAt")) - now_ms()
        print(f"{account.label}: refreshed, access token good for {hours(left)}")
    shutil.rmtree(stub, ignore_errors=True)


def refresh_account(file, force):
    """Refreshes FILE in a folder of its own."""
    global DEAD, STUB
    account = read_account(file)
    hint = login_hint(account.label)
    if is_dead(account.data):
        print(f"{account.label}: signed out, sign in again: {hint}")
        notify_once(f"dead:{file.name}", f"Claude account {account.label} is signed out", f"Sign in again: {hint}")
        DEAD = True
        return
    now = now_ms()
    ends = number(dig(account.data, "login", "claudeAiOauth", "refreshTokenExpiresAt"))
    if 0 < ends < now:
        print(f"{account.label}: the sign-in ended on {account.ends}, sign in again: {hint}")
        DEAD = True
        return
    expires = number(dig(account.data, "login", "claudeAiOauth", "expiresAt"))
    if not force and expires - now > REFRESH_AHEAD_MS:
        print(f"{account.label}: access token good for {hours(expires - now)}, nothing to do")
        return
    STUB = pathlib.Path(tempfile.mkdtemp(prefix=".refresh.", dir=STORE))
    write_private(STUB / ".account", f"{file.name}\n")
    write_private(STUB / ".base", f"{fingerprint(file, True)}\n")
    # An access token in the past makes claude refresh it first thing.
    login = pick_login(dig(account.data, "login") or {})
    login["claudeAiOauth"] = dict(dig(login, "claudeAiOauth") or {}, expiresAt=now - 60000)
    write_private(STUB / ".credentials.json", dumps(login))
    write_private(STUB / ".claude.json", dumps({"oauthAccount": dig(account.data, "oauthAccount"),
                                                "hasCompletedOnboarding": True}))
    # claude auth status refreshes an expired login without a model request. The cheapest model is the fallback,
    # for a version where it no longer does.
    stub_claude(STUB, CLAUDE_SECONDS, "auth", "status")
    if not stub_moved(STUB):
        stub_claude(STUB, CLAUDE_SECONDS, "-p", "--model", "haiku", "--tools", "", "--strict-mcp-config",
                    "--mcp-config", '{"mcpServers":{}}', "--setting-sources", "local", "--no-session-persistence",
                    "Reply with exactly: OK")
    finish_stub(STUB)
    STUB = None


def days_to(ms):
    """When the time MS comes, in days of the calendar here: today, tomorrow or in N days."""
    days = (datetime.date.fromtimestamp(number(ms) // 1000) - datetime.date.today()).days
    return {0: "today", 1: "tomorrow"}.get(days, f"in {days} days")


def signin_reminder(file, ahead):
    """What to do about the sign-in of FILE when it's over or ends within AHEAD ms, None otherwise."""
    account = read_account(file)
    ends = number(dig(account.data, "login", "claudeAiOauth", "refreshTokenExpiresAt"))
    now = now_ms()
    hint = login_hint(account.label)
    if is_dead(account.data):
        return f"{account.label} is signed out. Sign in again: {hint}"
    if ends <= 0 or ends - now >= ahead:
        return None
    if ends <= now:
        return f"The sign-in of {account.label} ended on {account.ends}. Sign in again: {hint}"
    return f"The sign-in of {account.label} ends on {account.ends}, {days_to(ends)}. Sign in again before then: {hint}"


def remind_signins():
    """Only a new sign-in moves the date a sign-in ends, so that date is reminded of ahead of time."""
    for file in accounts():
        text = signin_reminder(file, REMIND_AHEAD_MS)
        if text:
            warn(text)


def check_deadline(file):
    """The same on the desktop, closer to the date. A refused login is announced by its refresh."""
    if is_dead(load(file)):
        return
    text = signin_reminder(file, WARN_AHEAD_MS)
    if text:
        print(text)
        notify_once(f"ends:{file.name}", f"Claude sign-in of {read_account(file).label}", text)


def recover_refreshes():
    """A refresh killed once claude rotated a login leaves the new one in its folder, and the store's copy is used
    up. Every command brings it back first, a switch included."""
    for stub in sorted(STORE.glob(".refresh.*")):
        if stub.is_dir():
            finish_stub(stub)


def tidy_signins():
    """Sign-in folders that outlived their sign-in, a killed or abandoned login: a finished one newer than the stored
    login of its account is kept, the rest goes. One younger than an hour may still be running."""
    for folder in sorted(STORE.glob(".signin.*")):
        if not folder.is_dir():
            continue
        age = time.time() - folder.stat().st_mtime
        if age <= 3600:
            continue
        credentials = load_or_none(folder / ".credentials.json")
        file = account_file(folder / ".claude.json") if signed_in(credentials) else None
        if file is not None:
            ends = number(dig(credentials, "claudeAiOauth", "refreshTokenExpiresAt"))
            stored = number(dig(load_or_none(file), "login", "claudeAiOauth", "refreshTokenExpiresAt"))
            if ends > stored:
                store_account(folder / ".credentials.json", folder / ".claude.json", file)
                if file == live_file():
                    lock_live()
                    apply_account(file)
                    release_locks()
                print(f"{read_account(file).label}: kept the sign-in an interrupted login left behind")
            shutil.rmtree(folder, ignore_errors=True)
        elif age > 86400:
            shutil.rmtree(folder, ignore_errors=True)


def cmd_refresh(arguments):
    """Refreshes the login of every account that is not live, and announces the sign-ins that end soon."""
    first = arguments[0] if arguments else ""
    if first not in ("", "--force"):
        die("Usage: claude-switch refresh [--force]")
    lock_store()
    recover_refreshes()
    tidy_signins()
    unlock_store()
    for file in accounts():
        lock_store()
        # The live login belongs to the running sessions, its stored copy is older than theirs.
        if file != live_file():
            refresh_account(file, first == "--force")
        check_deadline(file)
        unlock_store()
    return 1 if DEAD else 0


def cmd_list():
    live = live_file()
    print(f"  {'ACCOUNT':<28} {'EMAIL':<36} {'PLAN':<6} UNTIL")
    for file in accounts():
        account = read_account(file)
        marker = "*" if file == live else " "
        print(f"{marker} {account.label:<28} {account.email:<36} {account.plan:<6} {account.ends}")
    remind_signins()


def is_row(text, rows):
    """True when TEXT is the number of a row."""
    return bool(re.fullmatch(r"[1-9][0-9]{0,2}", text)) and int(text) <= rows


def prompt_rename(files):
    row = "1"
    try:
        if len(files) > 1:
            row = input("Rename which one (number)? ")
            if not row:
                return
            if not is_row(row, len(files)):
                print(f"There is no account {row}.")
                return
        new = input("New name (Enter to keep it): ")
    except EOFError:
        return
    if not new.strip():
        return
    try:
        lock_store()
        rename_account(files[int(row) - 1], new)
    except SystemExit as error:
        if error.code == 143:
            raise
    finally:
        unlock_store()


def cmd_prompt():
    while True:
        lock_store()
        save_live()
        unlock_store()
        live = live_file()
        files = accounts()
        print("\nClaude Code accounts\n")
        for row, file in enumerate(files, 1):
            account = read_account(file)
            shown = account.label if account.label == account.email else f"{account.label}  {account.email}"
            when = account.ends if account.ends == "signed out" else f"until {account.ends}"
            here = "active" if file == live else ""
            print(f"  {row:2d}) {shown:<56} {account.plan:<6} {when:<17} {here}")
        remind_signins()
        print("\n   a) Add an account\n   r) Rename an account\n   q) Quit\n")
        try:
            choice = input("Select an account to switch to: ")
        except EOFError:
            print()
            return
        if choice in ("q", "Q"):
            return
        if choice in ("a", "A"):
            run([sys.executable, "-I", str(pathlib.Path(__file__).resolve()), "login"])
        elif choice in ("r", "R"):
            prompt_rename(files)
        elif is_row(choice, len(files)):
            lock_store()
            switch_to(files[int(choice) - 1])
            return
        else:
            print("Type a number from the list, a, r or q.")


def main(arguments):
    first = arguments[0] if arguments else ""
    if first in ("help", "-h", "--help"):
        print(USAGE, end="")
        return 0
    if first.startswith("-"):
        print(USAGE, end="", file=sys.stderr)
        return 2
    if not shutil.which("claude"):
        die("claude is not in the PATH.")
    lock_store()
    if credential_manager():
        if first != "list":
            die(f"{CREDMAN} Nothing changed.")
        warn(CREDMAN)
        cmd_list()
        return 0
    recover_refreshes()
    repair_live()
    save_live()
    if first == "":
        if terminal():
            unlock_store()
            cmd_prompt()
        else:
            cmd_list()
    elif first == "list":
        cmd_list()
    elif first == "login":
        unlock_store()
        cmd_login(arguments[1:])
    elif first == "refresh":
        unlock_store()
        return cmd_refresh(arguments[1:])
    elif first == "rename":
        if len(arguments) != 3:
            die("Usage: claude-switch rename NAME NEW_NAME")
        rename_account(require_account(arguments[1]), arguments[2])
    else:
        if len(arguments) != 1:
            die("Usage: claude-switch NAME")
        switch_to(require_account(first))
        remind_signins()
    return 0


def cleanup():
    """Keeps a login already rotated, frees the locks, drops what a cut run left, and puts claude.ai in Chrome back
    on the live account when a sign-in took it away."""
    global STUB, SIGNIN
    if STUB is not None:
        with contextlib.suppress(Exception):
            finish_stub(STUB)
        STUB = None
    release_locks()
    if SIGNIN is not None:
        shutil.rmtree(SIGNIN, ignore_errors=True)
        SIGNIN = None
    for path in (f"{CREDENTIALS}.switch", f"{CONFIG}.switch"):
        with contextlib.suppress(OSError):
            os.remove(path)
    if WEB_AWAY:
        with contextlib.suppress(Exception):
            web_back()
    unlock_store()


def setup_output():
    """Line by line, so these messages and those of the helpers come in order. A run without a console, the task
    started through pyw.exe, writes to ~/.claude-accounts/.task.log instead."""
    if sys.stdout is None or sys.stderr is None:
        make_store()
        log = STORE / ".task.log"
        with contextlib.suppress(OSError):
            if log.stat().st_size > 1_000_000:
                replace(log, STORE / ".task.log.old")
        sys.stdout = sys.stderr = open(log, "a", encoding="utf-8")
        print(f"--- {datetime.datetime.now():%d/%m/%Y %H:%M:%S} {' '.join(sys.argv[1:])}")
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(line_buffering=True, errors="replace")


if __name__ == "__main__":
    os.umask(0o077)
    setup_output()
    for stop_signal in STOPS:
        signal.signal(stop_signal, stop)
    status = 1
    try:
        status = main(sys.argv[1:])
    except (OSError, ValueError) as error:
        # Where a file can't be read or written, nothing changed: the live files are only ever renamed into place.
        print(f"claude-switch: {error}", file=sys.stderr)
    finally:
        cleanup()
    sys.exit(status)
