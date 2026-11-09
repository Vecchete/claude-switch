#!/usr/bin/env python3
"""Tests of the Windows branches of windows/desktop.py on a scratch profile: which folder is Desktop's, an open Desktop
that holds its cookie database, the preferences that go with each account, and a quit that ends a stand-in Desktop.
The real Claude Desktop is never touched: every process a test ends is a stand-in it started, and the runs that would
call taskkill get a fake one. Windows only, see tests/test-desktop.py for the rest.
Usage: test-desktop.py [DESKTOP_PY], windows/desktop.py by default."""
import contextlib
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

if os.name != "nt":
    print("skipped: the Windows branches of desktop.py run on Windows only")
    sys.exit(0)

HERE = pathlib.Path(__file__).resolve().parent
HELPER = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else HERE.parent / "desktop.py").resolve()
ACCOUNT_A = "aaaaaaaa-0000-4000-8000-0000000000a1"
ACCOUNT_B = "bbbbbbbb-0000-4000-8000-0000000000b1"
SESSION = ("sessionKey", "sessionKeyLC", "routingHint", "lastActiveOrg")
COLUMNS = ["creation_utc", "host_key", "top_frame_site_key", "name", "value", "encrypted_value", "path", "expires_utc",
           "is_secure", "is_httponly", "last_access_utc", "has_expires", "is_persistent", "priority", "samesite",
           "source_scheme", "source_port", "last_update_utc", "source_type", "has_cross_site_ancestor"]
WEB = ("Local Storage", "Session Storage", "IndexedDB", "WebStorage")
NO_WINDOW = subprocess.CREATE_NO_WINDOW
failures = []
passes = 0

# Loads desktop.py, swaps in what TEST_PLAN names, then runs it as desktop.py would run.
RUNNER = r'''
import importlib.util, json, os, signal, sys
spec = importlib.util.spec_from_file_location("desktop", sys.argv[1])
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)
plan = json.loads(os.environ.get("TEST_PLAN", "{}"))
if plan.get("folder"):
    print(desktop.DESKTOP)
    sys.exit(0)
if "file_free" in plan:
    print(json.dumps(desktop.file_free(plan["file_free"])))
    sys.exit(0)
if "processes" in plan:
    desktop.windows_processes = lambda name=None: plan["processes"]
if "inside" in plan:
    desktop.inside_desktop = lambda: plan["inside"]
if "files_free_seconds" in plan:
    desktop.FILES_FREE_SECONDS = plan["files_free_seconds"]
if "end" in plan:
    end = plan["end"]
    def end_process(pid, force):
        with open(end["log"], "a") as log:
            log.write(json.dumps([pid, force]) + "\n")
        if pid == end.get("stand_in") and force in end.get("kills_on", []):
            os.kill(pid, signal.SIGTERM)
            return True
        return False
    desktop.end_process = end_process
if "fail_replace" in plan:
    real = os.replace
    def replace(source, target):
        if str(source).endswith(plan["fail_replace"]):
            raise PermissionError(13, "held by a test", str(source))
        return real(source, target)
    os.replace = replace
sys.exit(desktop.main(sys.argv[2:]))
'''

# A stand-in that holds files the way Desktop does: "lock" as Chromium holds lockfile (others may read it, and it goes
# when the handle closes), "exclusive" as it holds its cookie database.
HOLD = r'''
import _winapi, sys, time
lock = sys.argv[1] == "lock"
handles = [_winapi.CreateFile(path, 0x40000000 | 0x80000000, 1 if lock else 0, 0, 2 if lock else 3,
                              0x04000000 if lock else 0, 0) for path in sys.argv[2:]]
print("held", flush=True)
time.sleep(300)
'''


def check(name, condition):
    global passes
    if condition:
        passes += 1
    else:
        failures.append(name)
        print("FAIL", name)


