#!/usr/bin/env -S python3 -I
"""Claude Desktop follows claude-switch. The Windows port of share/desktop.py.

Claude Desktop keeps its sign-in in its data folder, apart from Claude Code: the claude.ai session cookies in
Cookies, and in config.json the tokens of its Code tab (oauth:tokenCacheV2) and the account it saw last
(lastKnownAccountUuid). Those belong to the account, the rest of the folder to the machine. The cookies and the
tokens are encrypted with the one key Desktop keeps for itself, the same for every account, so a sign-in saved here
goes back as it is and is never decrypted.

The folder is ~/.config/Claude here. On Windows the Store build (MSIX) keeps it in
%LOCALAPPDATA%\\Claude-Data\\claude_<package id> since Desktop 3.x (2026-10-22 on the PC), and leaves a
.user-data-left.* file in the folder it used before, its package's copy of %APPDATA%\\Claude under
%LOCALAPPDATA%\\Packages\\Claude_*\\LocalCache\\Roaming\\Claude. An older build keeps %APPDATA%\\Claude itself. Its key is
the one of Local State in that folder, protected by DPAPI for the user, so the saved sign-ins only open on this account
of this PC. Chromium keeps the cookies in Network\\Cookies on Windows, and holds that file for itself alone while
Desktop runs: there the account of an open Desktop is the one its config.json names.

Desktop holds its cookies open and writes config.json as it quits, so it must be closed for a switch.
lastKnownAccountUuid always moves with the rest: when it names the account Desktop finds signed in, Desktop carries
on as before, and otherwise it takes the change for another person and resets connectors and tool permissions (seen
in Desktop 4.4212.0).

On Windows the preferences of the window go with the account too: its web storage (Local Storage, Session Storage,
IndexedDB, WebStorage), which holds the sidebar's sorting and groups, the presets and the drafts. Desktop keeps one
copy for every account and resets part of it when it starts signed out. An account with no copy saved yet keeps the
preferences in place.

The open session is named by its cookies among the saved ones, else by lastKnownAccountUuid. A session nobody can
name is never saved or replaced. The saved sign-ins are in ~/.claude-accounts/.desktop, one file per account (and on
Windows a folder of preferences beside it), and only you can read them.

    desktop.py status            what Desktop is on, as JSON
    desktop.py switch ACCOUNT    save the sign-in under its account, put back the one of ACCOUNT
    desktop.py quit [SECONDS]    close Desktop and wait until it is gone, 20 seconds at most
    desktop.py start             start Desktop, without the environment of the calling session

switch exits 3 when Desktop is open and on another account, 4 when its session has no name, and on Windows 6 when
Desktop is open and runs this switch, through one of its Code sessions or its terminal.
"""
import base64
import contextlib
import json
import os
import pathlib
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time

WINDOWS = os.name == "nt"
if WINDOWS:
    import _winapi
HOME = pathlib.Path.home()


def desktop_folder():
    if not WINDOWS:
        return pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "Claude"
    local = pathlib.Path(os.environ.get("LOCALAPPDATA") or HOME / "AppData" / "Local")
    roaming = pathlib.Path(os.environ.get("APPDATA") or HOME / "AppData" / "Roaming")
    # Claude-Data first, where the Store build keeps its folder since it moved it out of its package (seen on the PC on
    # 2026-10-22 and 2026-11-09). A folder it moved out of keeps only a .user-data-left.* file and is no longer its own.
    candidates = [*sorted(local.glob("Claude-Data/claude_*")),
                  *(package / "LocalCache" / "Roaming" / "Claude" for package in sorted(local.glob("Packages/Claude_*"))),
                  roaming / "Claude"]
    candidates = [folder for folder in candidates if folder.is_dir() and not any(folder.glob(".user-data-left*"))]
    return next((folder for folder in candidates if (folder / "config.json").is_file()),
                candidates[0] if candidates else roaming / "Claude")


