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

With claude-switch auto on, the machine goes to the best account with room by itself: the claude-switch-watch task
checks every 5 minutes while a session is open, and a StopFailure hook in ~/.claude/settings.json moves the machine as
soon as a session meets a limit, then wakes that session. usage.py in ~/.local/share/claude-switch reads what each
account has left and ranks them.
"""
import contextlib
import datetime
import getpass
import hashlib
import json
import os
import pathlib
import re
import shlex
import shutil
import signal
import stat
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
SETTINGS = HOME / ".claude" / "settings.json"
STORE = HOME / ".claude-accounts"
SHARE = HOME / ".local" / "share" / "claude-switch"
CHROME_HOST = SHARE / "chrome-host.py"
DESKTOP_HELPER = SHARE / "desktop.py"
NOTIFY = SHARE / "notify.ps1"
USAGE_HELPER = SHARE / "usage.py"
SELF = pathlib.Path(__file__).resolve()
# When this run started: a hook tells by it whether its session failed before the last switch.
STARTED = int(time.time() * 1000)

# Automatic switching is on while this file is there.
AUTO_FLAG = STORE / ".auto"
# The account a switch typed by hand holds while automatic switching is on, until it runs out.
HOLD = STORE / ".hold"
# The order the kinds of account rank in, see claude-switch auto order, and the one with no such file.
ORDER_FILE = STORE / ".auto-order"
DEFAULT_ORDER = ("ultra", "key", "max")
# When the last switch happened, in milliseconds.
SWITCHED = STORE / ".switched"
# When each session was woken, one file per session.
WOKEN = STORE / ".woken"
# What the automatic switches did and what claude-switch said meanwhile: a hook's stderr goes to Claude.
AUTO_LOG = STORE / ".auto.log"
# What runs the watch: its systemd timer elsewhere, the scheduled task install.ps1 registers on Windows.
WATCH_TIMER = "claude-switch-watch.timer"
WATCH_TASK = "claude-switch-watch"
# What Claude Code passes to the StopFailure hook of claude-switch. A budget comes back as unknown.
HOOK_MATCHER = "rate_limit|billing_error|authentication_failed|unknown"
# The words a gateway answers with when a key spent its budget, which Claude Code reports as unknown.
BUDGET_WORDS = re.compile(r"budget|credit balance|insufficient_quota|spend limit")

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

COMMANDS = ("list", "login", "key", "rename", "refresh", "help", "auto", "watch", "hook")

# A gateway key account is the set of these entries in the env of ~/.claude/settings.json: a token there goes before the
# claude.ai login, and the model names and the URL belong to the gateway. A switch replaces all of them, the other
# entries of env stay.
KEY_ENV = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL",
           "ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
           "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_CUSTOM_HEADERS")

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
# The files this run is writing aside, which only it may remove.
WRITING = []
SIGNIN = None
STUB = None
DEAD = False
# Set while claude.ai in Chrome is on the account of a sign-in rather than on the live one.
WEB_AWAY = False
# The open file that holds the store lock.
STORE_LOCK = None
# Set while claude-switch switches on its own: Desktop restarts without a question.
AUTO = False
# Set when Claude Desktop stayed on its account because it runs this, one of its Code sessions for instance.
DESKTOP_KEPT = False
# The account the last route went to, and why, for the hook.
ROUTED_TO = ""
ROUTED_WHY = ""
# What each account has left, by file, for list and the prompt.
LEFT_OF = {}
# ~/.claude-accounts/.auto.log while what claude-switch and its helpers say goes there, and where a hook's last
# sentence goes, to Claude.
LOGGING = None
HOOK_ERR = None

USAGE = """\
Usage:
  claude-switch                          Prompt to switch, add or rename.
  claude-switch NAME                     Switch the whole machine to NAME, a name or an email.
  claude-switch list                     Print the accounts and what each has left. * marks the active one.
  claude-switch login [ACCOUNT] [--switch] [OPTIONS]
                                         Sign in an account and keep it here. The machine stays on its
                                         account, and claude.ai in Chrome goes back to it when it can,
                                         unless --switch puts the machine on the new one. With ACCOUNT,
                                         a name or an email already here, it renews that one: claude.ai
                                         in Chrome goes to it first and the sign-in page asks for its
                                         email. OPTIONS go to claude auth login.
  claude-switch key import NAME          Keep the gateway key now in ~/.claude/settings.json as the
                                         account NAME: its token, URL and model names.
  claude-switch key add NAME [--switch] [--like KEY] [--api-key] [--base-url URL] [--env VAR=VALUE]...
                                         Keep a gateway key as the account NAME, and switch to it with
                                         --switch. The token is asked without echo, or read from the
                                         standard input. --like takes the URL and model names of KEY,
                                         --api-key sends the token as an Anthropic API key, x-api-key,
                                         rather than a bearer token.
  claude-switch key renew NAME           Give the gateway key NAME a new token, asked the same way.
  claude-switch key remove NAME          Forget the gateway key NAME, and take it out of
                                         ~/.claude/settings.json when it is active.
  claude-switch rename NAME NEW_NAME     Rename an account.
  claude-switch refresh [--force]        Refresh the login of the accounts that are not active.
                                         The claude-switch-refresh task runs it at logon and every
                                         4 hours. --force refreshes them even when not due.
  claude-switch auto [on|off]            Turn automatic switching on or off, or say how it stands.
                                         On, the machine goes to the best account with room: every 5
                                         minutes while a session is open, and as soon as a session
                                         meets a limit, which then carries on by itself.
  claude-switch auto order [KIND...]     Say which kind of account comes first, or print the order. A
                                         KIND is ultra, a subscription with no 5-hour limit, max, Max
                                         and the other plans, or key, an API or gateway key. Those left
                                         out follow in the order ultra, key, max.
  claude-switch help                     Print this help, as -h and --help do.

A switch changes the account only, wherever you call it. New sessions start on it and running
sessions move to it at their next request. claude.ai in Chrome follows it too, once the companion
extension in ~/.local/share/claude-switch/chrome-extension is loaded in Chrome. Claude Desktop
follows it as well: an open Desktop is restarted when you answer yes in a terminal. A Remote Control
server (claude remote-control) is stopped when you answer yes, so what keeps it running starts it
again on the new account.

A switch to a gateway key puts its entries in the env of ~/.claude/settings.json, a switch to a
claude.ai account empties them again, which running sessions follow too. claude.ai in Chrome and
Claude Desktop stay on their account.

A sign-in lasts about 28 days, and only a new sign-in moves its end. From 7 days before, list, the
prompt and a switch say when it ends, with the claude-switch login that renews it.

The best account with room is an Ultra subscription first, then a key, then another plan, unless
claude-switch auto order says otherwise: only the weekly limit counts. Between accounts of the same
kind, the one the machine is on stays until it runs out. An account that stops a session waits until
its limit resets. A switch typed by hand stays while automatic switching is on, until that account
runs out.