class Machine:
    def __init__(self, root):
        self.root = root
        self.home, self.local, self.roaming = root / "home", root / "local", root / "roaming"
        self.desktop = self.local / "Claude-Data" / "claude_test"
        self.store = self.home / ".claude-accounts"
        for folder in (self.home, self.roaming, self.desktop / "Network", self.store):
            folder.mkdir(parents=True, exist_ok=True)
        # The folder the Store build used before it moved, left with only the marker of the move.
        self.left = self.local / "Packages" / "Claude_test" / "LocalCache" / "Roaming" / "Claude"
        self.left.mkdir(parents=True)
        (self.left / ".user-data-left.claude_test").write_text("")
        self.stand_ins = []
        self.log = root / "taskkill.log"
        for account, email, name in ((ACCOUNT_A, "a@example.com", "first"), (ACCOUNT_B, "b@example.com", "")):
            (self.store / f"{email}_x.json").write_text(json.dumps(
                {"name": name, "oauthAccount": {"accountUuid": account, "emailAddress": email}, "login": {}}))
        with self.database() as db:
            db.execute("CREATE TABLE meta(key LONGVARCHAR NOT NULL UNIQUE PRIMARY KEY, value LONGVARCHAR)")
            db.execute("INSERT INTO meta VALUES ('version', '24'), ('last_compatible_version', '24')")
            db.execute(f"CREATE TABLE cookies({', '.join(name + ' ' + ('BLOB' if name == 'encrypted_value' else 'TEXT' if name in ('host_key', 'top_frame_site_key', 'name', 'value', 'path') else 'INTEGER') + ' NOT NULL' for name in COLUMNS)})")
            db.execute("CREATE UNIQUE INDEX cookies_unique_index ON cookies(host_key, top_frame_site_key, "
                       "has_cross_site_ancestor, name, path, source_scheme, source_port)")
        self.add_cookie(".claude.ai", "anthropic-device-id", b"device")
        self.write_config({"locale": "en-US", "userThemeMode": "dark"})
        self.write_web("start")

    def cookie_file(self):
        return self.desktop / "Network" / "Cookies"

    @contextlib.contextmanager
    def database(self):
        """The cookie database, committed and closed after: Windows keeps a file another handle holds where it is."""
        db = sqlite3.connect(self.cookie_file())
        try:
            with db:
                yield db
        finally:
            db.close()

    def add_cookie(self, host, name, blob):
        with self.database() as db:
            db.execute(f"INSERT INTO cookies({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
                       (time.time_ns() // 1000, host, "", name, "", blob, "/", 13_500_000_000_000_000, 1, 1,
                        13_400_000_000_000_000, 1, 1, 1, 1, 2, 443, 13_400_000_000_000_000, 0, 0))

    def sign_in(self, account, tag):
        with self.database() as db:
            db.execute(f"DELETE FROM cookies WHERE host_key = '.claude.ai' AND name IN ({', '.join('?' * len(SESSION))})",
                       SESSION)
        for name in SESSION:
            self.add_cookie(".claude.ai", name, f"v10-{tag}-{name}".encode())
        config = self.config()
        config.update({"oauth:tokenCacheV2": f"djEw-{tag}", "lastKnownAccountUuid": account})
        self.write_config(config)

    def session(self):
        with self.database() as db:
            return {row[0]: row[1] for row in db.execute(
                "SELECT name, encrypted_value FROM cookies WHERE host_key = '.claude.ai'") if row[0] in SESSION}

    def config(self):
        return json.loads((self.desktop / "config.json").read_text(encoding="utf-8"))

    def write_config(self, config):
        (self.desktop / "config.json").write_text(json.dumps(config, indent="\t"), encoding="utf-8")

    def write_web(self, tag):
        """The preferences someone set: one file in each folder of the web storage, and the LOCK files of LevelDB."""
        for name in WEB:
            shutil.rmtree(self.desktop / name, ignore_errors=True)
            (self.desktop / name / "leveldb").mkdir(parents=True)
            (self.desktop / name / "leveldb" / "000003.log").write_text(f"{tag}-{name}")
            (self.desktop / name / "leveldb" / "LOCK").write_text("")

    def web(self, folder=None):
        folder = folder or self.desktop
        return {name: ((folder / name / "leveldb" / "000003.log").read_text()
                       if (folder / name / "leveldb" / "000003.log").exists() else None) for name in WEB}

    def leftovers(self):
        return sorted(path.name for path in list(self.desktop.iterdir()) + list((self.store / ".desktop").iterdir())
                      if path.name.endswith((".switching", ".old", ".tmp")))

    def snapshot(self):
        return (self.cookie_file().read_bytes(), (self.desktop / "config.json").read_bytes(), self.web(),
                sorted(path.name for path in (self.store / ".desktop").glob("*")) if (self.store / ".desktop").exists()
                else [])

    def env(self, **extra):
        base = {key: value for key, value in os.environ.items() if not key.upper().startswith(("CLAUDE", "ANTHROPIC"))}
        return dict(base, USERPROFILE=str(self.home), HOME=str(self.home), LOCALAPPDATA=str(self.local),
                    APPDATA=str(self.roaming), PYTHONDONTWRITEBYTECODE="1", **extra)

    def run(self, *args, plan=None):
        """desktop.py ARGS, through the runner, which swaps in what PLAN names. Every plan that can reach taskkill
        carries a fake one."""
        runner = self.root / "runner.py"
        runner.write_text(RUNNER, encoding="utf-8")
        return subprocess.run([sys.executable, "-I", str(runner), str(HELPER), *args], capture_output=True,
                              text=True, env=self.env(TEST_PLAN=json.dumps(plan or {})), timeout=120,
                              creationflags=NO_WINDOW)

    def hold(self, kind, *paths):
        """A stand-in process holding PATHS, as Desktop holds them."""
        process = subprocess.Popen([sys.executable, "-I", "-c", HOLD, kind, *map(str, paths)], stdout=subprocess.PIPE,
                                   text=True, creationflags=NO_WINDOW)
        self.stand_ins.append(process)
        process.stdout.readline()
        return process

    def stop(self):
        for process in self.stand_ins:
            if process.poll() is None:
                process.kill()
            process.wait()
        self.stand_ins.clear()

    def calls(self):
        calls = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        self.log.unlink(missing_ok=True)
        return calls


