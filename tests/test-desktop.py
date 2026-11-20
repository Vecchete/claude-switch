#!/usr/bin/env python3
"""Tests of desktop.py on a scratch HOME: a Claude Desktop folder with a cookie database and config.json,
fake Desktop processes, no keyring. Usage: test-desktop.py DESKTOP_PY"""
import base64
import json
import os
import pathlib
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

HELPER = pathlib.Path(sys.argv[1]).resolve()
ACCOUNT_A = "aaaaaaaa-0000-4000-8000-0000000000a1"
ACCOUNT_B = "bbbbbbbb-0000-4000-8000-0000000000b1"
ACCOUNT_C = "cccccccc-0000-4000-8000-0000000000c1"
SESSION = ("sessionKey", "sessionKeyLC", "routingHint", "lastActiveOrg")
# Set when claude.ai registers Desktop for the Remote Control of an account, seen 2026-11-02.
TRUSTED = "__Host-ant_trusted_device"
COLUMNS = ["creation_utc", "host_key", "top_frame_site_key", "name", "value", "encrypted_value", "path", "expires_utc",
           "is_secure", "is_httponly", "last_access_utc", "has_expires", "is_persistent", "priority", "samesite",
           "source_scheme", "source_port", "last_update_utc", "source_type", "has_cross_site_ancestor"]
failures = []
passes = 0


def check(name, condition):
    global passes
    if condition:
        passes += 1
    else:
        failures.append(name)
        print("FAIL", name)