Adding an account doesn't switch to it, unless with --switch. With automatic switching on, the new
account ranks with the others: added without --switch, the machine goes to it at once when it comes
first, and added with --switch, the next check moves the machine on when another account comes first.

Don't use /logout on an account you want to keep: it revokes the login saved here too.
To forget an account, remove its file in ~/.claude-accounts, or a gateway key with claude-switch key
remove NAME.
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
    return next((other for other in accounts()
                 if other != file and not is_key(other) and fingerprint(other, True) == live), None)


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


# Max 20x is called Ultra since 30/10/2026, and Max 5x Max (Claude Help Center, "What is the Ultra plan?"). Claude Code
# still says max for both, and tells Ultra by this rate limit tier (read in the 2.1.302 binary).
ULTRA_TIER = "default_claude_max_20x"


def plan_of(data, login):
    """The plan of an account, as claude.ai names it. Claude Code fetches the profile of the account again at its
    start, while a token refresh leaves the plan of the login as it was, so the profile comes first."""
    organization = dig(data, "oauthAccount", "organizationType")
    plan = organization[len("claude_"):] if isinstance(organization, str) and organization.startswith("claude_") else ""
    plan = plan or alt(dig(login, "subscriptionType"), "-")
    tier = alt(dig(data, "oauthAccount", "organizationRateLimitTier"), dig(login, "rateLimitTier"))
    return "ultra" if plan == "max" and tier == ULTRA_TIER else str(plan)


def read_account(file):
    """name, email, plan, ends (the date the sign-in ends or "signed out") and label of an account file. A gateway key
    shows its URL as the email and key as the plan."""
    data = load(file)
    login = dig(data, "login", "claudeAiOauth")
    name = dig(data, "name")
    name = name if isinstance(name, str) else ""
    if dig(data, "kind") == "key":
        url = alt(dig(data, "env", "ANTHROPIC_BASE_URL"), "gateway key")
        url = url if isinstance(url, str) else json.dumps(url)
        return types.SimpleNamespace(data=data, name=name, email=url, plan="key", ends="-", label=name or url)
    email = dig(data, "oauthAccount", "emailAddress")
    email = email if isinstance(email, str) else ""
    ends = "signed out" if is_dead(data) else local_date(alt(dig(login, "refreshTokenExpiresAt"), 0))
    return types.SimpleNamespace(data=data, name=name, email=email, plan=plan_of(data, login), ends=ends,
                                 label=name or email)


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


def is_key(file):
    """True when FILE is a gateway key account."""
    return dig(load_or_none(file), "kind") == "key"


def filled(value):
    """jq's VALUE | length > 0: null and false are empty, a number counts by its size."""
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return abs(value) > 0
    return len(value) > 0


def key_set(env):
    """The gateway entries of ENV that hold a value. claude takes an empty one as unset, and a switch leaves "" in place
    of an entry it takes out: a running session takes in an entry that changes, but keeps one that is gone."""
    if not isinstance(env, dict):
        return {}
    return {key: value for key, value in env.items() if key in KEY_ENV and value != ""}


def key_token(entries):
    """After key_set: the token, which claude takes before the claude.ai login."""
    return alt(entries.get("ANTHROPIC_AUTH_TOKEN"), alt(entries.get("ANTHROPIC_API_KEY"), ""))


def key_credential(entries):
    """After key_set: what a gateway knows its key by, the token or else the headers of a gateway that takes the key
    there."""
    return alt(entries.get("ANTHROPIC_AUTH_TOKEN"),
               alt(entries.get("ANTHROPIC_API_KEY"), alt(entries.get("ANTHROPIC_CUSTOM_HEADERS"), "")))


def settings_data():
    """settings.json, {} when it is missing or empty, None when it can't be read as a JSON object."""
    try:
        if not SETTINGS.exists() or SETTINGS.stat().st_size == 0:
            return {}
        data = load(SETTINGS)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def settings_env():
    """The env of settings.json, {} when there is none to read."""
    env = alt(dig(settings_data(), "env"), {})
    return env if isinstance(env, dict) else {}


def key_in_settings(credential=False):
    """True when the env of settings.json holds a gateway token, or with CREDENTIAL what a gateway knows its key by.
    settings.json may be missing or empty."""
    entries = key_set(settings_env())
    return filled(key_credential(entries) if credential else key_token(entries))


def live_key():
    """The key account whose gateway entries are those in the env of settings.json, as (STATUS, FILE). A key is known by
    its credential, then by its URL, then by the rest, so entries changed by hand still name it, see save_live_key.
    STATUS is 0 with the file of that key, 1 when no key is there, 2 when the token there is kept by no account, or by
    several that can't be told apart. Headers without a token are 1: claude takes no key from them before the login, and
    a proxy in front of claude.ai may want headers too. Tokens are compared here, never printed."""
    if not key_in_settings(credential=True):
        return 1, None
    live = key_set(settings_env())
    best, found = 0, []
    for file in accounts():
        data = load_or_none(file)
        if dig(data, "kind") != "key":
            continue
        key = key_set(dig(data, "env"))
        if key_credential(key) != key_credential(live):
            continue
        score = 3 if key == live else 2 if key.get("ANTHROPIC_BASE_URL") == live.get("ANTHROPIC_BASE_URL") else 1
        if score > best:
            best, found = score, [file]
        elif score == best:
            found.append(file)
    if len(found) == 1:
        return 0, found[0]
    return (2, None) if key_in_settings() else (1, None)


def save_live_key():
    """Gateway entries changed by hand in settings.json belong to the active key, as a rotated login belongs to its
    account, so they're saved to it before anything replaces them."""
    status, file = live_key()
    if status:
        return
    data = load(file)
    live = key_set(settings_env())
    if key_set(dig(data, "env")) != live:
        data["env"] = live
        save(file, data)


def active_file():
    """The account the next session starts on: a gateway key before the claude.ai login, as claude chooses, and None when
    that key is kept by no account."""
    status, file = live_key()
    if status == 0:
        return file
    return live_file() if status == 1 else None


def require_kept_key():
    """A switch replaces the token in settings.json, so one no account keeps stops it."""
    if live_key()[0] == 2:
        die("~/.claude/settings.json holds a gateway key no account keeps, nothing changed. Keep it with: "
            "claude-switch key import NAME. To forget it, remove it after that with: claude-switch key remove NAME")


def settings_target():
    """settings.json itself, behind the symbolic link a dotfiles manager may keep in its place."""
    return pathlib.Path(os.path.realpath(SETTINGS))