DESKTOP = desktop_folder()
COOKIES = DESKTOP / "Network" / "Cookies" if (DESKTOP / "Network" / "Cookies").exists() else DESKTOP / "Cookies"
CONFIG = DESKTOP / "config.json"
STORE = HOME / ".claude-accounts"
SAVED = STORE / ".desktop"
HOSTS = ("claude.ai", ".claude.ai")
# The cookies claude.ai writes at a sign-in, the other names Desktop reads a session from, and the cookie
# claude.ai sets when it registers Desktop for the Remote Control of the account (2026-11-02).
SESSION = ("sessionKey", "sessionKeyLC", "routingHint", "lastActiveOrg", "sessionKeyV2", "sessionKeyV3",
           "sessionKeyV3LC", "__Host-ant_trusted_device")
KEYS = ("sessionKey", "sessionKeyV3")
TOKENS = ("oauth:tokenCacheV2", "oauth:tokenCache")
ACCOUNT = "lastKnownAccountUuid"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
RUNNING, UNNAMED, BROKEN, INSIDE = 3, 4, 5, 6
NO_WINDOW = subprocess.CREATE_NO_WINDOW if WINDOWS else 0
# Windows: the web storage of the window, which holds its preferences.
WEB = ("Local Storage", "Session Storage", "IndexedDB", "WebStorage")
# Windows: how long a quit waits for the helpers that outlive Desktop to let go of its files.
FILES_FREE_SECONDS = 15
# A program of Claude Desktop: the app, or the Claude Code its Code tab runs.
DESKTOP_PROGRAM = re.compile(r"\\WindowsApps\\Claude_|\\AnthropicClaude\\|\\Claude-Data\\[^\\]+\\claude-code\\|"
                             r"\\Packages\\Claude_[^\\]+\\LocalCache\\|\\Roaming\\Claude\\claude-code\\", re.IGNORECASE)
USER_DATA_DIR = re.compile(r'--user-data-dir=(?:"([^"]+)"|(\S+))')


