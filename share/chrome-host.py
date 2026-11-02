#!/usr/bin/env -S python3 -I
"""claude.ai in Chrome follows claude-switch.

Chrome starts this file as the native messaging host of the companion extension in
~/.local/share/claude-switch/chrome-extension, and it runs as long as Chrome does. claude-switch
runs it with --switch ACCOUNT: it saves the claude.ai session open in Chrome under its account and
puts back the one saved for ACCOUNT. Nobody logs out, so every saved session stays valid on the
server. While the cookies are swapped the extension blocks claude.ai, because a page that sends a half
swapped session gets it revoked (seen 2026-10-30: account_session_invalid).

A session belongs to an account, the accountUuid claude-switch keeps, and stays the same when the
account moves to another of its organizations on claude.ai. The open session is recognized among the
saved ones, or else by claude.ai's /api/account, the only place it is sent. When neither can tell,
nothing changes: a session nobody can name is never saved over another or thrown away.

The sessions are in ~/.claude-accounts/.web, one file per account. They are logins, so only you can
read them. The extension is meant for one Chrome profile: a second profile or browser that loads it
takes the switch over from the first.

    chrome-host.py --switch ACCOUNT   put claude.ai in Chrome on ACCOUNT, an accountUuid
    chrome-host.py --status           print the account open in Chrome and the saved ones
"""

import http.client
import itertools
import json
import os
import pathlib
import queue
import re
import socket
import struct
import sys
import threading
import time
import urllib.error
import urllib.request

STORE = pathlib.Path.home() / ".claude-accounts"
WEB = STORE / ".web"
SOCKET = STORE / ".chrome.sock"
PENDING = STORE / ".chrome-pending"

# Tests point this at a fake claude.ai on this machine. Nothing else is accepted: a session goes to
# claude.ai and nowhere else.
CLAUDE_AI = os.environ.get("CLAUDE_SWITCH_CLAUDE_AI", "")
if not re.fullmatch(r"http://127\.0\.0\.1:[0-9]+", CLAUDE_AI):
    CLAUDE_AI = "https://claude.ai"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"

# What a claude.ai sign-in writes, seen in Chrome Beta on 2026-10-30: the session and the active
# organization. With them goes the cookie claude.ai sets when it registers the browser for the Remote
# Control of the account (2026-11-02), so each account finds its own registration again when it comes
# back. The device id, analytics and the look of the page stay with the browser.
SESSION = ["sessionKey", "sessionKeyLC", "routingHint", "lastActiveOrg", "__Host-ant_trusted_device"]
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

# A switch made while Chrome was closed waits for claude.ai, which may be unreachable right after a boot.
PENDING_TRIES = 20
PENDING_PAUSE = 30

replies = {}
numbers = itertools.count(1)
write_lock = threading.Lock()
# One switch at a time, whether it comes from claude-switch or from the pending file.
switching = threading.Lock()
# The socket this host made. A newer host of the same Chrome replaces it, and keeps it.
own_socket = None


def send(message):
    data = json.dumps(message).encode()
    with write_lock:
        sys.stdout.buffer.write(struct.pack("@I", len(data)) + data)
        sys.stdout.buffer.flush()


def release_socket():
    try:
        if own_socket is not None and SOCKET.stat().st_ino == own_socket:
            SOCKET.unlink()
    except OSError:
        pass


def listen():
    """Hands each answer of the extension to the request waiting for it. Chrome closing the port, or anything
    on it the host can't read, ends the host: the extension starts a new one, never a host that stays deaf."""
    try:
        while True:
            header = sys.stdin.buffer.read(4)
            if len(header) < 4:
                return
            message = json.loads(sys.stdin.buffer.read(struct.unpack("@I", header)[0]))
            waiting = replies.pop(message.get("id"), None)
            if waiting:
                waiting.put(message)
    finally:
        release_socket()
        os._exit(0)


def ask(command, **fields):
    number = next(numbers)
    answer = queue.Queue()
    replies[number] = answer
    send({"id": number, "cmd": command, **fields})
    message = answer.get(timeout=30)
    if "error" in message:
        raise RuntimeError(message["error"])
    return message


def value(cookies, name):
    return next((cookie["value"] for cookie in cookies if cookie["name"] == name), None)


def saved_path(account):
    return WEB / f"{account}.json"


def save(account, cookies):
    WEB.mkdir(mode=0o700, exist_ok=True)
    temporary = WEB / f"{account}.tmp"
    temporary.write_text(json.dumps({"account": account, "cookies": cookies}))
    temporary.replace(saved_path(account))


