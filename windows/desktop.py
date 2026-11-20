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

On Windows nothing of an account is lost at a switch, and its configuration goes over to the next one. The window's
storage (Local Storage, Session Storage, IndexedDB, WebStorage) stays in place for every account, so the pinned
projects, the starred sessions and the sidebar's sorting stay as they are. What Desktop keeps in it under the account
left, its sidebar sections and groups, its project order and its presets, is copied to the next account, as are the
settings its own files keep per account. What an account can't hand over, its sign-in, its Code
sessions, its Remote Control environments and its drafts, stays its own: the storage is saved with the account left,
and what goes missing while it is away comes back when it does. IndexedDB and WebStorage can't be merged, and
Remote Control keeps the account's trusted device key there, so they are the account's and come back with its
sign-in.
The bypass permissions gate and what auto mode warned of stay per account, as do what an account connected on
claude.ai and what claude.ai found it may use.

The open session is named by its cookies among the saved ones, else by lastKnownAccountUuid. A session nobody can
name is never saved or replaced. The saved sign-ins are in ~/.claude-accounts/.desktop, one file per account (and on
Windows the window's storage of the account beside it), and only you can read them.

    desktop.py status            what Desktop is on, as JSON
    desktop.py switch ACCOUNT    save the sign-in under its account, put back the one of ACCOUNT
    desktop.py quit [SECONDS]    close Desktop and wait until it is gone, 20 seconds at most
    desktop.py start             start Desktop, without the environment of the calling session, in a systemd
                                 scope of its own when the user's systemd runs

switch exits 3 when Desktop is open and on another account, 4 when its session has no name, and on Windows 6 when
Desktop is open and runs this switch, through one of its Code sessions or its terminal.
"""
import base64
import contextlib
import copy
import importlib.util
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
ID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
UUID = re.compile(ID)
RUNNING, UNNAMED, BROKEN, INSIDE = 3, 4, 5, 6
NO_WINDOW = subprocess.CREATE_NO_WINDOW if WINDOWS else 0
# Windows: the web storage of the window, which holds its preferences. One for every account, as on Linux.
WEB = ("Local Storage", "Session Storage", "IndexedDB", "WebStorage")
LOCAL_STORAGE = DESKTOP / "Local Storage" / "leveldb"
ORIGIN = "https://claude.ai"
# The keys of the window's Local Storage that are settings named after an account (or its organization), which go
# over to the next account: the persisted preferences and the presets of the Code tab. Not the rows of the connectors
# list, which say what the account connected on claude.ai.
CARRIED = ("LSS-persisted.", "persisted.ccd-session-presets.")
# The values that keep settings per account inside them, under the account's id or its id and its organization's.
SCOPED = ("dframe-store", "LSS-persisted.")
# Not carried, wherever they are kept: the bypass permissions gate and what auto mode and the permission modes warned
# of and denied, which each account answers for itself; what claude.ai found the account may use, which it finds again
# for the next one; and the auto resume of the sessions of the account left, which the next one doesn't have.
NOT_CARRIED = ("bypassPermissions", "epitaxy-auto-", "perm-mode-ack", "ultrathinkAvailable", "autoResumeRateLimit")
# The keys of a map inside a value that name what belongs to one account: its id, its organization's, one of its Code
# sessions (local_<id>), or a pair of them.
OWNED = re.compile(f"(?:[a-z]+[_:])?{ID}(?:[/:]{ID})?")
# What names an environment or a session of one account inside a setting, as the defaults of the Code tab's presets do
# (bridge:env_<id>, cloud:environment:env_<id>, seen on the PC on 2026-11-09). Its ids mean nothing to another
# account, so a setting carried over keeps the next account's own.
BOUND = re.compile(rf"(?:env|session)_[0-9A-Za-z]{{16,}}|local_{ID}")
# Desktop's own files of settings kept per account, carried the same way. remote-control-state.json and
# ant-device-registry.json are not among them: they hold what claude.ai registered for one account.
CARRIED_FILES = ("claude_desktop_config.json", "ccd-session-presets.json", "mcp-user-tool-toggles.json")
INDEXEDDB = ("IndexedDB/https_claude.ai_0.indexeddb.leveldb", "IndexedDB/https_claude.ai_0.indexeddb.blob")
# The databases of the window that belong to the account signed in and can't be merged: WebStorage, and claude.ai's
# IndexedDB, the two folders Chromium deletes when it clears the site. claude.ai keeps the trusted device key of Remote
# Control there, and Desktop emptied them when it started signed out (seen on the PC on 2026-10-22), so each account
# gets its own back with its sign-in. Local Storage is merged instead, and comes back whole only when it went whole.
SWAPPED = ("WebStorage", *INDEXEDDB)
# What the copy saved for an account keeps from the copy before when the window no longer has it, each with the
# folders that go with it, so the only copy of a database is never dropped.
KEPT = (("Local Storage/leveldb",), ("WebStorage",), INDEXEDDB)
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
    """Windows: the window's storage as the account left had it, saved beside its sign-in, from which what it loses
    while away comes back. A database the window no longer has is kept from the copy saved before, so its only copy is
    never dropped. The copy is made aside and renamed after, so a copy that fails keeps the previous one."""
    if not WINDOWS:
        return
    target = saved_web(account)
    fresh, old = target.with_name(target.name + ".tmp"), target.with_name(target.name + ".old")
    shutil.rmtree(fresh, ignore_errors=True)
    fresh.mkdir(parents=True)
    for name in WEB:
        if (DESKTOP / name).is_dir():
            shutil.copytree(DESKTOP / name, fresh / name)
    for unit in KEPT:
        if not any((fresh / path).exists() for path in unit):
            for path in unit:
                if (target / path).is_dir():
                    shutil.copytree(target / path, fresh / path)
    shutil.rmtree(old, ignore_errors=True)
    if target.exists():
        os.replace(target, old)
    os.replace(fresh, target)
    shutil.rmtree(old, ignore_errors=True)


def beside(path, suffix):
    live = DESKTOP / path
    return live.with_name(live.name + suffix)


def stage_web(account):
    """Windows: the databases saved for ACCOUNT, copied beside Desktop's own and ready to go in, as (path, copy) pairs:
    no copy for a database they lack, which goes. [] when ACCOUNT has none saved: the ones in place stay."""
    source = saved_web(account)
    if not WINDOWS or not source.is_dir():
        return []
    staged = []
    try:
        for path in SWAPPED:
            ready = beside(path, ".switching")
            shutil.rmtree(ready, ignore_errors=True)
            if (source / path).is_dir():
                shutil.copytree(source / path, ready)
                staged.append((path, ready))
            else:
                staged.append((path, None))
    except BaseException:
        drop_staged(staged)
        raise
    return staged


def drop_staged(staged):
    for _, ready in staged:
        if ready is not None:
            shutil.rmtree(ready, ignore_errors=True)


def swap_web(staged):
    """Puts the staged databases in place: each of Desktop's goes aside and its copy takes its place, all undone when
    one fails. Returns what went aside, to remove once the switch is written."""
    aside = []
    try:
        for path, ready in staged:
            live, old = DESKTOP / path, beside(path, ".old")
            shutil.rmtree(old, ignore_errors=True)
            if live.exists():
                os.replace(live, old)
                aside.append((live, old, ready))
            else:
                aside.append((live, None, ready))
            if ready is not None:
                os.replace(ready, live)
    except BaseException:
        unswap_web(aside)
        raise
    return aside


def unswap_web(aside):
    """Desktop's own databases back where they were."""
    for live, old, ready in reversed(aside):
        if ready is not None and not ready.exists():
            shutil.rmtree(live, ignore_errors=True)
        if old is not None and old.exists() and not live.exists():
            os.replace(old, live)


def finish_web(aside):
    for _, old, _ in aside:
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)