class Refused(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def running():
    """The pid of the Desktop holding the folder, None when none does."""
    if WINDOWS:
        return running_on_windows()
    try:
        holder = os.readlink(DESKTOP / "SingletonLock")
    except OSError:
        return None
    host, _, pid = holder.rpartition("-")
    if host != socket.gethostname():
        # Another computer on a shared home: it can't be checked, so it counts as open.
        return -1
    try:
        program = pathlib.Path(f"/proc/{int(pid)}/cmdline").read_bytes().split(b"\0")[0]
    except (OSError, ValueError):
        return None
    return int(pid) if os.path.basename(program) == b"claude-desktop" else None


def lock_held():
    """Windows: Chromium holds the file lockfile in the folder while Desktop runs, for itself alone, and Windows removes
    it as Desktop ends (seen on the PC on 2026-11-09, Desktop 4.23118.0.0)."""
    try:
        os.close(os.open(DESKTOP / "lockfile", os.O_WRONLY))
    except PermissionError:
        return True
    except OSError:
        return False
    return False


def windows_processes(name=None):
    """The processes here, called NAME when it is given, as dicts of ProcessId, ParentProcessId, ExecutablePath and
    CommandLine. None when Windows doesn't say."""
    query = f"-Filter \"Name = '{name}'\" " if name else ""
    command = ("[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; "
               f"ConvertTo-Json -Compress -InputObject @(Get-CimInstance Win32_Process {query}| "
               "Select-Object ProcessId, ParentProcessId, ExecutablePath, CommandLine)")
    try:
        output = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                                capture_output=True, timeout=60, creationflags=NO_WINDOW).stdout
        processes = json.loads(output.decode("utf-8") or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    if isinstance(processes, dict):
        processes = [processes]
    return [process for process in processes if isinstance(process, dict)] if isinstance(processes, list) else None


def same_folder(one, other):
    return os.path.normcase(os.path.abspath(one)) == os.path.normcase(os.path.abspath(other))


def folder_helpers(processes):
    """The helpers of the Desktop that runs on this folder: the main process names no folder on its command line,
    the helpers it starts name it in --user-data-dir."""
    helpers = []
    for process in processes:
        command = process.get("CommandLine") or ""
        folder = USER_DATA_DIR.search(command)
        if folder and "--type=" in command and same_folder(folder.group(1) or folder.group(2), DESKTOP):
            helpers.append(process)
    return helpers


def running_on_windows():
    """The pid of the Desktop holding the folder: the parent of its helpers. -1 when it holds the folder and no helper
    names it, a Desktop that is just starting for instance: then it counts as open and can't be closed from here."""
    if not lock_held():
        return None
    processes = windows_processes("claude.exe") or []
    pids = {process.get("ProcessId") for process in processes}
    return next((helper["ParentProcessId"] for helper in folder_helpers(processes)
                 if helper.get("ParentProcessId") in pids), -1)


def inside_desktop():
    """Windows: True when Claude Desktop runs this, through one of its Code sessions or its terminal. Closing Desktop
    would then end this run before the switch is done."""
    if not WINDOWS:
        return False
    processes = {process.get("ProcessId"): process for process in windows_processes() or []}
    pid, seen = os.getpid(), set()
    while pid in processes and pid not in seen:
        seen.add(pid)
        if DESKTOP_PROGRAM.search(processes[pid].get("ExecutablePath") or ""):
            return True
        pid = processes[pid].get("ParentProcessId")
    return False


def read_config():
    if not CONFIG.exists():
        return {}
    try:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Refused(BROKEN, f"Claude Desktop's config.json can't be read ({error}), nothing changed.")
    if not isinstance(config, dict):
        raise Refused(BROKEN, "Claude Desktop's config.json is not an object, nothing changed.")
    return config


def encode(value):
    return {"base64": base64.b64encode(value).decode()} if isinstance(value, bytes) else value


def decode(value):
    return base64.b64decode(value["base64"]) if isinstance(value, dict) else value


def where():
    return (f"host_key IN ({', '.join('?' * len(HOSTS))}) AND name IN ({', '.join('?' * len(SESSION))})",
            HOSTS + SESSION)


def read_session(readonly=True):
    """The session cookies as saved rows, and the version of the cookie database."""
    if not COOKIES.exists():
        return [], None
    uri = f"{COOKIES.absolute().as_uri()}?mode=ro&immutable=1" if readonly else COOKIES.absolute().as_uri()
    with contextlib.closing(sqlite3.connect(uri, uri=True)) as db:
        db.row_factory = sqlite3.Row
        version = db.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()[0]
        clause, values = where()
        rows = db.execute(f"SELECT * FROM cookies WHERE {clause} ORDER BY name, host_key", values).fetchall()
    return [{key: encode(row[key]) for key in row.keys()} for row in rows], str(version)


def session_key(rows):
    """What names a session: its encrypted session cookie. Linux encrypts the same value to the same bytes. Windows
    encrypts with a new nonce each time, so there a session Desktop wrote again is named by lastKnownAccountUuid."""
    for row in rows:
        if row["name"] in KEYS and (decode(row["encrypted_value"]) or row["value"]):
            return row["encrypted_value"], row["value"]
    return None


def saved(account):
    try:
        return json.loads((SAVED / f"{account}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def name(rows, config):
    """The account of the open session, None when Desktop is signed out. Raises when nobody can tell."""
    key = session_key(rows)
    if key is None:
        return None
    for path in sorted(SAVED.glob("*.json")):
        other = saved(path.stem)
        if other and session_key(other.get("cookies", [])) == key:
            return other["account"]
    account = config.get(ACCOUNT)
    if isinstance(account, str) and UUID.fullmatch(account):
        return account
    raise Refused(UNNAMED, "Claude Desktop is signed in to an account it doesn't name, so it was left as it is.")


def write_private(path, text):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(path.name + ".tmp")
    with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temporary, path)


def labels():
    """The name or the email claude-switch shows for each account."""
    names = {}
    for path in STORE.glob("*.json"):
        try:
            account = json.loads(path.read_text(encoding="utf-8"))
            names[account["oauthAccount"]["accountUuid"]] = account.get("name") or account["oauthAccount"]["emailAddress"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return names


def save(rows, version, config, account):
    write_private(SAVED / f"{account}.json", json.dumps(
        {"account": account, "cookieVersion": version, "cookies": rows,
         "config": {key: config[key] for key in TOKENS if key in config}}))


@contextlib.contextmanager
def stops_held():
    """TERM, INT and HUP wait until the block is done. Windows has no signal mask, so Ctrl+C and Ctrl+Break are held
    by handlers that note them, and end the script after the block."""
    if not WINDOWS:
        stops = {signal.SIGTERM, signal.SIGINT, signal.SIGHUP}
        signal.pthread_sigmask(signal.SIG_BLOCK, stops)
        try:
            yield
        finally:
            signal.pthread_sigmask(signal.SIG_UNBLOCK, stops)
        return
    caught = []
    previous = {number: signal.signal(number, lambda number, frame: caught.append(number))
                for number in (signal.SIGINT, signal.SIGBREAK)}
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    if caught:
        raise KeyboardInterrupt


def saved_web(account):
    return SAVED / f"{account}.web"


def save_web(account):
    """Windows: the preferences of the window, saved beside the sign-in of ACCOUNT. The copy is made aside and renamed
    after, so a copy that fails keeps the previous one."""
    if not WINDOWS:
        return
    target = saved_web(account)
    copy, old = target.with_name(target.name + ".tmp"), target.with_name(target.name + ".old")
    shutil.rmtree(copy, ignore_errors=True)
    copy.mkdir(parents=True)
    for name in WEB:
        if (DESKTOP / name).is_dir():
            shutil.copytree(DESKTOP / name, copy / name)
    shutil.rmtree(old, ignore_errors=True)
    if target.exists():
        os.replace(target, old)
    os.replace(copy, target)
    shutil.rmtree(old, ignore_errors=True)


def stage_web(account):
    """Windows: the preferences saved for ACCOUNT, copied beside Desktop's own and ready to go in, as (folder, copy)
    pairs: no copy for a folder they lack, which goes. [] when ACCOUNT has none saved, the preferences in place stay."""
    source = saved_web(account)
    if not WINDOWS or not source.is_dir():
        return []
    staged = []
    try:
        for name in WEB:
            copy = DESKTOP / f"{name}.switching"
            shutil.rmtree(copy, ignore_errors=True)
            if (source / name).is_dir():
                shutil.copytree(source / name, copy)
                staged.append((name, copy))
            else:
                staged.append((name, None))
    except BaseException:
        drop_staged(staged)
        raise
    return staged


def drop_staged(staged):
    for _, copy in staged:
        if copy is not None:
            shutil.rmtree(copy, ignore_errors=True)


def swap_web(staged):
    """Puts the staged preferences in place: each folder of Desktop goes aside and its copy takes its place, all undone
    when one fails. Returns what went aside, to remove once the switch is written."""
    aside = []
    try:
        for name, copy in staged:
            live, old = DESKTOP / name, DESKTOP / f"{name}.old"
            shutil.rmtree(old, ignore_errors=True)
            if live.exists():
                os.replace(live, old)
                aside.append((live, old, copy))
            else:
                aside.append((live, None, copy))
            if copy is not None:
                os.replace(copy, live)
    except BaseException:
        unswap_web(aside)
        raise
    return aside


def unswap_web(aside):
    """Desktop's own folders back where they were."""
    for live, old, copy in reversed(aside):
        if copy is not None and not copy.exists():
            shutil.rmtree(live, ignore_errors=True)
        if old is not None and old.exists() and not live.exists():
            os.replace(old, live)


def finish_web(aside):
    for _, old, _ in aside:
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)


def repair_web():
    """Windows: a switch cut in the middle of the swap left a folder of Desktop aside, which comes back. The copies it
    left go."""
    for name in WEB:
        live, old, copy = DESKTOP / name, DESKTOP / f"{name}.old", DESKTOP / f"{name}.switching"
        if old.is_dir() and not live.exists():
            os.replace(old, live)
        shutil.rmtree(old, ignore_errors=True)
        shutil.rmtree(copy, ignore_errors=True)


def put_back(account, config, version):
    """Puts the sign-in saved for ACCOUNT in place, and on Windows its preferences. True when there was a sign-in."""
    copy = saved(account)
    rows = copy["cookies"] if copy and copy.get("cookieVersion") == version else []
    for key in TOKENS:
        config.pop(key, None)
    if rows:
        config.update(copy.get("config", {}))
    config[ACCOUNT] = account
    clause, values = where()
    staged = stage_web(account)
    aside = []
    try:
        with contextlib.closing(sqlite3.connect(COOKIES)) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(cookies)")}
            db.execute("BEGIN IMMEDIATE")
            db.execute(f"DELETE FROM cookies WHERE {clause}", values)
            for row in rows:
                names = [key for key in row if key in columns]
                db.execute(f"INSERT INTO cookies({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
                           [decode(row[key]) for key in names])
            if running():
                db.rollback()
                raise Refused(RUNNING, "Claude Desktop started during the switch, nothing changed.")
            temporary = CONFIG.with_name("config.json.switch")
            with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w",
                      encoding="utf-8") as handle:
                handle.write(json.dumps(config, indent="\t"))
            # A stop between the two writes would leave the cookies of one account under the name of the
            # other, which Desktop takes for another person, so it waits until both are done.
            with stops_held():
                aside = swap_web(staged)
                try:
                    db.commit()
                except BaseException:
                    unswap_web(aside)
                    raise
                os.replace(temporary, CONFIG)
    finally:
        drop_staged(staged)
    finish_web(aside)
    return bool(rows)


def switch(account):
    if not UUID.fullmatch(account):
        raise Refused(2, f"{account} is not an account id.")
    if not DESKTOP.is_dir() or not COOKIES.exists():
        return ""
    who = labels().get(account, account)
    config = read_config()
    if WINDOWS and running() is not None:
        return open_on_windows(account, config, who)
    rows, version = read_session()
    current = name(rows, config)
    if current == account:
        return f"Claude Desktop is already on {who}."
    if running():
        raise Refused(RUNNING, "Claude Desktop is open.")
    # Desktop writes config.json through a journal and replays a leftover one at its next start.
    if CONFIG.with_name("config.json.journal").exists():
        raise Refused(BROKEN, "Claude Desktop didn't finish writing its settings. Start it and quit it once, then "
                              "switch again.")
    if WINDOWS:
        repair_web()
    rows, version = read_session(readonly=False)
    current = name(rows, config)
    if current is not None:
        save(rows, version, config, current)
        save_web(current)
    if put_back(account, config, version):
        return f"Claude Desktop is on {who}."
    return f"Claude Desktop is signed out: sign in to it once as {who}, it follows the switch from then on."


def shell_word(text):
    """TEXT as one word to type in PowerShell or cmd: as it is when nothing in it needs quotes, else in quotes."""
    return text if re.fullmatch(r"[\w.:/=+-][\w@.:/=+-]*", text) else "'" + text.replace("'", "''") + "'"


def open_on_windows(account, config, who):
    """Windows: an open Desktop holds its cookie database for itself alone, so it is on the account its config.json
    names, while it holds a token there."""
    if config.get(ACCOUNT) == account and any(config.get(key) for key in TOKENS):
        return f"Claude Desktop is already on {who}."
    if inside_desktop():
        raise Refused(INSIDE, f"Claude Desktop runs this terminal, so it stays on its account. Quit it, then run "
                              f"claude-switch {shell_word(who)} in another terminal to bring it along.")
    raise Refused(RUNNING, "Claude Desktop is open.")


def status():
    config = read_config() if CONFIG.exists() else {}
    pid = running() if DESKTOP.is_dir() else None
    if WINDOWS and pid is not None:
        account = config.get(ACCOUNT)
        signed_in = any(config.get(key) for key in TOKENS)
        return {"installed": True, "running": True, "signedIn": signed_in,
                "account": account if signed_in and isinstance(account, str) and UUID.fullmatch(account) else None,
                "folder": str(DESKTOP)}
    rows, _ = read_session()
    try:
        account = name(rows, config)
    except Refused:
        account = "unknown"
    return {"installed": DESKTOP.is_dir(), "running": pid is not None, "signedIn": session_key(rows) is not None,
            "account": account, "folder": str(DESKTOP)}


def quit_desktop(seconds):
    pid = running()
    if pid is None:
        return 0
    if pid < 0:
        print("Claude Desktop is open and its process can't be told, close it by hand." if WINDOWS
              else "Claude Desktop is open on another computer.", file=sys.stderr)
        return 1
    if WINDOWS:
        return quit_on_windows(pid, seconds)
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + seconds
    while running() is not None:
        if time.monotonic() > deadline:
            print(f"Claude Desktop is still open after {seconds:g} seconds.", file=sys.stderr)
            return 1
        time.sleep(0.2)
    return 0


def quit_on_windows(pid, seconds):
    """taskkill without /F asks the windows of Desktop to close, and a Desktop that keeps running in the tray stays
    open (seen on the PC, 2026-10-16). So after 5 seconds its process ends with its tree, as the PC's earlier switcher
    ended it. Then the helpers that outlive it go, and its cookie database and web storage must be free."""
    if inside_desktop():
        print("Claude Desktop runs this terminal, quit it by hand.", file=sys.stderr)
        return 1
    if end_process(pid, force=False):
        gone_within(min(seconds, 5))
    if lock_held():
        end_process(pid, force=True)
        if not gone_within(seconds):
            print(f"Claude Desktop is still open after {seconds:g} seconds.", file=sys.stderr)
            return 1
    for helper in folder_helpers(windows_processes("claude.exe") or []):
        end_process(helper["ProcessId"], force=True)
    deadline = time.monotonic() + FILES_FREE_SECONDS
    while busy := busy_files():
        if time.monotonic() > deadline:
            print(f"Claude Desktop's files are still in use: {', '.join(busy)}", file=sys.stderr)
            return 1
        time.sleep(0.2)
    return 0


def gone_within(seconds):
    deadline = time.monotonic() + seconds
    while lock_held():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.2)
    return True