root = pathlib.Path(tempfile.mkdtemp(prefix="claude-switch-desktop-windows."))
try:
    m = Machine(root)

    # Which folder is Desktop's.
    folder = m.run(plan={"folder": True}).stdout.strip()
    check("folder: Claude-Data, not the one the Store build left", pathlib.Path(folder) == m.desktop)
    shutil.move(str(m.local / "Claude-Data"), str(root / "claude-data-aside"))
    folder = m.run(plan={"folder": True}).stdout.strip()
    check("folder: a folder with only the marker of the move is not Desktop's", pathlib.Path(folder) == m.roaming / "Claude")
    (m.left / ".user-data-left.claude_test").unlink()
    (m.left / "config.json").write_text("{}")
    folder = m.run(plan={"folder": True}).stdout.strip()
    check("folder: the package's copy of %APPDATA% before the move", pathlib.Path(folder) == m.left)
    (m.left / "config.json").unlink()
    (m.left / ".user-data-left.claude_test").write_text("")
    shutil.move(str(root / "claude-data-aside"), str(m.local / "Claude-Data"))

    status = json.loads(m.run("status").stdout)
    check("status: closed, signed out, its folder", not status["running"] and not status["signedIn"]
          and pathlib.Path(status["folder"]) == m.desktop)

    # The preferences go with the account.
    m.sign_in(ACCOUNT_A, "A")
    m.write_web("A")
    a_session = m.session()
    result = m.run("switch", ACCOUNT_B)
    check("to B: switched, asks for one sign-in", result.returncode == 0 and "sign in" in result.stdout)
    check("to B: A's preferences saved with it", m.web(m.store / ".desktop" / f"{ACCOUNT_A}.web") ==
          {name: f"A-{name}" for name in WEB})
    check("to B: B has none saved, so the ones in place stay", m.web() == {name: f"A-{name}" for name in WEB})
    m.sign_in(ACCOUNT_B, "B")
    m.write_web("B")
    b_session = m.session()
    result = m.run("switch", ACCOUNT_A)
    check("back to A: switched", result.returncode == 0 and "first" in result.stdout)
    check("back to A: its sign-in", m.session() == a_session and m.config()["lastKnownAccountUuid"] == ACCOUNT_A)
    check("back to A: its preferences", m.web() == {name: f"A-{name}" for name in WEB})
    check("back to A: B's preferences saved with it", m.web(m.store / ".desktop" / f"{ACCOUNT_B}.web") ==
          {name: f"B-{name}" for name in WEB})
    result = m.run("switch", ACCOUNT_B)
    check("to B again: its sign-in and its preferences", m.session() == b_session
          and m.web() == {name: f"B-{name}" for name in WEB})
    check("nothing left aside", m.leftovers() == [])
    check("the rest of config.json stays", m.config()["userThemeMode"] == "dark")

    # A folder the saved preferences lack goes.
    shutil.rmtree(m.store / ".desktop" / f"{ACCOUNT_A}.web" / "WebStorage")
    m.run("switch", ACCOUNT_A)
    check("a folder A's preferences lack: gone", m.web() == {**{name: f"A-{name}" for name in WEB}, "WebStorage": None}
          and not (m.desktop / "WebStorage").exists())
    m.write_web("A")

    # A switch cut in the middle of the swap left a folder of Desktop aside: the next switch puts it back first.
    (m.desktop / "Local Storage").rename(m.desktop / "Local Storage.old")
    (m.desktop / "IndexedDB.switching").mkdir()
    result = m.run("switch", ACCOUNT_B)
    check("a cut swap: repaired before A is saved", result.returncode == 0 and
          m.web(m.store / ".desktop" / f"{ACCOUNT_A}.web")["Local Storage"] == "A-Local Storage")
    check("a cut swap: nothing left aside", m.leftovers() == [])

    # A swap that fails is undone whole, and the sign-in stays.
    before = m.snapshot()
    result = m.run("switch", ACCOUNT_A, plan={"fail_replace": "IndexedDB.switching"})
    check("a failed swap: refused", result.returncode not in (0, 3, 4, 6))
    check("a failed swap: nothing changed", m.snapshot() == before)
    check("a failed swap: nothing left aside", m.leftovers() == [])

    # Desktop open: it holds its cookie database for itself alone.
    before = m.snapshot()
    stand_in = m.hold("lock", m.desktop / "lockfile")
    database = m.hold("exclusive", m.cookie_file())
    status = json.loads(m.run("status").stdout)
    check("open: status from config.json", status["running"] and status["signedIn"] and status["account"] == ACCOUNT_B)
    result = m.run("switch", ACCOUNT_B)
    check("open, already on B: fine", result.returncode == 0 and "already" in result.stdout)
    result = m.run("switch", ACCOUNT_A, plan={"inside": False})
    check("open: refused with 3", result.returncode == 3)
    result = m.run("switch", ACCOUNT_A, plan={"inside": True})
    check("open and running this switch: refused with 6, with how to bring it along",
          result.returncode == 6 and "claude-switch first" in result.stderr)
    database.kill()
    database.wait()
    check("open: nothing changed", m.snapshot() == before)

    # A quit never ends a Desktop it can't tell, nor the one that runs it.
    end = {"log": str(m.log), "stand_in": stand_in.pid}
    result = m.run("quit", "3", plan={"inside": False, "end": dict(end, kills_on=[True])})
    check("quit, no helper names the folder: refused, nothing ended",
          result.returncode == 1 and m.calls() == [] and stand_in.poll() is None)
    helper = {"ProcessId": 999999, "ParentProcessId": stand_in.pid, "ExecutablePath": r"C:\x\Claude.exe",
              "CommandLine": f'"C:\\x\\Claude.exe" --type=renderer --user-data-dir="{m.desktop}"'}
    processes = [{"ProcessId": stand_in.pid, "ParentProcessId": 1, "ExecutablePath": r"C:\x\Claude.exe",
                  "CommandLine": '"C:\\x\\Claude.exe"'}, helper]
    result = m.run("quit", "3", plan={"processes": processes, "inside": True, "end": dict(end, kills_on=[True])})
    check("quit from inside Desktop: refused, nothing ended",
          result.returncode == 1 and "by hand" in result.stderr and m.calls() == [] and stand_in.poll() is None)

    # A Desktop that stays in the tray when its windows close ends with its tree, then the helper left behind.
    result = m.run("quit", "3", plan={"processes": processes, "inside": False, "end": dict(end, kills_on=[True])})
    check("quit, tray: ended", result.returncode == 0 and stand_in.wait(timeout=10) is not None)
    check("quit, tray: windows asked first, then the tree, then the helper",
          m.calls() == [[stand_in.pid, False], [stand_in.pid, True], [999999, True]])
    # A Desktop that quits when its windows close is not forced.
    stand_in = m.hold("lock", m.desktop / "lockfile")
    processes[0]["ProcessId"] = helper["ParentProcessId"] = stand_in.pid
    result = m.run("quit", "3", plan={"processes": processes, "inside": False,
                                      "end": dict(end, stand_in=stand_in.pid, kills_on=[False])})
    check("quit, closes on its own: not forced",
          result.returncode == 0 and m.calls() == [[stand_in.pid, False], [999999, True]])
    # Its files still held by a process that outlives it: the quit says so.
    stand_in = m.hold("lock", m.desktop / "lockfile")
    lock = m.hold("exclusive", m.desktop / "Local Storage" / "leveldb" / "LOCK")
    processes[0]["ProcessId"] = helper["ParentProcessId"] = stand_in.pid
    result = m.run("quit", "3", plan={"processes": processes, "inside": False, "files_free_seconds": 1,
                                      "end": dict(end, stand_in=stand_in.pid, kills_on=[True])})
    check("quit, files still held: says which", result.returncode == 1 and "LOCK" in result.stderr)
    m.calls()
    lock.kill()
    lock.wait()
    check("a file nobody holds is free", json.loads(m.run(plan={"file_free": str(m.cookie_file())}).stdout))
    held = m.hold("exclusive", m.cookie_file())
    check("a file Desktop holds is not", not json.loads(m.run(plan={"file_free": str(m.cookie_file())}).stdout))
    held.kill()
    held.wait()
finally:
    try:
        m.stop()
    except NameError:
        pass
    shutil.rmtree(root, ignore_errors=True)

print(f"{passes} passed, {len(failures)} failed")
sys.exit(1 if failures else 0)