def repair_web():
    """Windows: a switch cut in the middle of the swap left a database of Desktop aside, which comes back before
    anything is saved. The copies it left go."""
    for path in SWAPPED:
        live, old, ready = DESKTOP / path, beside(path, ".old"), beside(path, ".switching")
        if old.is_dir() and not live.exists():
            os.replace(old, live)
        shutil.rmtree(old, ignore_errors=True)
        shutil.rmtree(ready, ignore_errors=True)


def localstorage():
    """localstorage.py, next to this file: python -I puts no folder of a script on the path."""
    spec = importlib.util.spec_from_file_location("localstorage", pathlib.Path(__file__).with_name("localstorage.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def organization(account):
    """The organization Desktop keeps the settings of ACCOUNT under: the one it keeps its Code sessions under, else the
    one its saved login names (they were the same on the PC, 2026-11-09). None when neither says."""
    folders = [path for path in (DESKTOP / "claude-code-sessions" / account).glob("*")
               if path.is_dir() and UUID.fullmatch(path.name)]
    if folders:
        return max(folders, key=lambda path: path.stat().st_mtime).name
    for path in STORE.glob("*.json"):
        try:
            oauth = json.loads(path.read_text(encoding="utf-8"))["oauthAccount"]
            if oauth["accountUuid"] == account and UUID.fullmatch(oauth["organizationUuid"]):
                return oauth["organizationUuid"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def sessions_of(account):
    """The ids of the Code sessions of ACCOUNT."""
    return {found for path in (DESKTOP / "claude-code-sessions" / account).rglob("local_*.json")
            for found in UUID.findall(path.stem)}


def retargetable(key, mapping):
    """True when every id KEY names is one MAPPING moves to the next account."""
    ids = UUID.findall(key)
    return bool(ids) and all(found in mapping for found in ids)


def retarget(key, mapping):
    for old, new in mapping.items():
        key = key.replace(old, new)
    return key


def carry_scopes(value, mapping):
    """Copies, at any depth of VALUE, what is kept under a key that names the account left, by its id or its id and its
    organization's, alone or after a name (code-projects-order.<id> in claude_desktop_config.json), to the same key of
    the next account. True when something changed."""
    changed = False
    if isinstance(value, dict):
        for key in list(value):
            if any(marker in key for marker in NOT_CARRIED):
                continue
            changed |= carry_scopes(value[key], mapping)
            if retargetable(key, mapping):
                new = retarget(key, mapping)
                carried = carried_value(value.get(new), value[key])
                if value.get(new) != carried:
                    value[new] = carried
                    changed = True
    elif isinstance(value, list):
        for item in value:
            changed |= carry_scopes(item, mapping)
    return changed


def carried_value(kept, carried):
    """CARRIED, a setting of the account left, in place of KEPT, the next account's: at any depth of their objects, what
    names one of the next account's environments or sessions stays where CARRIED has nothing of it."""
    if not isinstance(kept, dict) or not isinstance(carried, dict):
        return copy.deepcopy(carried)
    value = {key: carried_value(kept[key], item) if key in kept else copy.deepcopy(item) for key, item in carried.items()}
    for key, item in kept.items():
        if key not in carried:
            own = copy.deepcopy(item) if BOUND.search(key) else bound_part(item)
            if own is not None:
                value[key] = own
    return value


def bound_part(value):
    """What VALUE keeps under keys that name an environment or a session, at any depth. None when it keeps nothing."""
    if not isinstance(value, dict):
        return None
    part = {key: copy.deepcopy(item) if BOUND.search(key) else bound_part(item) for key, item in value.items()}
    return {key: item for key, item in part.items() if item is not None} or None


def owned(key, own):
    """True when KEY names what belongs to one account alone: OWN holds its id, its organization's and its sessions'."""
    found = set(UUID.findall(key))
    return bool(found) and found <= own


def restore_entries(live, kept, own):
    """Puts back in LIVE, at any depth, what KEPT holds under a key of the account coming back and LIVE lost: its scope,
    or one of its sessions. True when something changed."""
    changed = False
    if isinstance(live, dict) and isinstance(kept, dict):
        for key, value in kept.items():
            if key in live:
                changed |= restore_entries(live[key], value, own)
            elif OWNED.fullmatch(key) and owned(key, own):
                live[key] = copy.deepcopy(value)
                changed = True
    return changed


def as_json(text):
    try:
        return json.loads(text)
    except ValueError:
        return None


def as_text(value):
    """VALUE as the window writes it, JSON.stringify's compact form."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def carry_local_storage(mapping, target):
    """The settings of the window kept under the account left go to TARGET, and what TARGET lost while away comes back
    from the copy saved when it was left. Returns how many keys moved, how many came back, and what went wrong with
    the saved copy, which leaves the carrying over to go on."""
    ls = localstorage()
    if not (LOCAL_STORAGE / "CURRENT").is_file():
        return 0, 0, []
    db = ls.Database(LOCAL_STORAGE)
    live = db.origin(ORIGIN)
    changes = {}
    if mapping:
        for key, value in live.items():
            data = as_json(value) if key.startswith(SCOPED) else None
            if data is not None and carry_scopes(data, mapping):
                changes[key] = as_text(data)
        for key, value in live.items():
            if (key.startswith(CARRIED) and retargetable(key, mapping)
                    and not any(marker in key for marker in NOT_CARRIED)):
                new, carried = retarget(key, mapping), changes.get(key, value)
                kept = changes.get(new, live.get(new))
                data, kept_data = as_json(carried), as_json(kept) if kept is not None else None
                if isinstance(data, dict) and isinstance(kept_data, dict):
                    merged = carried_value(kept_data, data)
                    if merged != kept_data:
                        changes[new] = as_text(merged)
                elif kept != carried:
                    changes[new] = carried
    moved = len(changes)
    restored = 0
    problems = []
    kept_folder = saved_web(target) / "Local Storage" / "leveldb"
    kept = {}
    if (kept_folder / "CURRENT").is_file():
        try:
            kept = ls.Database(kept_folder).origin(ORIGIN)
        except Exception as error:
            problems.append(f"Not put back, its saved copy can't be read: the window's storage ({error}).")
    if kept:
        own = {target, organization(target), *sessions_of(target)} - {None}
        for key, value in kept.items():
            now = changes.get(key, live.get(key))
            if now is None:
                if owned(key, own):
                    changes[key] = value
                    restored += 1
                continue
            data = as_json(now)
            if isinstance(data, dict) and restore_entries(data, as_json(value), own):
                changes[key] = as_text(data)
                restored += 1
    # Desktop opens its Local Storage seconds after it starts, so one that hasn't started by now finds the write done.
    if changes and running() is not None:
        raise Refused(RUNNING, "Claude Desktop started during the switch, nothing changed.")
    db.write(ORIGIN, changes)
    return moved, restored, problems


def carry_files(mapping):
    """The settings Desktop's own files keep per account go from the account left to the next one: the preferences of
    claude_desktop_config.json, the presets and the tool toggles. Not the bypass permissions gate. Returns how many
    files changed, none put back, and the ones that could not be written, which stay as they were. A file that can't be
    read is Desktop's, and is left alone."""
    changed, failed = 0, []
    for name in CARRIED_FILES if mapping else ():
        path = DESKTOP / name
        try:
            text = path.read_text(encoding="utf-8")
            data = json.loads(text)
        except (OSError, ValueError):
            continue
        settings = data.get("preferences") if name == "claude_desktop_config.json" and isinstance(data, dict) else data
        if not isinstance(settings, dict) or not carry_scopes(settings, mapping):
            continue
        indent = re.match(r"\{\r?\n([ \t]+)", text)
        try:
            write_file(path, json.dumps(data, indent=indent.group(1) if indent else None, ensure_ascii=False)
                       + ("\n" if text.endswith("\n") else ""))
        except OSError as error:
            failed.append(f"Not carried over, left as it was: {name} ({error}).")
            continue
        changed += 1
    return changed, 0, failed


def write_file(path, text):
    """TEXT in PATH, whole or not at all."""
    temporary = path.with_name(path.name + ".switch")
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def restore_folders(target):
    """Local Storage gone whole comes back whole from the copy saved for TARGET, before the settings are carried over.
    Returns none carried over, how many came back, and no problem: a copy that fails raises."""
    kept = saved_web(target)
    missing = [unit for unit in KEPT[:1] if not any((DESKTOP / path).exists() for path in unit)
               and any((kept / path).is_dir() for path in unit)]
    if missing and running() is not None:
        raise Refused(RUNNING, "Claude Desktop started during the switch, nothing changed.")
    for unit in missing:
        copies = [(kept / path, DESKTOP / path, (DESKTOP / path).with_name((DESKTOP / path).name + ".switch"))
                  for path in unit if (kept / path).is_dir()]
        try:
            for source, _, temporary in copies:
                shutil.rmtree(temporary, ignore_errors=True)
                shutil.copytree(source, temporary)
            for _, live, temporary in copies:
                os.replace(temporary, live)
        finally:
            for _, _, temporary in copies:
                shutil.rmtree(temporary, ignore_errors=True)
    return 0, len(missing), []


def carry_over(current, target):
    """Windows: the configuration of the account left goes over to TARGET, and what TARGET lost while away comes back.
    What an account can't hand over, its sign-in, its Code sessions, its Remote Control environments and its drafts,
    stays its own and is never deleted: the copy saved when it was left brings back what went missing. A part that
    fails is left as it was, the rest of the switch goes on, and the message says which part and why."""
    if not WINDOWS:
        return ""
    mapping = {}
    if current is not None and current != target:
        mapping[current] = target
        old, new = organization(current), organization(target)
        if old and new:
            # Two accounts of one organization keep it: their scopes still move from one account to the other.
            mapping[old] = new
    moved = restored = 0
    failed = []
    for part, step in (("the window's storage", lambda: restore_folders(target)),
                       ("the window's storage", lambda: carry_local_storage(mapping, target)),
                       ("Desktop's settings files", lambda: carry_files(mapping))):
        try:
            more, back, problems = step()
        except Refused:
            raise
        except Exception as error:
            failed.append(f"Not carried over, left as it was: {part} ({error}).")
            continue
        moved, restored, failed = moved + more, restored + back, failed + problems
    counts = [f"{moved} carried over" if moved else "", f"{restored} put back" if restored else ""]
    return "\n".join(filter(None, [
        f"Claude Desktop's settings: {', '.join(filter(None, counts))}." if moved or restored else "", *failed]))


def put_back(account, config, version):
    """Puts the sign-in saved for ACCOUNT in place, and on Windows its databases. True when there was a sign-in."""
    copy = saved(account)
    rows = copy["cookies"] if copy and copy.get("cookieVersion") == version else []
    for key in TOKENS:
        config.pop(key, None)
    if rows:
        config.update(copy.get("config", {}))
    config[ACCOUNT] = account
    clause, values = where()
    temporary = CONFIG.with_name("config.json.switch")
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
            with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w",
                      encoding="utf-8") as handle:
                handle.write(json.dumps(config, indent="\t"))
            # A stop between the writes would leave the cookies of one account under the name of the other, which
            # Desktop takes for another person, or the databases of one with the sign-in of the other, so it waits
            # until all are done.
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
        temporary.unlink(missing_ok=True)
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
    carried = carry_over(current, account)
    if put_back(account, config, version):
        return "\n".join(filter(None, [f"Claude Desktop is on {who}.", carried]))
    return "\n".join(filter(None, [f"Claude Desktop is signed out: sign in to it once as {who}, it follows the switch "
                                   "from then on.", carried]))


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
    command = ["claude-desktop"]
    # What a systemd service starts goes with it when it ends, the automatic switch of the watch timer for instance, so
    # Desktop gets a scope of its own whenever the user's systemd is there to give one. Windows starts it through the
    # shell, out of the task that switched.
    runtime = environment.get("XDG_RUNTIME_DIR")
    if runtime and os.path.exists(os.path.join(runtime, "systemd", "private")) \
            and shutil.which("systemd-run", path=environment.get("PATH")):
        command = ["systemd-run", "--user", "--scope", "--quiet", "--collect", "--"] + command
    subprocess.Popen(command, env=environment, start_new_session=True, stdin=subprocess.DEVNULL,
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