def end_process(pid, force):
    """taskkill: with FORCE the process ends with its tree, without it its windows are asked to close. True when
    taskkill did it."""
    command = ["taskkill", "/PID", str(pid)] + (["/T", "/F"] if force else [])
    try:
        return subprocess.run(command, capture_output=True, timeout=30, creationflags=NO_WINDOW).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def busy_files():
    """Windows: the files of Desktop another process still holds, its cookie database and the LOCK files of its web
    storage."""
    paths = [COOKIES, *(path for name in WEB for path in (DESKTOP / name).rglob("LOCK"))]
    return [str(path) for path in paths if path.is_file() and not file_free(path)]


def file_free(path):
    """True when PATH opens for this process alone."""
    try:
        handle = _winapi.CreateFile(str(path), _winapi.GENERIC_READ | _winapi.GENERIC_WRITE, 0, _winapi.NULL,
                                    _winapi.OPEN_EXISTING, 0, _winapi.NULL)
    except OSError as error:
        # 32: another process has it open, 33: another process locked a part of it.
        return getattr(error, "winerror", None) not in (32, 33)
    _winapi.CloseHandle(handle)
    return True


def start():
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("CLAUDE", "ANTHROPIC"))}
    if WINDOWS:
        return start_on_windows(environment)
    subprocess.Popen(["claude-desktop"], env=environment, start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=HOME)
    return 0


