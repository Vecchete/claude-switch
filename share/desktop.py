#!/usr/bin/env -S python3 -I
"""Claude Desktop follows claude-switch.

Claude Desktop keeps its sign-in in ~/.config/Claude, apart from Claude Code: the claude.ai session cookies
in Cookies, and in config.json the tokens of its Code tab (oauth:tokenCacheV2) and the account it saw last
(lastKnownAccountUuid). Those belong to the account, the rest of the folder to the machine. The cookies
and the tokens are encrypted with the one key Desktop keeps in the desktop keyring, the same for every
account, so a sign-in saved here goes back as it is and is never decrypted.

Desktop holds its cookies open and writes config.json as it quits, so it must be closed for a switch.
lastKnownAccountUuid always moves with the rest: when it names the account Desktop finds signed in,
Desktop carries on as before, and otherwise it takes the change for another person and resets
connectors and tool permissions (seen in Desktop 4.4212.0).

The open session is named by its cookies among the saved ones, else by lastKnownAccountUuid. A session
nobody can name is never saved or replaced. The saved sign-ins are in ~/.claude-accounts/.desktop, one
file per account, and only you can read them.

    desktop.py status            what Desktop is on, as JSON
    desktop.py switch ACCOUNT    save the sign-in under its account, put back the one of ACCOUNT
    desktop.py quit [SECONDS]    close Desktop and wait until it is gone, 20 seconds at most
    desktop.py start             start Desktop, without the environment of the calling session

switch exits 3 when Desktop is open and on another account, 4 when its session has no name.
"""
import base64
import contextlib
import json
import os
import pathlib
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import time

HOME = pathlib.Path.home()
DESKTOP = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "Claude"
COOKIES = DESKTOP / "Cookies"
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
RUNNING, UNNAMED, BROKEN = 3, 4, 5


class Refused(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def running():
    """The pid of the Desktop holding the folder, None when none does."""
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


def read_config():
    if not CONFIG.exists():
        return {}
    try:
        config = json.loads(CONFIG.read_text())
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
    uri = f"file:{COOKIES}?mode=ro&immutable=1" if readonly else f"file:{COOKIES}"
    with contextlib.closing(sqlite3.connect(uri, uri=True)) as db:
        db.row_factory = sqlite3.Row
        version = db.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()[0]
        clause, values = where()
        rows = db.execute(f"SELECT * FROM cookies WHERE {clause} ORDER BY name, host_key", values).fetchall()
    return [{key: encode(row[key]) for key in row.keys()} for row in rows], str(version)


def session_key(rows):
    for row in rows:
        if row["name"] in KEYS and (decode(row["encrypted_value"]) or row["value"]):
            return row["encrypted_value"], row["value"]
    return None


def saved(account):
    try:
        return json.loads((SAVED / f"{account}.json").read_text())
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
    with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
        handle.write(text)
    os.replace(temporary, path)


def labels():
    """The name or the email claude-switch shows for each account."""
    names = {}
    for path in STORE.glob("*.json"):
        try:
            account = json.loads(path.read_text())
            names[account["oauthAccount"]["accountUuid"]] = account.get("name") or account["oauthAccount"]["emailAddress"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return names


def save(rows, version, config, account):
    write_private(SAVED / f"{account}.json", json.dumps(
        {"account": account, "cookieVersion": version, "cookies": rows,
         "config": {key: config[key] for key in TOKENS if key in config}}))


def put_back(account, config, version):
    """Puts the sign-in saved for ACCOUNT in place. True when there was one."""
    copy = saved(account)
    rows = copy["cookies"] if copy and copy.get("cookieVersion") == version else []
    for key in TOKENS:
        config.pop(key, None)
    if rows:
        config.update(copy.get("config", {}))
    config[ACCOUNT] = account
    clause, values = where()
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
        with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
            handle.write(json.dumps(config, indent="\t"))
        # A stop between the two writes would leave the cookies of one account under the name of the
        # other, which Desktop takes for another person, so it waits until both are done.
        stops = {signal.SIGTERM, signal.SIGINT, signal.SIGHUP}
        signal.pthread_sigmask(signal.SIG_BLOCK, stops)
        try:
            db.commit()
            os.replace(temporary, CONFIG)
        finally:
            signal.pthread_sigmask(signal.SIG_UNBLOCK, stops)
    return bool(rows)


def switch(account):
    if not UUID.fullmatch(account):
        raise Refused(2, f"{account} is not an account id.")
    if not DESKTOP.is_dir() or not COOKIES.exists():
        return ""
    who = labels().get(account, account)
    config = read_config()
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
    rows, version = read_session(readonly=False)
    current = name(rows, config)
    if current is not None:
        save(rows, version, config, current)
    if put_back(account, config, version):
        return f"Claude Desktop is on {who}."
    return f"Claude Desktop is signed out: sign in to it once as {who}, it follows the switch from then on."


def status():
    config = read_config() if CONFIG.exists() else {}
    rows, _ = read_session()
    try:
        account = name(rows, config)
    except Refused:
        account = "unknown"
    return {"installed": DESKTOP.is_dir(), "running": running() is not None, "signedIn": session_key(rows) is not None,
            "account": account}


def quit_desktop(seconds):
    pid = running()
    if pid is None:
        return 0
    if pid < 0:
        print("Claude Desktop is open on another computer.", file=sys.stderr)
        return 1
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + seconds
    while running() is not None:
        if time.monotonic() > deadline:
            print(f"Claude Desktop is still open after {seconds:g} seconds.", file=sys.stderr)
            return 1
        time.sleep(0.2)
    return 0


def start():
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("CLAUDE", "ANTHROPIC"))}
    subprocess.Popen(["claude-desktop"], env=environment, start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=HOME)
    return 0


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