class Machine:
    def __init__(self, root):
        self.home = root / "home"
        self.desktop = self.home / ".config/Claude"
        self.store = self.home / ".claude-accounts"
        self.bin = root / "bin"
        self.desktop.mkdir(parents=True)
        self.store.mkdir(mode=0o700)
        self.bin.mkdir()
        self.processes = []
        for account, email, name in ((ACCOUNT_A, "a@example.com", "first"), (ACCOUNT_B, "b@example.com", "")):
            (self.store / f"{email}_x.json").write_text(json.dumps(
                {"name": name, "oauthAccount": {"accountUuid": account, "emailAddress": email}, "login": {}}))
        with sqlite3.connect(self.desktop / "Cookies") as db:
            db.execute("CREATE TABLE meta(key LONGVARCHAR NOT NULL UNIQUE PRIMARY KEY, value LONGVARCHAR)")
            db.execute("INSERT INTO meta VALUES ('version', '24'), ('last_compatible_version', '24')")
            db.execute("CREATE TABLE cookies(creation_utc INTEGER NOT NULL, host_key TEXT NOT NULL, top_frame_site_key "
                       "TEXT NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL, encrypted_value BLOB NOT NULL, path TEXT "
                       "NOT NULL, expires_utc INTEGER NOT NULL, is_secure INTEGER NOT NULL, is_httponly INTEGER NOT NULL, "
                       "last_access_utc INTEGER NOT NULL, has_expires INTEGER NOT NULL, is_persistent INTEGER NOT NULL, "
                       "priority INTEGER NOT NULL, samesite INTEGER NOT NULL, source_scheme INTEGER NOT NULL, source_port "
                       "INTEGER NOT NULL, last_update_utc INTEGER NOT NULL, source_type INTEGER NOT NULL, "
                       "has_cross_site_ancestor INTEGER NOT NULL)")
            db.execute("CREATE UNIQUE INDEX cookies_unique_index ON cookies(host_key, top_frame_site_key, "
                       "has_cross_site_ancestor, name, path, source_scheme, source_port)")
        self.add_cookie(".claude.ai", "anthropic-device-id", b"device")
        self.add_cookie(".google.com", "NID", b"google")
        self.write_config({"locale": "en-US", "userThemeMode": "dark", "coworkTrustedDeviceToken:x": "keep"})

    def add_cookie(self, host, name, blob, creation=None):
        with sqlite3.connect(self.desktop / "Cookies") as db:
            db.execute(f"INSERT INTO cookies({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
                       (creation or time.time_ns() // 1000, host, "", name, "", blob, "/", 13_500_000_000_000_000, 1, 1,
                        13_400_000_000_000_000, 1, 1, 1, 1, 2, 443, 13_400_000_000_000_000, 0, 0))

    def sign_in(self, account, tag):
        """What Desktop leaves after a sign-in to ACCOUNT: the session cookies, the token cache, the account."""
        with sqlite3.connect(self.desktop / "Cookies") as db:
            db.execute("DELETE FROM cookies WHERE host_key = '.claude.ai' AND name IN (?, ?, ?, ?)", SESSION)
        for name in SESSION:
            self.add_cookie(".claude.ai", name, f"v11-{tag}-{name}".encode())
        config = self.config()
        config.update({"oauth:tokenCacheV2": f"djEx-{tag}", "lastKnownAccountUuid": account})
        self.write_config(config)

    def register(self, tag):
        """What Desktop keeps after it registered for the Remote Control of an account: a host-only cookie."""
        with sqlite3.connect(self.desktop / "Cookies") as db:
            db.execute("DELETE FROM cookies WHERE host_key = 'claude.ai' AND name = ?", (TRUSTED,))
        self.add_cookie("claude.ai", TRUSTED, f"v11-{tag}-trusted".encode())

    def trusted(self):
        rows = [row for row in self.cookies("claude.ai") if row[3] == TRUSTED]
        return rows[0][5] if rows else None

    def sign_out(self):
        with sqlite3.connect(self.desktop / "Cookies") as db:
            db.execute("DELETE FROM cookies WHERE host_key = '.claude.ai' AND name IN (?, ?, ?, ?)", SESSION)
        config = self.config()
        config.pop("oauth:tokenCacheV2", None)
        self.write_config(config)

    def cookies(self, host=".claude.ai"):
        with sqlite3.connect(self.desktop / "Cookies") as db:
            rows = db.execute(f"SELECT {', '.join(COLUMNS)} FROM cookies WHERE host_key = ? ORDER BY name", (host,))
            return [tuple(row) for row in rows]

    def session(self):
        return {row[3]: row[5] for row in self.cookies() if row[3] in SESSION}

    def config(self):
        return json.loads((self.desktop / "config.json").read_text())

    def write_config(self, config):
        (self.desktop / "config.json").write_text(json.dumps(config, indent="\t"))

    def snapshot(self):
        return (self.desktop / "Cookies").read_bytes(), (self.desktop / "config.json").read_bytes(), sorted(
            (path.name, path.read_bytes()) for path in (self.store / ".desktop").glob("*.json"))

    def saved(self, account):
        path = self.store / ".desktop" / f"{account}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def env(self, **extra):
        return {"HOME": str(self.home), "PATH": f"{self.bin}:{os.environ['PATH']}", **extra}

    def run(self, *args, **extra):
        return subprocess.run([sys.executable, "-I", str(HELPER), *args], capture_output=True, text=True,
                              env=self.env(**extra), timeout=60)

    def fake_desktop(self, ignore_term=False):
        """A process that looks like Claude Desktop, holding the folder's single-instance lock."""
        trap = "trap '' TERM; " if ignore_term else ""
        process = subprocess.Popen(["bash", "-c", f"{trap}exec -a claude-desktop sleep 300"])
        self.processes.append(process)
        time.sleep(0.2)
        lock = self.desktop / "SingletonLock"
        lock.unlink(missing_ok=True)
        lock.symlink_to(f"{socket.gethostname()}-{process.pid}")
        return process

    def stop(self):
        for process in self.processes:
            process.kill()
            process.wait()
        self.processes.clear()
        (self.desktop / "SingletonLock").unlink(missing_ok=True)