def folder_writable(folder):
    """True when this user can make a file in FOLDER. Windows ignores the read-only attribute of a folder, and an ACL that
    denies writing shows only when a file is made, so one is made there and removed. Not with tempfile: it takes the
    access denied of Windows for a name already used and tries other names without end."""
    if not WINDOWS:
        return os.access(folder, os.W_OK)
    for _ in range(3):
        probe = pathlib.Path(folder) / f".claude-switch.{os.getpid()}.{os.urandom(6).hex()}"
        try:
            os.close(os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        except FileExistsError:
            continue
        except OSError:
            return False
        with contextlib.suppress(OSError):
            os.remove(probe)
        return True
    return False


def require_writable_settings():
    """Stops a switch before anything changes when settings.json can't be written aside, in a read-only store a dotfiles
    manager links it to for instance. Windows can't rename a file over one marked read-only either."""
    target = settings_target()
    if not folder_writable(target.parent):
        die(f"~/.claude/settings.json is {target}, in a folder this user can't write, nothing changed.")
    if WINDOWS and target.is_file() and not os.access(target, os.W_OK):
        die(f"~/.claude/settings.json is {target}, which is read-only, nothing changed. Clear that with: "
            f"attrib -r {quote(str(target))}")


def kept_owner(target, path):
    """Gives PATH the mode, owner and group of TARGET, False when this user can't. Windows has none of them: a file
    renamed into place keeps the ACL it was made with, which is its folder's, as .claude.json gets at a switch."""
    if WINDOWS:
        return True
    status = target.stat()
    try:
        os.chmod(path, stat.S_IMODE(status.st_mode))
        os.chown(path, status.st_uid, status.st_gid)
    except OSError:
        return False
    return True


def settings_to_change():
    """settings.json as a switch or auto finds it, once this user may write it there: {} when it's missing or empty,
    None when it can't be read as JSON."""
    require_writable_settings()
    target = settings_target()
    try:
        return load(target) if target.is_file() and target.stat().st_size > 0 else {}
    except (OSError, ValueError):
        return None


def write_settings(settings):
    """SETTINGS in settings.json, the rest of what was read there kept. It's written aside and renamed, since running
    sessions read it at any moment, with the mode, owner and group it had."""
    target = settings_target()
    aside = pathlib.Path(f"{target}.switch")
    WRITING[:] = [aside]
    write_private(aside, dumps(settings))
    if not target.exists() or kept_owner(target, aside):
        replace(aside, target)
    else:
        # An owner or a group this user can't give a file of its own: written in place, which keeps them.
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(aside.read_text(encoding="utf-8"))
        aside.unlink(missing_ok=True)
    WRITING.clear()


def set_key_env(file=None):
    """Puts the entries of the key account FILE in the env of settings.json in place of the gateway entries there, or
    only empties those without FILE: an entry taken out stays as "", which claude takes as unset, so running sessions
    leave it too. The rest of the file stays, and a missing or empty one is taken as {}. The token never goes on a
    command line. A session keeps the ANTHROPIC_MODEL it started on, which is said."""
    settings = settings_to_change()
    current = alt(dig(settings, "env"), {}) if isinstance(settings, dict) else None
    if not isinstance(current, dict):
        die("~/.claude/settings.json can't be read as JSON, nothing changed.")
    model = key_set(current).get("ANTHROPIC_MODEL")
    account = alt(dig(load(file), "env"), {}) if file is not None else {}
    account = account if isinstance(account, dict) else {}
    env = {key: "" if key in KEY_ENV else value for key, value in current.items()}
    env.update(account)
    if env:
        settings["env"] = env
    else:
        settings.pop("env", None)
    write_settings(settings)
    if model and model != key_set(account).get("ANTHROPIC_MODEL"):
        warn(f"A running session that started on the model {model} keeps it: /model in it picks another.")


def switch_to_key(file, account):
    require_kept_key()
    if live_key()[1] == file:
        print(f"Already on {account.label}.")
        return
    set_key_env(file)
    note_switch()
    warn_environment(file)
    remind_overrides(file)
    print(f"Switched to {account.label}, a gateway key. claude.ai in Chrome and Claude Desktop stay on their account.")


def write_text(path, text):
    """TEXT in PATH, written aside first and renamed after, so a run that reads it never finds it half written."""
    temporary = pathlib.Path(f"{path}.{os.getpid()}.tmp")
    write_private(temporary, text)
    replace(temporary, path)


def note_switch():
    """When the last switch happened, for automatic switching: a session that failed before it only has to send its
    request again."""
    if AUTO_FLAG.exists():
        write_text(SWITCHED, f"{now_ms()}\n")


def key_usage():
    die("Usage: claude-switch key import NAME, key add NAME [--switch] [--like KEY] [--api-key] [--base-url URL] "
        "[--env VAR=VALUE]..., key renew NAME or key remove NAME")


def read_token(label):
    """A token asked without echo, or read from the standard input. The store is unlocked while someone types it. Windows
    PowerShell can put a BOM before what it pipes, which is no part of a token."""
    unlock_store()
    token = ""
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            # TODO(windows): type a key at this prompt in Windows Terminal, with PowerShell and with cmd.
            token = getpass.getpass(f"Gateway key for {label}: ")
        elif sys.stdin is not None:
            token = sys.stdin.buffer.readline().decode("utf-8-sig", errors="replace")
    except EOFError:
        pass
    except KeyboardInterrupt:
        sys.exit(143)
    lock_store()
    token = token.strip()
    if not token:
        die("No key given, nothing changed.")
    return token


def key_twin(data, replaced=None):
    """The key account with the same gateway entries as DATA, other than REPLACED: no switch could tell them apart."""
    entries = key_set(dig(data, "env"))
    for other in accounts():
        found = load_or_none(other)
        if other != replaced and dig(found, "kind") == "key" and key_set(dig(found, "env")) == entries:
            return other
    return None


def store_key(file, data):
    """Puts DATA in the key account FILE, unless another account is the same key."""
    twin = key_twin(data, file)
    if twin is not None:
        die(f"The same gateway key is already kept as {read_account(twin).label}, nothing changed.")
    make_store()
    save(file, data)


def key_file(name):
    return STORE / ("key_" + re.sub(r"[^\w@.-]", "_", name) + ".json")


def key_new(action, name, options):
    """Keeps a gateway key as the account NAME: the one in settings.json (import), or a token asked without echo or read
    from the standard input (add)."""
    problem = name_problem(name)
    if problem is not None:
        die(problem)
    file = key_file(name)
    if file.exists():
        die(f"Another account is kept as {file.name}, choose another name.")
    switch = False
    if action == "import":
        if options:
            key_usage()
        if not key_in_settings(credential=True):
            die("The env of ~/.claude/settings.json holds no gateway key.")
        status, held = live_key()
        if status == 0:
            die(f"The gateway key in ~/.claude/settings.json is already kept as {read_account(held).label}.")
        data = {"kind": "key", "name": name, "env": key_set(settings_env())}
    elif action == "add":
        like = like_name = None
        base, api, extra = "", False, []
        while options:
            if options[0] == "--api-key":
                api = True
                options = options[1:]
                continue
            if options[0] == "--switch":
                switch = True
                options = options[1:]
                continue
            if len(options) < 2:
                key_usage()
            option, value = options[:2]
            if option == "--like":
                like, like_name = require_account(value), value
                if not is_key(like):
                    die(f"{value} is a claude.ai account, --like takes a gateway key.")
            elif option == "--base-url":
                base = value
            elif option == "--env":
                var, equals, setting = value.partition("=")
                if not equals or not setting:
                    die("--env takes VAR=VALUE.")
                if var in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
                    die("The token is asked, never given on the command line.")
                if var not in KEY_ENV:
                    die(f"{var} is not an entry of a gateway key: {', '.join(KEY_ENV)}")
                extra.append((var, setting))
            else:
                key_usage()
            options = options[2:]
        token = read_token(name)
        # The store was unlocked while the token was typed.
        problem = name_problem(name)
        if problem is not None:
            die(problem)
        if file.exists():
            die(f"Another account is kept as {file.name}, choose another name.")
        if like is not None and not like.is_file():
            die(f"{like_name} is gone, nothing changed.")
        like_env = key_set(dig(load(like), "env")) if like is not None else {}
        # The token goes in the variable of the key it is like: a gateway that wants x-api-key refuses a bearer token.
        var = ("ANTHROPIC_API_KEY" if api or (like_env.get("ANTHROPIC_API_KEY") is not None
                                              and like_env.get("ANTHROPIC_AUTH_TOKEN") is None)
               else "ANTHROPIC_AUTH_TOKEN")
        env = {key: value for key, value in like_env.items() if key not in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")}
        if base:
            env["ANTHROPIC_BASE_URL"] = base
        env[var] = token
        env.update(extra)
        data = {"kind": "key", "name": name, "env": env}
    else:
        key_usage()
    store_key(file, data)
    after_adding(file, switch, f"Kept the gateway key {name}.")


def key_renew(who):
    """A new token for the gateway key WHO, in the variable its token is in. Its URL and model names stay, and
    settings.json takes the new token too when it is the active key."""
    file = require_account(who)
    if not is_key(file):
        die(f"{who} is a claude.ai account, renew it with: claude-switch login {quote(who)}")
    account = read_account(file)
    if not filled(key_token(key_set(dig(account.data, "env")))):
        die(f"{account.label} has no token, its key is in its headers. Keep new headers with: "
            "claude-switch key import NAME")
    token = read_token(account.label)
    # The store was unlocked while the token was typed.
    if not file.is_file():
        die(f"{account.label} is gone, nothing changed.")
    active = live_key()[1] == file
    data = load(file)
    env = data.get("env") if isinstance(data.get("env"), dict) else {}
    env["ANTHROPIC_AUTH_TOKEN" if key_set(env).get("ANTHROPIC_AUTH_TOKEN") is not None else "ANTHROPIC_API_KEY"] = token
    data["env"] = env
    store_key(file, data)
    forget_refusal(file)
    if active:
        set_key_env(file)
        print(f"Renewed {account.label}. Running sessions take the new token at their next request.")
    else:
        print(f"Renewed the gateway key {account.label}.")


def key_remove(who):
    """Forgets the gateway key WHO. The active key is emptied in settings.json first, as on a switch to the claude.ai
    login."""
    file = require_account(who)
    if not is_key(file):
        die(f"{who} is a claude.ai account. To forget it, remove ~/.claude-accounts/{file.name}")
    account = read_account(file)
    if live_key()[1] == file:
        set_key_env()
        file.unlink(missing_ok=True)
        print(f"Removed the gateway key {account.label}, and emptied its entries in ~/.claude/settings.json.")
    else:
        file.unlink(missing_ok=True)
        print(f"Removed the gateway key {account.label}.")


def cmd_key(arguments):
    """Keeps, renews or forgets a gateway key account."""
    if len(arguments) < 2:
        key_usage()
    action, name, options = arguments[0], arguments[1].strip(), arguments[2:]
    if action in ("import", "add"):
        key_new(action, name, options)
    elif action in ("renew", "remove"):
        if options:
            key_usage()
        if action == "renew":
            key_renew(name)
        else:
            key_remove(name)
    else:
        key_usage()


def forget_refusal(file):
    """A new token or a new sign-in ends the wait a refused account was in."""
    (STORE / ".usage" / f"{pathlib.Path(file).stem}.refused.json").unlink(missing_ok=True)


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
    WRITING[:] = [pathlib.Path(f"{CREDENTIALS}.switch"), pathlib.Path(f"{CONFIG}.switch")]
    write_private(f"{CREDENTIALS}.switch", dumps(credentials))
    write_private(f"{CONFIG}.switch", dumps(config))
    # A stop between the renames would leave the login of one account under the name of the other, so it waits
    # until both are done.
    with stops_deferred():
        replace(f"{CREDENTIALS}.switch", CREDENTIALS)
        replace(f"{CONFIG}.switch", CONFIG)
        WRITING.clear()


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
    """The status of COMMAND, run in this terminal, or with what it says in ~/.claude-accounts/.auto.log while
    claude-switch writes there. Windows hands a program its standard handles, not the files Python writes to, so the
    log is given to it."""
    try:
        if LOGGING is not None:
            LOGGING.flush()
            return subprocess.run(command, stdin=subprocess.DEVNULL, stdout=LOGGING, stderr=subprocess.STDOUT,
                                  creationflags=NO_WINDOW).returncode
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


def web_restore():
    """After a sign-in that added an account and didn't switch: claude.ai in Chrome back on the account Claude Code is
    on, since the browser may have signed in the new one there. Without a session saved for that account the companion
    leaves Chrome as it is, because putting back none would sign Chrome out."""
    live = live_file()
    helper = helper_command(CHROME_HOST)
    account = dig(load_or_none(live), "oauthAccount", "accountUuid") if helper and live is not None else None
    if account and run(helper + ["--back", account]) != 0:
        warn("Put claude.ai in Chrome back on its account by hand this time.")


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
    restarted: on a switch claude-switch makes by itself, and on one typed in a terminal unless it says no. A Desktop
    that runs this, through one of its Code sessions, stays on its account: desktop.py says so with 6, not 3."""
    global DESKTOP_KEPT
    helper = helper_command(DESKTOP_HELPER)
    account = dig(load_or_none(file), "oauthAccount", "accountUuid") if helper else None
    if not account:
        return
    status = run(helper + ["switch", account])
    if status == 6:
        DESKTOP_KEPT = True
    if status != 3:
        return
    answer = "no"
    if AUTO:
        answer = "yes"
    elif terminal():
        answer = ask(f"Claude Desktop is open. Restart it on {label}? Code and Cowork tasks running in it stop. [Y/n] ",
                     60) or "yes"
    if answer not in ("y", "Y", "yes", "Yes", "YES"):
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


def warn_environment(file):
    """The variables of this shell that a claude started from it takes before the account FILE. claude ranks a cloud
    provider, ANTHROPIC_AUTH_TOKEN, ANTHROPIC_API_KEY, then CLAUDE_CODE_OAUTH_TOKEN before the claude.ai login, and what
    settings.json sets, "" too, goes before the shell. CLAUDE_CONFIG_DIR takes another folder altogether."""
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        warn("CLAUDE_CONFIG_DIR is set in this shell, a claude started from here doesn't follow the switch.")
    account = alt(dig(load_or_none(file), "env"), {})
    settings = settings_env()
    for var in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "ANTHROPIC_AUTH_TOKEN",
                "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        # The token of a key, and what claude ranks after it, doesn't count.
        if isinstance(account, dict) and filled(alt(account.get(var), "")):
            break
        if os.environ.get(var, "").lower() in ("", "0", "false", "no", "off"):
            continue
        if var in settings:
            continue
        warn(f"{var} is set in this shell, a claude started from here doesn't follow the switch.")


def as_text(value):
    """jq's tostring: a string as it is, anything else as JSON."""
    return value if isinstance(value, str) else json.dumps(value)


def remind_overrides(file):
    """What settings.json sets that claude takes before the account FILE, ranked as claude ranks them: a cloud provider
    before everything, then a gateway token, then apiKeyHelper, CLAUDE_CODE_OAUTH_TOKEN and a profile named in
    ANTHROPIC_PROFILE, then the claude.ai login. A gateway key that sends only headers ranks with the login."""
    account = read_account(file)
    rank = 7
    if is_key(file):
        rank = 2 if key_token(key_set(dig(account.data, "env"))) != "" else 7
    settings = settings_data()
    variables = alt(dig(settings, "env"), {})
    if settings is None or not isinstance(variables, dict):
        return
    found = [(1, var) for var in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
             if as_text(alt(variables.get(var), "")).lower() not in ("", "0", "false", "no", "off")]
    if alt(settings.get("apiKeyHelper"), "") != "":
        found.append((4, "apiKeyHelper"))
    found += [(at, var) for at, var in ((5, "CLAUDE_CODE_OAUTH_TOKEN"), (6, "ANTHROPIC_PROFILE"))
              if alt(variables.get(var), "") != ""]
    override = next((name for at, name in found if at < rank), None)
    if override:
        warn(f"~/.claude/settings.json sets {override}, which claude uses before {account.label}.")


def switch_to(file):
    account = read_account(file)
    if is_key(file):
        switch_to_key(file, account)
        return
    require_kept_key()
    # The active key goes out of settings.json once the login is in place.
    key = live_key()[1]
    if key is not None:
        require_writable_settings()
    if file == live_file():
        if key is not None:
            set_key_env()
            note_switch()
            warn_environment(file)
            remind_overrides(file)
            print(f"Switched to {account.label}.")
        else:
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
    if key is not None:
        set_key_env()
    note_switch()
    warn_environment(file)
    remind_overrides(file)
    check_login_keys()
    print(f"Switched to {account.label}.")
    web_switch(file)
    desktop_switch(file, account.label)
    remote_control_switch(account.label)


def cmd_login(arguments):
    """Signs in beside the store and not in ~/.claude, so the live account changes only on a switch, the one --switch
    asks for once the account is kept. No session ever runs from that folder, it's gone when this ends."""
    global SIGNIN, WEB_AWAY
    switch = "--switch" in arguments
    arguments = [argument for argument in arguments if argument != "--switch"]
    wanted = wanted_email = None
    if arguments and not arguments[0].startswith("-"):
        wanted = require_account(arguments[0])
        if is_key(wanted):
            die(f"{arguments[0]} is a gateway key, it has no sign-in. Give it a new token with: "
                f"claude-switch key renew {quote(arguments[0])}")
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
    forget_refusal(file)
    account = read_account(file)
    if wanted is not None and file != wanted:
        warn(f"The browser signed in {account.email}, not {wanted_email}. {account.email} is kept, "
             f"{wanted_email} is unchanged.")
    if WEB_AWAY:
        web_back()
    elif not known and not switch:
        web_restore()
    if known:
        if file == live_file():
            lock_live()
            apply_account(file)
            release_locks()
        renewed = f"{account.label} was already here, its login is renewed."
        if switch:
            after_adding(file, True, renewed)
        else:
            print(renewed)
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
    after_adding(file, switch, f"Added {account.label}.")


def after_adding(file, switch, done):
    """DONE says the account FILE was kept. With SWITCH the machine goes to it now, and nothing holds it there: it's
    no choice by hand, so automatic switching moves the machine on at its next check when another account comes
    first. Without, the machine stays, and automatic switching, when it is on, ranks the new account with the others
    at once: the machine goes to it when it comes first."""
    if switch:
        print(done)
        lock_store()
        switch_to(file)
        if auto_on():
            HOLD.unlink(missing_ok=True)
            print("Automatic switching is on: at its next check it goes to the best account with room, which may be "
                  "another one.")
    elif auto_on():
        print(f"{done} Automatic switching ranks it with the others:")
        lock_store()
        route_and_say()
    else:
        print(f"{done} Switch to it with: claude-switch {quote(read_account(file).label)}")


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


def remind_unkept_key():
    if live_key()[0] == 2:
        warn("~/.claude/settings.json holds a gateway key no account keeps, it goes before the claude.ai login. "
             "Keep it with: claude-switch key import NAME")


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
        # A gateway key has no login to refresh.
        if is_key(file):
            continue
        lock_store()
        # The live login belongs to the running sessions, its stored copy is older than theirs.
        if file != live_file():
            refresh_account(file, first == "--force")
        check_deadline(file)
        unlock_store()
    return 1 if DEAD else 0


def auto_on():
    return AUTO_FLAG.exists()


def hold_account(file):
    """A switch typed by hand holds its account while automatic switching is on, until that account runs out."""
    if auto_on():
        write_text(HOLD, f"{pathlib.Path(file).name}\n")


def held_file():
    """The account a switch typed by hand holds, None when none does or it's gone."""
    try:
        name = HOLD.read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError):
        return None
    file = STORE / name
    return file if name and pathlib.Path(name).name == name and file.is_file() else None


def notify(title, body):
    """A desktop notification of an automatic switch."""
    show_notification(title, body)


def shell_word(text):
    """TEXT as bash's printf %q writes it, which the shell that runs a hook reads back as one word."""
    if not text:
        return "''"
    return "".join(char if re.fullmatch(r"[\w@%+=:,./-]", char, re.ASCII) else "\\" + char for char in text)


def hook_handler():
    """The StopFailure hook of this claude-switch, as auto on writes it. Claude Code runs it in the background when a
    turn ends on a limit, and its exit 2 wakes the session. Elsewhere it's a command line for the shell. On Windows
    Python runs this script with no shell between: the hooks reference says a .cmd can't be started that way, and a
    command line goes to Git Bash or PowerShell, whichever is there. pyw.exe, the launcher the tasks of install.ps1
    use, opens no console window."""
    if not WINDOWS:
        return {"type": "command", "command": f"{shell_word(str(SELF))} hook", "asyncRewake": True, "timeout": 120}
    launcher = shutil.which("pyw.exe")
    if launcher:
        command, arguments = launcher, ["-3", "-I", str(SELF), "hook"]
    else:
        windowless = pathlib.Path(sys.executable).with_name("pythonw.exe")
        command, arguments = str(windowless) if windowless.is_file() else sys.executable, ["-I", str(SELF), "hook"]
    return {"type": "command", "command": command, "args": arguments, "asyncRewake": True, "timeout": 120}


def own_hook(entry):
    """True for a StopFailure entry that runs claude-switch hook, whatever path it was installed at: a command line that
    ends in hook, or a program whose arguments do, with claude-switch in the program or its arguments."""
    hooks = entry.get("hooks") if isinstance(entry, dict) else None
    for hook in hooks if isinstance(hooks, list) else []:
        command = hook.get("command") if isinstance(hook, dict) else None
        arguments = hook.get("args") if isinstance(hook, dict) else None
        if isinstance(command, str) and command.endswith(" hook") and "claude-switch" in command:
            return True
        if isinstance(arguments, list) and arguments[-1:] == ["hook"] \
                and any(isinstance(part, str) and "claude-switch" in part for part in [command, *arguments]):
            return True
    return False


def stop_failures(settings):
    """The StopFailure entries of SETTINGS, [] when there are none."""
    entries = dig(settings, "hooks", "StopFailure")
    return entries if isinstance(entries, list) else []


def hook_in_settings():
    """True when settings.json holds a StopFailure hook of claude-switch."""
    return any(own_hook(entry) for entry in stop_failures(settings_data()))


def hook_current():
    """True when that hook is the one of this claude-switch, as set_auto_hook writes it."""
    wanted = hook_handler()
    for entry in stop_failures(settings_data()):
        hooks = entry.get("hooks") if isinstance(entry, dict) else None
        if isinstance(hooks, list) and len(hooks) == 1 and isinstance(hooks[0], dict) \
                and entry.get("matcher") == HOOK_MATCHER and hooks[0].get("asyncRewake") is True \
                and hooks[0].get("command") == wanted["command"] and hooks[0].get("args") == wanted.get("args"):
            return True
    return False


def set_auto_hook(on):
    """Adds the StopFailure hook of claude-switch to settings.json, or takes it out. Other hooks stay."""
    settings = settings_to_change()
    hooks = alt(dig(settings, "hooks"), {}) if isinstance(settings, dict) else None
    if not isinstance(hooks, dict):
        die("~/.claude/settings.json can't be read as JSON, nothing changed.")
    kept = [entry for entry in stop_failures(settings) if not own_hook(entry)]
    if on:
        hooks["StopFailure"] = kept + [{"matcher": HOOK_MATCHER, "hooks": [hook_handler()]}]
    elif kept:
        hooks["StopFailure"] = kept
    else:
        hooks.pop("StopFailure", None)
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)
    write_settings(settings)