def start_on_windows(environment):
    """An older install starts from its claude.exe, the Store build from its app id through the shell, which starts it
    with the environment of Explorer rather than this one. The app id is Claude_pzs8sxrjxfjjc!Claude on the PC
    (Get-StartApps, 2026-11-09)."""
    program = pathlib.Path(os.environ.get("LOCALAPPDATA") or HOME / "AppData" / "Local") / "AnthropicClaude" / "claude.exe"
    if program.is_file():
        subprocess.Popen([str(program)], env=environment, cwd=HOME, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        return 0
    command = ("$app = Get-StartApps | Where-Object { $_.AppID -like 'Claude_*!*' } | Select-Object -First 1; "
               "if (-not $app) { exit 1 }; Start-Process ('shell:AppsFolder\\' + $app.AppID)")
    return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                          creationflags=NO_WINDOW).returncode


def main(arguments):
    command = arguments[0] if arguments else ""
    try:
        if command == "status" and len(arguments) == 1:
            print(json.dumps(status()))
            return 0
        if command == "switch" and len(arguments) == 2:
            message = switch(arguments[1])
            if message:
                print(message)
            return 0
        if command == "quit" and len(arguments) <= 2:
            return quit_desktop(float(arguments[1]) if len(arguments) == 2 else 20)
        if command == "start" and len(arguments) == 1:
            return start()
    except Refused as refusal:
        print(refusal, file=sys.stderr)
        return refusal.status
    except (OSError, sqlite3.Error) as error:
        print(f"Claude Desktop could not be switched: {error}", file=sys.stderr)
        return 1
    print(__doc__.split("\n\n")[-2], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