def load(account):
    """The session saved for ACCOUNT, None when there is none. An expired one is removed, and a cookie of it
    that ran out on its own, a Remote Control registration for instance, is left out: Chrome would not
    keep it."""
    try:
        cookies = json.loads(saved_path(account).read_text())["cookies"]
        key = next(cookie for cookie in cookies if cookie["name"] == "sessionKey")
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return None
    if not key.get("session") and key.get("expirationDate", 0) < time.time():
        saved_path(account).unlink(missing_ok=True)
        return None
    return [cookie for cookie in cookies if cookie.get("session") or cookie.get("expirationDate", 0) > time.time()]


def saved_sessions():
    """Each saved session as (account, cookies)."""
    for path in WEB.glob("*.json"):
        try:
            data = json.loads(path.read_text())
            yield data["account"], data["cookies"]
        except (OSError, ValueError, KeyError, TypeError):
            continue


def dead_session(error):
    """True when claude.ai refused the session itself: account_session_invalid, seen on 2026-10-30."""
    try:
        if "json" not in (error.headers or {}).get("Content-Type", ""):
            return False
        return json.loads(error.read())["error"]["details"]["error_code"] == "account_session_invalid"
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
        return False


def claude_ai_account(cookies):
    """The account claude.ai says the session belongs to, None when it no longer accepts the session."""
    request = urllib.request.Request(f"{CLAUDE_AI}/api/account", headers={
        "Cookie": "; ".join(f"{cookie['name']}={cookie['value']}" for cookie in cookies),
        "Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as error:
        # Only that refusal means signed out. Any other, or a Cloudflare page, says nothing about the session.
        if error.code in (401, 403) and dead_session(error):
            return None
        raise RuntimeError(f"could not tell which account is open in Chrome, claude.ai answered {error.code}") from None
    except (OSError, ValueError, http.client.HTTPException) as error:
        raise RuntimeError(f"could not tell which account is open in Chrome: {error}") from None
    account = data.get("uuid") if isinstance(data, dict) else None
    if not isinstance(account, str) or not UUID.fullmatch(account):
        raise RuntimeError("could not tell which account is open in Chrome, claude.ai named none")
    return account


def identify(cookies):
    """The account of the session in COOKIES, None when it is signed out."""
    cookies = [cookie for cookie in cookies if cookie["name"] in SESSION]
    key = value(cookies, "sessionKey")
    if not key:
        return None
    for account, saved in saved_sessions():
        if value(saved, "sessionKey") == key:
            return account
    return claude_ai_account(cookies)


def switch(account):
    """Puts claude.ai on ACCOUNT. Returns already, switched or signed-out."""
    if not UUID.fullmatch(account):
        raise ValueError(f"not an account: {account}")
    # Named before the block, since claude.ai may take a moment to answer.
    before = [cookie for cookie in ask("get")["cookies"] if cookie["name"] in SESSION]
    current = identify(before)
    if current == account:
        return "already"
    restored = load(account)
    try:
        # From here claude.ai is blocked until the extension put the other session in place.
        cookies = ask("capture", names=SESSION)["cookies"]
        if value(cookies, "sessionKey") != value(before, "sessionKey"):
            raise RuntimeError("the claude.ai session in Chrome changed during the switch, run it again")
        if current:
            save(current, cookies)
        ask("apply", remove=cookies, add=restored or [])
    finally:
        try:
            ask("release")
        except (OSError, RuntimeError, queue.Empty):
            pass
    return "switched" if restored else "signed-out"


def status():
    reply = {"saved": sorted(account for account, _ in saved_sessions())}
    try:
        reply["current"] = identify(ask("get")["cookies"])
    except RuntimeError as error:
        reply["unknown"] = str(error)
    return reply


def migrate():
    """Version 1.1 named a session by its organization. Each one moves to its account."""
    accounts = {}
    for path in STORE.glob("*.json"):
        try:
            account = json.loads(path.read_text())["oauthAccount"]
            accounts.setdefault(account["organizationUuid"], set()).add(account["accountUuid"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
    for path in WEB.glob("*.json"):
        try:
            cookies = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        owners = accounts.get(path.stem, set())
        # An organization of several accounts here can't tell whose session it was: it stays as it is.
        if not isinstance(cookies, list) or len(owners) != 1:
            continue
        account = owners.pop()
        if not saved_path(account).exists():
            save(account, cookies)
        path.unlink()


def follow_pending():
    """A block a stopped worker left on claude.ai, then a switch made while Chrome was closed. A switch
    asked since replaces it."""
    with switching:
        try:
            ask("release")
        except (OSError, RuntimeError, queue.Empty):
            pass
    for _ in range(PENDING_TRIES):
        with switching:
            try:
                account = PENDING.read_text().strip()
            except FileNotFoundError:
                return
            try:
                switch(account)
                PENDING.unlink(missing_ok=True)
                return
            except ValueError as error:
                PENDING.unlink(missing_ok=True)
                print(f"claude-switch chrome host: {error}", file=sys.stderr)
                return
            except (OSError, RuntimeError, queue.Empty) as error:
                print(f"claude-switch chrome host: {error}", file=sys.stderr)
        time.sleep(PENDING_PAUSE)


def bind():
    global own_socket
    SOCKET.unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(SOCKET))
    own_socket = SOCKET.stat().st_ino
    server.listen(4)
    server.settimeout(5)
    threading.Thread(target=follow_pending, daemon=True).start()
    return server


def serve():
    os.umask(0o077)
    STORE.mkdir(mode=0o700, exist_ok=True)
    migrate()
    threading.Thread(target=listen, daemon=True).start()
    server = bind()
    while True:
        try:
            connection, _ = server.accept()
        except socket.timeout:
            # A host of another profile took the socket over and left: this one serves again.
            if not SOCKET.exists():
                server.close()
                server = bind()
            continue
        with connection:
            # Whatever goes wrong with one request, the host keeps serving the next.
            try:
                connection.settimeout(10)
                request = json.loads(connection.makefile().readline())
                connection.settimeout(None)
                with switching:
                    if "switch" in request:
                        PENDING.unlink(missing_ok=True)
                        reply = {"result": switch(request["switch"])}
                    else:
                        reply = status()
            except Exception as error:
                reply = {"result": "error", "detail": str(error) or type(error).__name__}
            try:
                connection.sendall(json.dumps(reply).encode() + b"\n")
            except OSError:
                pass


def request(message):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(90)
        connection.connect(str(SOCKET))
        connection.sendall(json.dumps(message).encode() + b"\n")
        return json.loads(connection.makefile().readline())


def labels():
    """The name or the email claude-switch shows for each account."""
    names = {}
    for path in STORE.glob("*.json"):
        try:
            account = json.loads(path.read_text())
            names[account["oauthAccount"]["accountUuid"]] = (
                account.get("name") or account["oauthAccount"]["emailAddress"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return names


def main(argv):
    if argv[1:2] == ["--switch"] and len(argv) == 3:
        account = argv[2]
        if not UUID.fullmatch(account):
            print(f"claude.ai in Chrome: {account} is not an account.", file=sys.stderr)
            return 1
        label = labels().get(account, account)
        try:
            reply = request({"switch": account})
        except (FileNotFoundError, ConnectionRefusedError) as error:
            # Nobody listens on a socket a killed host left: a host still running takes it back.
            if isinstance(error, ConnectionRefusedError):
                SOCKET.unlink(missing_ok=True)
            os.umask(0o077)
            PENDING.write_text(account + "\n")
            print(f"claude.ai in Chrome: the companion is not running, it opens {label} when it starts.")
            return 0
        except (OSError, ValueError) as error:
            print(f"claude.ai in Chrome did not follow: {error}", file=sys.stderr)
            return 1
        messages = {
            "already": f"claude.ai in Chrome: already on {label}.",
            "switched": f"claude.ai in Chrome: switched to {label}, its tabs reloaded.",
            "signed-out": f"claude.ai in Chrome: no session saved for {label} yet, sign in there once.",
        }
        if reply.get("result") in messages:
            print(messages[reply["result"]])
            return 0
        print(f"claude.ai in Chrome did not follow: {reply.get('detail')}", file=sys.stderr)
        return 1
    if argv[1:] == ["--status"]:
        try:
            reply = request({"status": True})
        except (OSError, ValueError):
            print("claude.ai in Chrome: the companion is not running (Chrome closed, or the extension not loaded).")
            return 0
        if reply.get("result") == "error":
            print(f"claude.ai in Chrome: {reply.get('detail')}", file=sys.stderr)
            return 1
        names = labels()
        current = reply.get("current")
        if "unknown" in reply:
            print(f"claude.ai in Chrome: signed in, but {reply['unknown']}")
        else:
            print(f"claude.ai in Chrome: {names.get(current, current) if current else 'signed out'}")
        print("saved sessions: " + (", ".join(names.get(a, a) for a in reply.get("saved", [])) or "none"))
        return 0
    if argv[1:2] and argv[1].startswith("chrome-extension://"):
        return serve()
    print("Usage: chrome-host.py --switch ACCOUNT | --status", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