def auto_order():
    """The order the kinds of account rank in, as words: ultra key max by default."""
    with contextlib.suppress(OSError, IndexError):
        order = ORDER_FILE.read_text(encoding="utf-8").splitlines()[0].split()
        if order:
            return order
    return list(DEFAULT_ORDER)


def order_text():
    """The same, as a person reads it."""
    words = {"ultra": "Ultra subscriptions", "key": "API and gateway keys", "max": "Max and the other plans"}
    return ", then ".join(words[kind] for kind in auto_order() if kind in words)


def set_auto_order(kinds):
    """Puts KINDS first, in that order, and the kinds left out after them in the default order."""
    order = []
    for kind in kinds:
        kind = kind.lower()
        kind = "key" if kind == "api" else kind
        if kind not in DEFAULT_ORDER:
            die(f"{kind} is not a kind of account: ultra, max or key.")
        if kind in order:
            die(f"{kind} is twice in the order.")
        order.append(kind)
    order += [kind for kind in DEFAULT_ORDER if kind not in order]
    make_store()
    write_text(ORDER_FILE, " ".join(order) + "\n")


def watch_enable(on):
    """Starts or stops the watch, True when that went: its systemd timer, or on Windows the scheduled task install.ps1
    registers."""
    if WINDOWS:
        command = [shutil.which("schtasks") or "schtasks", "/Change", "/TN", WATCH_TASK, "/ENABLE" if on else "/DISABLE"]
    else:
        command = ["systemctl", "--user", "enable" if on else "disable", "--now", WATCH_TIMER]
    try:
        return subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              creationflags=NO_WINDOW, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def cmd_auto(arguments):
    """Turns automatic switching on or off, says how it stands, or sets the order. [on|off|order [KIND...]]"""
    first = arguments[0] if arguments else ""
    if first != "order" and len(arguments) > 1:
        die("Usage: claude-switch auto [on|off|order [KIND...]]")
    if first == "":
        auto_status()
    elif first == "order":
        kinds = arguments[1:]
        if len(kinds) > 3:
            die("Usage: claude-switch auto order [KIND...], a KIND is ultra, max or key.")
        if not kinds:
            print(f"Automatic switching ranks the accounts in this order: {order_text()}.")
            return
        set_auto_order(kinds)
        print(f"Automatic switching ranks the accounts in this order now: {order_text()}.")
        if auto_on() and not route():
            print("No account has room now.")
    elif first == "on":
        if helper_command(USAGE_HELPER) is None:
            die(f"usage.py is not in ~/.local/share/claude-switch, run {'install.ps1' if WINDOWS else 'install.sh'} "
                "first.")
        if not hook_current():
            set_auto_hook(True)
        make_store()
        write_text(AUTO_FLAG, "")
        HOLD.unlink(missing_ok=True)
        if not watch_enable(True):
            if WINDOWS:
                warn(f"The task {WATCH_TASK} didn't start, so only a limit a session meets switches. install.ps1 "
                     f"registers it, then: schtasks /Change /TN {WATCH_TASK} /ENABLE")
            else:
                warn(f"{WATCH_TIMER} didn't start, so only a limit a session meets switches. Start it with: "
                     f"systemctl --user enable --now {WATCH_TIMER}")
        print("Automatic switching is on: the machine goes to the best account with room, every 5 minutes while a "
              "session is open and as soon as a session meets a limit.")
        route_and_say()
    elif first == "off":
        if hook_in_settings():
            set_auto_hook(False)
        AUTO_FLAG.unlink(missing_ok=True)
        HOLD.unlink(missing_ok=True)
        watch_enable(False)
        print("Automatic switching is off.")
    else:
        die("Usage: claude-switch auto [on|off|order [KIND...]]")