with tempfile.TemporaryDirectory(prefix="claude-switch-desktop.") as folder:
    root = pathlib.Path(folder)
    m = Machine(root)
    other_cookies = m.cookies(".google.com"), [row for row in m.cookies() if row[3] == "anthropic-device-id"]

    status = json.loads(m.run("status").stdout)
    check("status: installed, closed, signed out", status["installed"] and not status["running"]
          and not status["signedIn"] and status["account"] is None)

    m.sign_in(ACCOUNT_A, "A")
    m.register("A")
    a_rows = m.cookies()
    a_trusted = [row for row in m.cookies("claude.ai") if row[3] == TRUSTED]
    status = json.loads(m.run("status").stdout)
    check("status: signed in to A", status["signedIn"] and status["account"] == ACCOUNT_A)

    result = m.run("switch", ACCOUNT_B)
    check("first switch to B: succeeds", result.returncode == 0)
    check("first switch to B: A is saved", m.saved(ACCOUNT_A) is not None)
    check("first switch to B: the saved file is private",
          oct((m.store / ".desktop" / f"{ACCOUNT_A}.json").stat().st_mode)[-3:] == "600"
          and oct((m.store / ".desktop").stat().st_mode)[-3:] == "700")
    check("first switch to B: no session left", m.session() == {})
    check("first switch to B: no token cache left", "oauth:tokenCacheV2" not in m.config())
    check("first switch to B: Desktop expects B", m.config()["lastKnownAccountUuid"] == ACCOUNT_B)
    check("first switch to B: asks for one sign-in", "sign in" in result.stdout and "b@example.com" in result.stdout)
    check("first switch to B: the rest of config.json stays",
          m.config()["userThemeMode"] == "dark" and m.config()["coworkTrustedDeviceToken:x"] == "keep")
    check("first switch to B: other cookies stay",
          (m.cookies(".google.com"), [row for row in m.cookies() if row[3] == "anthropic-device-id"]) == other_cookies)
    check("first switch to B: A's trusted device saved with A",
          any(row["name"] == TRUSTED for row in m.saved(ACCOUNT_A)["cookies"]))
    check("first switch to B: A's trusted device not left to B", m.trusted() is None)

    m.sign_in(ACCOUNT_B, "B")
    m.register("B")
    b_rows = m.cookies()
    result = m.run("switch", ACCOUNT_A)
    check("back to A: succeeds", result.returncode == 0 and "first" in result.stdout)
    check("back to A: its cookies come back whole", m.cookies() == a_rows)
    check("back to A: its token cache comes back", m.config()["oauth:tokenCacheV2"] == "djEx-A")
    check("back to A: Desktop expects A", m.config()["lastKnownAccountUuid"] == ACCOUNT_A)
    check("back to A: B is saved", m.saved(ACCOUNT_B)["config"]["oauth:tokenCacheV2"] == "djEx-B")
    check("back to A: its trusted device comes back", m.trusted() == b"v11-A-trusted")
    check("back to A: B's trusted device saved with B",
          any(row["name"] == TRUSTED for row in m.saved(ACCOUNT_B)["cookies"]))
    result = m.run("switch", ACCOUNT_B)
    check("to B again: its cookies come back whole", m.cookies() == b_rows)
    check("to B again: its trusted device comes back", m.trusted() == b"v11-B-trusted")
    check("to B again: its token cache comes back", m.config()["oauth:tokenCacheV2"] == "djEx-B")

    # Desktop open: nothing is touched, unless it is already on the account.
    m.fake_desktop()
    before = m.snapshot()
    result = m.run("switch", ACCOUNT_A)
    check("open Desktop: refused with 3", result.returncode == 3)
    check("open Desktop: nothing changed", m.snapshot() == before)
    result = m.run("switch", ACCOUNT_B)
    check("open Desktop already on B: fine", result.returncode == 0 and "already" in result.stdout)
    check("open Desktop already on B: nothing changed", m.snapshot() == before)
    check("status: open", json.loads(m.run("status").stdout)["running"])
    m.stop()

    # A lock left by a Desktop that is gone, or a process that only got its number.
    (m.desktop / "SingletonLock").symlink_to(f"{socket.gethostname()}-{os.getpid()}")
    result = m.run("switch", ACCOUNT_A)
    check("a stale lock: switched anyway", result.returncode == 0 and m.cookies() == a_rows)
    (m.desktop / "SingletonLock").unlink()

    # The open session is named by its saved cookies before lastKnownAccountUuid.
    config = m.config()
    config["lastKnownAccountUuid"] = ACCOUNT_B
    m.write_config(config)
    saved_b = (m.store / ".desktop" / f"{ACCOUNT_B}.json").read_bytes()
    m.run("switch", ACCOUNT_B)
    check("saved cookies name the session: B's copy untouched until saved as B",
          json.loads(saved_b)["cookies"] == m.saved(ACCOUNT_B)["cookies"] and m.cookies() == b_rows)
    check("saved cookies name the session: A saved as A", sorted(tuple(row[c] for c in COLUMNS[:5])
          for row in m.saved(ACCOUNT_A)["cookies"]) == sorted(row[:5] for row in a_rows + a_trusted
                                                                if row[3] in SESSION + (TRUSTED,)))

    # Signed in to an account nobody can name: nothing changes.
    m.sign_in(ACCOUNT_C, "C")
    config = m.config()
    del config["lastKnownAccountUuid"]
    m.write_config(config)
    before = m.snapshot()
    result = m.run("switch", ACCOUNT_A)
    check("unnamed session: refused with 4", result.returncode == 4)
    check("unnamed session: nothing changed", m.snapshot() == before)

    # An account Desktop is on but claude-switch doesn't know is still saved, under its uuid.
    config["lastKnownAccountUuid"] = ACCOUNT_C
    m.write_config(config)
    result = m.run("switch", ACCOUNT_A)
    check("other account: saved under its uuid", m.saved(ACCOUNT_C) is not None and m.cookies() == a_rows)

    # Signed out in Desktop: nothing is saved over the account's copy, and the target comes back.
    m.sign_out()
    saved_a = m.saved(ACCOUNT_A)
    result = m.run("switch", ACCOUNT_B)
    check("signed out: the copy of A stays", m.saved(ACCOUNT_A) == saved_a)
    check("signed out: B comes back", m.cookies() == b_rows)
    m.sign_out()
    result = m.run("switch", ACCOUNT_B)
    check("signed out on the target: its copy comes back", result.returncode == 0 and m.cookies() == b_rows)

    # Cookies saved by another version of the cookie database are not put back.
    saved = m.saved(ACCOUNT_A)
    saved["cookieVersion"] = "23"
    (m.store / ".desktop" / f"{ACCOUNT_A}.json").write_text(json.dumps(saved))
    result = m.run("switch", ACCOUNT_A)
    check("other cookie version: not put back", m.session() == {} and "sign in" in result.stdout)
    check("other cookie version: Desktop expects A", m.config()["lastKnownAccountUuid"] == ACCOUNT_A)

    # A broken config.json is left to Desktop, which moves it aside.
    m.sign_in(ACCOUNT_A, "A")
    (m.desktop / "config.json").write_text("{not json")
    before = m.snapshot()
    result = m.run("switch", ACCOUNT_B)
    check("broken config: refused", result.returncode not in (0, 3, 4))
    check("broken config: nothing changed", m.snapshot() == before)
    m.write_config({"locale": "en-US", "lastKnownAccountUuid": ACCOUNT_A, "oauth:tokenCacheV2": "djEx-A"})

    # A journal Desktop left behind would be replayed over the switch at its next start.
    (m.desktop / "config.json.journal").write_text("unfinished")
    before = m.snapshot()
    result = m.run("switch", ACCOUNT_B)
    check("leftover journal: refused", result.returncode not in (0, 3, 4) and "quit it once" in result.stderr)
    check("leftover journal: nothing changed", m.snapshot() == before)
    (m.desktop / "config.json.journal").unlink()

    # A stop between the two writes: the cookies and config.json still name one account.
    stopper = root / "stop-between.py"
    stopper.write_text(
        "import importlib.util, os, signal, sys\n"
        "spec = importlib.util.spec_from_file_location('desktop', sys.argv[1])\n"
        "desktop = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(desktop)\n"
        "real = os.replace\n"
        "def replace(a, b):\n"
        "    if str(b).endswith('config.json'):\n"
        "        os.kill(os.getpid(), signal.SIGTERM)\n"
        "    return real(a, b)\n"
        "os.replace = replace\n"
        "sys.exit(desktop.main(sys.argv[2:]))\n")
    subprocess.run([sys.executable, "-I", str(stopper), str(HELPER), "switch", ACCOUNT_B], env=m.env(),
                   capture_output=True, timeout=60)
    session, expected = m.session(), m.config()["lastKnownAccountUuid"]
    check("stopped mid-switch: cookies and config name one account",
          (expected == ACCOUNT_B and session.get("sessionKey") == b"v11-B-sessionKey")
          or (expected == ACCOUNT_A and session.get("sessionKey") == b"v11-A-sessionKey"))

    # Desktop started by hand while the switch writes: the switch gives way.
    starter = root / "start-during.py"
    starter.write_text(
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('desktop', sys.argv[1])\n"
        "desktop = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(desktop)\n"
        "calls = []\n"
        "def running():\n"
        "    calls.append(1)\n"
        "    return None if len(calls) == 1 else 4242\n"
        "desktop.running = running\n"
        "sys.exit(desktop.main(sys.argv[2:]))\n")
    m.stop()
    m.sign_in(ACCOUNT_A, "A")
    before = m.snapshot()
    result = subprocess.run([sys.executable, "-I", str(starter), str(HELPER), "switch", ACCOUNT_B], env=m.env(),
                            capture_output=True, text=True, timeout=60)
    check("started during the switch: refused", result.returncode == 3)
    check("started during the switch: Desktop's files unchanged", m.snapshot()[:2] == before[:2])

    # Closing and starting Desktop.
    m.stop()
    process = m.fake_desktop()
    result = m.run("quit", "5")
    check("quit: Desktop is gone", result.returncode == 0 and process.wait(timeout=5) is not None)
    m.stop()
    process = m.fake_desktop(ignore_term=True)
    result = m.run("quit", "1")
    check("quit: a Desktop that stays is reported", result.returncode != 0 and process.poll() is None)
    m.stop()
    (m.bin / "claude-desktop").write_text("#!/bin/sh\nenv > \"$HOME/started\"\n")
    (m.bin / "claude-desktop").chmod(0o755)
    result = m.run("start", CLAUDECODE="1", ANTHROPIC_API_KEY="key", CLAUDE_CODE_OAUTH_TOKEN="token")
    for _ in range(50):
        if (m.home / "started").exists():
            break
        time.sleep(0.1)
    started = (m.home / "started").read_text() if (m.home / "started").exists() else ""
    check("start: Desktop is started", result.returncode == 0 and "HOME=" in started)
    check("start: nothing of the calling session goes with it",
          "CLAUDECODE" not in started and "ANTHROPIC_API_KEY" not in started and "CLAUDE_CODE_OAUTH_TOKEN" not in started)
    check("start: without the user's systemd, Desktop is started as it is", not (m.home / "scoped").exists())
    # A service started by a timer takes what it started along when it ends: Desktop gets a scope of its own.
    (m.home / "started").unlink()
    runtime = root / "runtime"
    (runtime / "systemd").mkdir(parents=True)
    (runtime / "systemd" / "private").write_text("")
    (m.bin / "systemd-run").write_text('#!/bin/sh\nprintf "%s " "$@" > "$HOME/scoped"\nwhile [ "$1" != -- ]; do shift; '
                                       'done\nshift\nexec "$@"\n')
    (m.bin / "systemd-run").chmod(0o755)
    result = m.run("start", XDG_RUNTIME_DIR=str(runtime))
    for _ in range(50):
        if (m.home / "started").exists():
            break
        time.sleep(0.1)
    scoped = (m.home / "scoped").read_text() if (m.home / "scoped").exists() else ""
    check("start: in a scope of its own", result.returncode == 0 and scoped.startswith("--user --scope ")
          and scoped.rstrip().endswith("-- claude-desktop"))
    check("start: and started", (m.home / "started").exists())

    # No Desktop here: nothing to do.
    empty = root / "empty"
    (empty / "home").mkdir(parents=True)
    result = subprocess.run([sys.executable, "-I", str(HELPER), "switch", ACCOUNT_A], capture_output=True, text=True,
                            env={"HOME": str(empty / "home"), "PATH": os.environ["PATH"]}, timeout=60)
    check("no Desktop: nothing to do", result.returncode == 0 and not (empty / "home/.claude-accounts").exists())
    m.stop()

print(f"{passes} passed, {len(failures)} failed")
sys.exit(1 if failures else 0)