def usage_helper(*arguments):
    """What usage.py says to ARGUMENTS, as (status, output), 127 when it is not installed. Its errors go nowhere."""
    command = helper_command(USAGE_HELPER)
    if command is None:
        return 127, ""
    try:
        result = subprocess.run(command + [str(argument) for argument in arguments], stdin=subprocess.DEVNULL,
                                capture_output=True, creationflags=NO_WINDOW, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return result.returncode, result.stdout.decode("utf-8", "replace")


def best_of(files, hold=None, active=None):
    """The account of FILES the machine should be on, the one HOLD names while it has room, None when none has
    room. ACTIVE, the account the machine is on, goes before the others of its kind while it has room."""
    status, output = usage_helper("best", *(["--hold", hold] if hold is not None else []),
                                  *(["--active", active] if active is not None else []), "--order",
                                  ",".join(auto_order()), *files)
    lines = output.splitlines()
    return pathlib.Path(lines[0]) if status == 0 and lines else None


def auto_status():
    if not auto_on():
        print("Automatic switching is off. Turn it on with: claude-switch auto on")
        return
    print("Automatic switching is on: the machine goes to the best account with room, every 5 minutes while a session "
          "is open and as soon as a session meets a limit.")
    print(f"It ranks the accounts in this order: {order_text()}.")
    if not hook_in_settings():
        warn("~/.claude/settings.json lost the hook that moves a session on: claude-switch auto on puts it back.")
    held = held_file()
    if held is not None:
        print(f"{read_account(held).label} was chosen by hand, it stays until it runs out.")
    files = accounts()
    usage_helper("read", "--timeout", "5", *files)
    best = best_of(files, active=active_file())
    if best is not None:
        print(f"The best account with room now is {read_account(best).label}.")
    else:
        print("No account has room now.")


def nothing_left():
    """Once a day at most: every account is out."""
    status, first = usage_helper("first", *accounts())
    first = first.strip() if status == 0 else ""
    notify_once("nothing", "No Claude account has room", f"The first to have room again: {first}." if first else "")


def trim_log():
    """The log of automatic switching keeps its last 2000 lines once it passes 1 MB. Only before a run opens it: a run
    that writes to it would go on writing to the file the trim replaces."""
    with contextlib.suppress(OSError):
        if AUTO_LOG.stat().st_size > 1048576:
            lines = AUTO_LOG.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            write_text(AUTO_LOG, "".join(lines[-2000:]))


@contextlib.contextmanager
def to_log():
    """What claude-switch and its helpers say goes to ~/.claude-accounts/.auto.log meanwhile, as all a hook says but its
    last sentence does."""
    global LOGGING
    if LOGGING is not None:
        yield
        return
    make_store()
    saved = sys.stdout, sys.stderr
    for stream in saved:
        with contextlib.suppress(Exception):
            stream.flush()
    with open(AUTO_LOG, "a", encoding="utf-8", errors="replace", buffering=1) as log:
        sys.stdout = sys.stderr = LOGGING = log
        try:
            yield
        finally:
            LOGGING = None
            sys.stdout, sys.stderr = saved


def route():
    """Puts the machine on the best account that has room, when the active one isn't that account, and says so.
    ROUTED_TO and ROUTED_WHY tell where it went and why. False when no account has room."""
    global AUTO, DESKTOP_KEPT, ROUTED_TO, ROUTED_WHY
    ROUTED_TO = ROUTED_WHY = ""
    DESKTOP_KEPT = False
    active = active_file()
    files = accounts()
    # Younger than the 5 minutes between two runs of the task, so each run reads.
    usage_helper("read", "--max-age", "240", "--timeout", "10", *files)
    hold = held_file()
    best = best_of(files, hold, active)
    if best is None:
        nothing_left()
        return False
    # A hold ends with its account's room.
    if hold is not None and best != hold:
        HOLD.unlink(missing_ok=True)
    if best == active:
        return True
    ROUTED_TO = read_account(best).label
    if active is None:
        ROUTED_WHY = "no account was active"
    else:
        status, why = usage_helper("why", active)
        if status == 0 and why.strip():
            ROUTED_WHY = f"{read_account(active).label} {why.strip()}"
        else:
            ROUTED_WHY = f"{ROUTED_TO} is the best account with room"
    AUTO = True
    try:
        with to_log():
            switch_to(best)
    finally:
        AUTO = False
    print(f"Switched to {ROUTED_TO}: {ROUTED_WHY}.")
    kept = (f" Claude Desktop stays on its account: close it and run claude-switch {quote(ROUTED_TO)} to bring it "
            "along.") if DESKTOP_KEPT else ""
    notify(f"Switched to {ROUTED_TO}", f"{ROUTED_WHY}.{kept}")
    return True


def route_and_say():
    """route(), and what came of it when the machine stays."""
    if not route():
        print("No account has room now.")
    elif not ROUTED_TO and active_file() is not None:
        active = active_file()
        if held_file() == active:
            print(f"It stays on {read_account(active).label}, chosen by hand, until it runs out.")
        else:
            print(f"It stays on {read_account(active).label}, the best account with room.")


def cmd_watch(arguments):
    """The task runs this every 5 minutes. Without automatic switching or an open session it never gets here."""
    if arguments:
        die("Usage: claude-switch watch")
    trim_log()
    route()


def may_wake(session):
    """A session is woken 3 times in 10 minutes at most, so one every account refuses doesn't loop."""
    if not session:
        return True
    since = now_ms() - 600000
    try:
        times = (WOKEN / session).read_text(encoding="utf-8").split()
    except OSError:
        times = []
    return sum(1 for moment in times if moment.isdigit() and int(moment) > since) < 3


def note_wake(session):
    if not session:
        return
    WOKEN.mkdir(parents=True, exist_ok=True)
    for old in WOKEN.iterdir():
        with contextlib.suppress(OSError):
            if old.is_file() and time.time() - old.stat().st_mtime > 86400:
                old.unlink()
    with open(WOKEN / session, "a", encoding="utf-8") as handle:
        handle.write(f"{now_ms()}\n")


def iso_ms(value):
    """The time an ISO 8601 date names, in milliseconds, None when it names none."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00").replace("z", "+00:00")
    # Python before 3.11 takes 3 or 6 digits of a second only.
    if "." in text:
        head, _, rest = text.partition(".")
        digits = len(rest) - len(rest.lstrip("0123456789"))
        text = f"{head}.{(rest[:digits] + '000000')[:6]}{rest[digits:]}"
    try:
        moment = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return int(moment.timestamp() * 1000)


def tail_lines(path, count):
    """The last COUNT lines of PATH, read from its end."""
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        handle.seek(max(0, handle.tell() - 262144))
        return handle.read().decode("utf-8", "replace").splitlines()[-count:]


def failed_at(transcript):
    """When the request of the session failed, in milliseconds: the API error its transcript ends on, when that one is
    from the last minute, else a moment before this run started."""
    when = None
    with contextlib.suppress(OSError):
        for line in tail_lines(transcript, 50) if transcript else []:
            with contextlib.suppress(ValueError):
                entry = json.loads(line)
                if isinstance(entry, dict) and entry.get("isApiErrorMessage") is True and entry.get("timestamp"):
                    when = entry["timestamp"]
    moment = iso_ms(when)
    return moment if moment is not None and moment > STARTED - 60000 else STARTED - 1500


def cmd_hook(arguments):
    """Claude Code runs this when a turn ends on an API error, in the background, with the error on stdin. When the
    active account met a limit, the machine moves to the best account with room, and exit 2 wakes the session with a
    sentence on stderr for Claude, which sends its request again. claude-switch says the rest in
    ~/.claude-accounts/.auto.log, since Claude reads the hook's stderr."""
    raw = sys.stdin.buffer.read(1048576) if sys.stdin is not None else b""
    if not auto_on():
        return 0
    try:
        data = json.loads(raw.decode("utf-8-sig", "replace"))
    except ValueError:
        data = {}
    data = data if isinstance(data, dict) else {}
    error = data.get("error") if isinstance(data.get("error"), str) else ""
    text = " ".join(value for value in (data.get("last_assistant_message"), data.get("error_details"))
                    if isinstance(value, str))
    if error in ("rate_limit", "billing_error", "authentication_failed"):
        reason = error
    elif error == "unknown":
        # A gateway's budget comes back as an unknown error, only its words tell it.
        if not BUDGET_WORDS.search(text.lower()):
            return 0
        reason = "budget"
    else:
        return 0
    session = re.sub(r"[^A-Za-z0-9-]", "", data.get("session_id") if isinstance(data.get("session_id"), str) else "")
    if not may_wake(session):
        return 0
    active = active_file()
    if active is None:
        return 0
    try:
        switched = int(SWITCHED.read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        switched = 0
    transcript = data.get("transcript_path") if isinstance(data.get("transcript_path"), str) else ""
    if switched > failed_at(transcript):
        # The machine moved after the request failed, by another session's hook or by hand.
        message = f"claude-switch has moved this machine to {read_account(active).label} since the request failed."
    else:
        if usage_helper("refuse", active, reason, text)[0] != 0:
            return 0
        # Nothing has room: Claude Code waits for the limit to reset by itself.
        if not route() or not ROUTED_TO:
            return 0
        message = f"claude-switch moved this machine to {ROUTED_TO}: {ROUTED_WHY}."
    # A session of Claude Desktop goes on with the login of Desktop, which only a restart of Desktop changes, and that
    # restart would end the session: woken, it would only meet the limit again.
    helper = helper_command(DESKTOP_HELPER)
    if helper is not None and run(helper + ["inside"]) == 0:
        print("The session runs in Claude Desktop, which keeps its account until it restarts: it is not woken.")
        return 0
    note_wake(session)
    print(f"{message} The request that failed can go out again now: carry on with the task where it stopped.",
          file=HOOK_ERR or sys.stderr, flush=True)
    return 2


def start_hook_log():
    """Claude reads what the hook says: everything but its last sentence goes to the log."""
    global HOOK_ERR, LOGGING
    make_store()
    trim_log()
    HOOK_ERR = sys.stderr
    LOGGING = open(AUTO_LOG, "a", encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = sys.stderr = LOGGING


def read_left():
    """Brings the readings up to date and fills LEFT_OF with what each account has left."""
    LEFT_OF.clear()
    if helper_command(USAGE_HELPER) is None:
        return
    files = accounts()
    usage_helper("read", "--timeout", "5", *files)
    for line in usage_helper("left", *files)[1].splitlines():
        file, tab, text = line.partition("\t")
        if tab:
            LEFT_OF[pathlib.Path(file)] = text


def cmd_list():
    live = active_file()
    read_left()
    print(f"  {'ACCOUNT':<28} {'EMAIL':<36} {'PLAN':<6} {'UNTIL':<11} LEFT")
    for file in accounts():
        account = read_account(file)
        marker = "*" if file == live else " "
        print(f"{marker} {account.label:<28} {account.email:<36} {account.plan:<6} {account.ends:<11} "
              f"{LEFT_OF.get(file) or '-'}")
    remind_unkept_key()
    if live is not None:
        remind_overrides(live)
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


def prompt_add():
    """Asks whether the new account is a subscription or a key, and whether the machine goes to it once it's in, then
    signs it in or keeps the key. Each runs as a claude-switch of its own, so a refusal ends that run and not the
    prompt."""
    print("\n   s) A subscription, signed in with the browser\n   k) An API key or a gateway key\n")
    try:
        kind = input("Which kind of account? ")
        if kind in ("s", "S"):
            command = ["login"]
        elif kind in ("k", "K"):
            name = input("Name of the key: ").strip()
            if not name:
                return
            problem = name_problem(name)
            if problem is not None:
                print(problem)
                return
            url = input("Gateway URL (Enter for the Anthropic API): ").strip()
            if url and not re.match(r"https?://", url, re.IGNORECASE):
                print("A gateway URL starts with https:// or http://.")
                return
            command = ["key", "add", name] + (["--base-url", url] if url else ["--api-key"])
        else:
            if kind:
                print("Type s or k.")
            return
        if input("Switch to it once it's added? [y/N] ").strip() in ("y", "Y", "yes", "Yes", "YES"):
            command.append("--switch")
    except EOFError:
        return
    run([sys.executable, "-I", str(pathlib.Path(__file__).resolve()), *command])


def cmd_prompt():
    while True:
        lock_store()
        save_live()
        save_live_key()
        unlock_store()
        live = active_file()
        files = accounts()
        read_left()
        print("\nClaude Code accounts\n")
        for row, file in enumerate(files, 1):
            account = read_account(file)
            shown = account.label if account.label == account.email else f"{account.label}  {account.email}"
            when = account.ends if account.ends == "signed out" else f"until {account.ends}"
            if account.plan == "key":
                when = ""
            here = "active" if file == live else ""
            print(f"  {row:2d}) {shown:<56} {account.plan:<6} {when:<17} {LEFT_OF.get(file) or '-':<22} {here}")
        remind_unkept_key()
        if live is not None:
            remind_overrides(live)
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
            prompt_add()
        elif choice in ("r", "R"):
            prompt_rename(files)
        elif is_row(choice, len(files)):
            lock_store()
            switch_to(files[int(choice) - 1])
            hold_account(files[int(choice) - 1])
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
    if first == "watch":
        # The task runs this every 5 minutes: nothing to do without automatic switching or an open session.
        if not auto_on() or usage_helper("sessions")[0] != 0:
            return 0
    elif first == "hook":
        start_hook_log()
    lock_store()
    if credential_manager():
        if first != "list":
            die(f"{CREDMAN} Nothing changed.")
        warn(CREDMAN)
        cmd_list()
        return 0
    # What a switch killed on the way left aside. No other run writes there while the store is locked.
    for path in (f"{CREDENTIALS}.switch", f"{CONFIG}.switch", f"{settings_target()}.switch"):
        with contextlib.suppress(OSError):
            os.remove(path)
    recover_refreshes()
    repair_live()
    save_live()
    save_live_key()
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
    elif first == "key":
        cmd_key(arguments[1:])
    elif first == "rename":
        if len(arguments) != 3:
            die("Usage: claude-switch rename NAME NEW_NAME")
        rename_account(require_account(arguments[1]), arguments[2])
    elif first == "auto":
        cmd_auto(arguments[1:])
    elif first == "watch":
        cmd_watch(arguments[1:])
    elif first == "hook":
        return cmd_hook(arguments[1:])
    else:
        if len(arguments) != 1:
            die("Usage: claude-switch NAME")
        file = require_account(first)
        switch_to(file)
        hold_account(file)
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
    # Another run may be writing its own under the same names, so only what this one was writing.
    for path in WRITING:
        with contextlib.suppress(OSError):
            os.remove(path)
    WRITING.clear()
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
