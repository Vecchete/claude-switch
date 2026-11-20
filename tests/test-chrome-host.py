#!/usr/bin/env python3
"""Tests of chrome-host.py with a fake extension, a fake claude.ai and a cookie jar, on a scratch HOME.
Usage: test-chrome-host.py HOST"""
import http.server
import json
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time

HOST = pathlib.Path(sys.argv[1]).resolve()
ACCOUNT_A = "aaaaaaaa-0000-4000-8000-0000000000a1"
ACCOUNT_B = "bbbbbbbb-0000-4000-8000-0000000000b1"
ACCOUNT_C = "cccccccc-0000-4000-8000-0000000000c1"
ORG_A = "aaaaaaaa-0000-4000-8000-00000000000a"
ORG_B = "bbbbbbbb-0000-4000-8000-00000000000b"
ORG_B2 = "bbbbbbbb-0000-4000-8000-0000000000b2"
# Set when claude.ai registers the browser for the Remote Control of an account, seen 2026-11-02.
DEVICE = "__Host-ant_trusted_device"
SESSION = {"sessionKey", "sessionKeyLC", "routingHint", "lastActiveOrg", DEVICE}
failures = []
passes = 0


def check(name, condition):
    global passes
    if condition:
        passes += 1
    else:
        failures.append(name)
        print("FAIL", name)


def cookie(name, value, domain=".claude.ai", host_only=False, http_only=True, expires=4102444800):
    return {"name": name, "value": value, "domain": domain, "path": "/", "hostOnly": host_only,
            "httpOnly": http_only, "secure": True, "sameSite": "lax", "session": False,
            "expirationDate": expires, "storeId": "0"}


def web(letter, org):
    return [cookie("sessionKey", f"session-{letter}"), cookie("sessionKeyLC", f"lc-{letter}", http_only=False),
            cookie("lastActiveOrg", org, http_only=False), cookie("routingHint", f"route-{letter}")]


def trusted(letter, expires=4102444800):
    return cookie(DEVICE, f"trusted-{letter}", domain="claude.ai", host_only=True, expires=expires)


# What stays with the browser: Cloudflare, the device id, analytics, the look of the page, and a cookie
# claude.ai may add later.
BROWSER_SIDE = [cookie("cf_clearance", "cloudflare"), cookie("user-sidebar-pinned", "true", http_only=False),
                cookie("anthropic-device-id", "device", domain="claude.ai", host_only=True, http_only=False),
                cookie("ion-vk", "vk", domain="claude.ai", host_only=True),
                cookie("newCookie", "new")]


class ClaudeAi(http.server.BaseHTTPRequestHandler):
    """claude.ai's /api/account: the account of a session, 403 for one it does not know, 503 when down."""
    accounts = {"session-a": ACCOUNT_A, "session-b": ACCOUNT_B, "session-c": ACCOUNT_C}
    up = True
    cloudflare = False
    # A JSON refusal that is not about the session, a device check for instance.
    other_refusal = False

    def do_GET(self):
        jar = dict(part.strip().split("=", 1) for part in self.headers.get("Cookie", "").split(";") if "=" in part)
        if not ClaudeAi.up:
            self.send_response(503)
            self.end_headers()
            return
        if ClaudeAi.cloudflare:
            self.send_response(403)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.end_headers()
            self.wfile.write(b"<html><title>Just a moment...</title></html>")
            return
        account = ClaudeAi.accounts.get(jar.get("sessionKey"))
        # What the real claude.ai answers for a made-up session, seen 2026-10-30.
        code = "device_not_trusted" if ClaudeAi.other_refusal else "account_session_invalid"
        refusal = {"type": "error", "error": {"type": "permission_error", "message": "Invalid authorization",
                                               "details": {"error_code": code}}}
        if ClaudeAi.other_refusal:
            account = None
        body = json.dumps({"uuid": account} if account else refusal).encode()
        self.send_response(200 if account and self.path == "/api/account" else 403)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ClaudeAi)
threading.Thread(target=server.serve_forever, daemon=True).start()
CLAUDE_AI = f"http://127.0.0.1:{server.server_address[1]}"


class FakeExtension:
    """Chrome's side of the native messaging port, with a cookie jar for claude.ai."""

    def __init__(self, home, jar, blocked=False):
        self.jar = jar
        self.reloads = 0
        self.blocked = blocked
        self.unblocked_changes = 0
        # Called when the host captures the session: a sign-in that lands in the middle of a switch.
        self.on_capture = None
        env = dict(os.environ, HOME=str(home), CLAUDE_SWITCH_CLAUDE_AI=CLAUDE_AI)
        self.process = subprocess.Popen([sys.executable, "-I", str(HOST), "chrome-extension://fake/"],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        out = self.process.stdout
        while True:
            header = out.read(4)
            if len(header) < 4:
                return
            message = json.loads(out.read(struct.unpack("@I", header)[0]))
            if message["cmd"] == "get":
                reply = {"id": message["id"], "cookies": [dict(c) for c in self.jar]}
            elif message["cmd"] == "capture":
                self.blocked = True
                if self.on_capture:
                    self.on_capture()
                reply = {"id": message["id"], "cookies": [dict(c) for c in self.jar if c["name"] in message["names"]]}
            elif message["cmd"] == "apply":
                if not self.blocked:
                    self.unblocked_changes += 1
                gone = {(c["name"], c["domain"]) for c in message["remove"]}
                # Chrome doesn't keep a cookie that expired already, and the worker says so.
                kept = [c for c in message["add"] if c["session"] or c["expirationDate"] > time.time()]
                lost = [c["name"] for c in message["add"] if c not in kept]
                self.jar[:] = [c for c in self.jar if (c["name"], c["domain"]) not in gone] + kept
                self.blocked = False
                self.reloads += 1
                reply = {"id": message["id"], "tabs": 1}
                if lost:
                    reply = {"id": message["id"], "error": f"Chrome did not keep {', '.join(lost)}"}
            elif message["cmd"] == "release":
                self.blocked = False
                reply = {"id": message["id"]}
            else:
                reply = {"id": message["id"], "error": "unknown"}
            data = json.dumps(reply).encode()
            self.process.stdin.write(struct.pack("@I", len(data)) + data)
            self.process.stdin.flush()

    def garbage(self):
        self.process.stdin.write(struct.pack("@I", 5) + b"[1, 2")
        self.process.stdin.flush()

    def stop(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            check("host: stops when Chrome closes the port", False)


def client(home, *args):
    env = dict(os.environ, HOME=str(home))
    return subprocess.run([sys.executable, "-I", str(HOST), *args], env=env, capture_output=True, text=True, timeout=90)


def wait_for(path, present=True):
    for _ in range(100):
        if path.exists() == present:
            return True
        time.sleep(0.05)
    return False


def names(jar):
    return {c["name"]: c["value"] for c in jar}


def saved(store, account):
    path = store / ".web" / f"{account}.json"
    return json.loads(path.read_text()) if path.exists() else None


root = pathlib.Path(tempfile.mkdtemp())
try:
    home = root / "home"
    store = home / ".claude-accounts"
    store.mkdir(parents=True, mode=0o700)
    for letter, account, org, name in (("a", ACCOUNT_A, ORG_A, ""), ("b", ACCOUNT_B, ORG_B, "bee")):
        (store / f"{letter}@example.com_{org[:8]}.json").write_text(json.dumps({"name": name, "oauthAccount": {
            "emailAddress": f"{letter}@example.com", "accountUuid": account, "organizationUuid": org}}))
    # A session saved by version 1.1 is named by its organization: it must move to its account.
    (store / ".web").mkdir(mode=0o700)
    (store / ".web" / f"{ORG_B}.json").write_text(json.dumps(web("b", ORG_B)))

    jar = web("a", ORG_A) + [trusted("a")] + [dict(c) for c in BROWSER_SIDE]
    # A worker restarted in the middle of a swap left claude.ai blocked.
    extension = FakeExtension(home, jar, blocked=True)
    check("host: socket appears", wait_for(store / ".chrome.sock"))
    for _ in range(100):
        if not extension.blocked:
            break
        time.sleep(0.05)
    check("host start: a block left behind is lifted", not extension.blocked)
    check("host: socket private", (store / ".chrome.sock").stat().st_mode & 0o077 == 0)
    check("legacy: the session named by organization moved to its account",
          saved(store, ACCOUNT_B) is not None and not (store / ".web" / f"{ORG_B}.json").exists())

    result = client(home, "--status")
    check("status: names the open account", "claude.ai in Chrome: a@example.com" in result.stdout)

    # B was saved: a swap, identified through claude.ai.
    result = client(home, "--switch", ACCOUNT_B)
    check("switch: switched", "switched to bee" in result.stdout and names(jar).get("sessionKey") == "session-b")
    check("switch: the open session saved under its account", (saved(store, ACCOUNT_A) or {}).get("account") == ACCOUNT_A)
    check("switch: saved exactly the session cookies", {c["name"] for c in saved(store, ACCOUNT_A)["cookies"]} == SESSION)
    check("switch: A's trusted device saved with A", names(saved(store, ACCOUNT_A)["cookies"]).get(DEVICE) == "trusted-a")
    check("switch: A's trusted device not left to B", DEVICE not in names(jar))
    check("switch: saved file private", ((store / ".web" / f"{ACCOUNT_A}.json").stat().st_mode & 0o077) == 0)
    check("switch: browser cookies untouched", all(names(jar).get(c["name"]) == c["value"] for c in BROWSER_SIDE))
    check("switch: done while claude.ai was blocked", extension.unblocked_changes == 0 and not extension.blocked)
    check("switch: no value printed", "session-a" not in result.stdout + result.stderr)

    # B moves to its second organization on the web: still B, nothing to do.
    for c in jar:
        if c["name"] == "lastActiveOrg":
            c["value"] = ORG_B2
    reloads = extension.reloads
    result = client(home, "--switch", ACCOUNT_B)
    check("second organization: already", "already on bee" in result.stdout and extension.reloads == reloads)

    # B registers the browser for its Remote Control.
    jar.append(trusted("b"))

    # Back to A: B's session is saved under B, whatever organization it was on.
    result = client(home, "--switch", ACCOUNT_A)
    check("back: switched", "switched to a@example.com" in result.stdout and names(jar).get("sessionKey") == "session-a")
    check("back: B saved under its account", names(saved(store, ACCOUNT_B)["cookies"]).get("lastActiveOrg") == ORG_B2)
    check("back: B's trusted device saved with B", names(saved(store, ACCOUNT_B)["cookies"]).get(DEVICE) == "trusted-b")
    check("back: A's trusted device put back", names(jar).get(DEVICE) == "trusted-a")

    # A sign-in that added an account left claude.ai on another one: --back puts back the live account when its
    # session is saved, and leaves Chrome as it is when none is, rather than signed out.
    jar[:] = [c for c in jar if c["name"] not in SESSION and c["name"] != DEVICE] + saved(store, ACCOUNT_B)["cookies"]
    result = client(home, "--back", ACCOUNT_A)
    check("--back after a sign-in: claude.ai on the live account again", result.returncode == 0
          and "switched to a@example.com" in result.stdout and names(jar).get("sessionKey") == "session-a")
    reloads = extension.reloads
    result = client(home, "--back", ACCOUNT_C)
    check("--back with no session saved: Chrome left as it is", result.returncode == 0
          and "no session saved for" in result.stdout and "it stays on the account open there" in result.stdout
          and names(jar).get("sessionKey") == "session-a" and extension.reloads == reloads)

    # claude.ai down: a session saved before is still recognized.
    ClaudeAi.up = False
    result = client(home, "--switch", ACCOUNT_B)
    check("offline: a known session still switches", names(jar).get("sessionKey") == "session-b")
    check("offline: B's trusted device put back", names(jar).get(DEVICE) == "trusted-b")

    # claude.ai down and a session it never saw: nothing changes.
    jar[:] = [c for c in jar if c["name"] not in SESSION] + web("c", ORG_A)
    reloads = extension.reloads
    result = client(home, "--switch", ACCOUNT_A)
    check("offline unknown: refused", result.returncode == 1 and "could not tell" in result.stderr)
    check("offline unknown: nothing changed", names(jar).get("sessionKey") == "session-c" and extension.reloads == reloads)
    check("offline unknown: not left blocked", not extension.blocked)
    ClaudeAi.up = True

    # A Cloudflare page in place of claude.ai's answer says nothing about the session either.
    ClaudeAi.cloudflare = True
    result = client(home, "--switch", ACCOUNT_A)
    check("cloudflare: refused", result.returncode == 1 and "could not tell" in result.stderr)
    check("cloudflare: nothing changed", names(jar).get("sessionKey") == "session-c" and extension.reloads == reloads)
    ClaudeAi.cloudflare = False

    # A JSON refusal about something else than the session does not mean signed out either.
    ClaudeAi.other_refusal = True
    result = client(home, "--switch", ACCOUNT_A)
    check("other refusal: refused", result.returncode == 1 and "could not tell" in result.stderr)
    check("other refusal: nothing changed", names(jar).get("sessionKey") == "session-c" and extension.reloads == reloads)
    ClaudeAi.other_refusal = False

    # The session changes between the moment it is named and the moment it is captured: nothing is saved.
    extension.on_capture = lambda: jar.__setitem__(slice(None), [c for c in jar if c["name"] != "sessionKey"]
                                                   + [cookie("sessionKey", "session-a")])
    result = client(home, "--switch", ACCOUNT_A)
    extension.on_capture = None
    check("changed during the switch: refused", result.returncode == 1 and "changed during the switch" in result.stderr)
    check("changed during the switch: not saved under the account named before", saved(store, ACCOUNT_C) is None)
    check("changed during the switch: not left blocked", not extension.blocked)
    jar[:] = [c for c in jar if c["name"] not in SESSION] + web("c", ORG_A)

    # A third account open in Chrome is kept under its own account.
    result = client(home, "--switch", ACCOUNT_A)
    check("third account: saved", (saved(store, ACCOUNT_C) or {}).get("account") == ACCOUNT_C)
    check("third account: switched", names(jar).get("sessionKey") == "session-a")

    # A session claude.ai no longer accepts is not saved over a good one.
    good_b = saved(store, ACCOUNT_B)
    jar[:] = [c for c in jar if c["name"] not in SESSION] + [cookie("sessionKey", "session-dead"),
                                                             cookie("lastActiveOrg", ORG_B, http_only=False)]
    result = client(home, "--switch", ACCOUNT_B)
    check("dead session open: treated as signed out", names(jar).get("sessionKey") == "session-b")
    check("dead session open: the good one stayed", saved(store, ACCOUNT_B) == good_b)

    # A registration that ran out while its session still runs is left out: Chrome would not keep it.
    data = saved(store, ACCOUNT_A)
    data["cookies"] = [c for c in data["cookies"] if c["name"] != DEVICE] + [trusted("a", expires=time.time() - 60)]
    (store / ".web" / f"{ACCOUNT_A}.json").write_text(json.dumps(data))
    result = client(home, "--switch", ACCOUNT_A)
    check("expired trusted device: switched", result.returncode == 0 and "switched to a@example.com" in result.stdout)
    check("expired trusted device: not put back", DEVICE not in names(jar))
    client(home, "--switch", ACCOUNT_B)

    # An expired saved session is not put back.
    data = saved(store, ACCOUNT_A)
    for c in data["cookies"]:
        if c["name"] == "sessionKey":
            c["expirationDate"] = time.time() - 60
    (store / ".web" / f"{ACCOUNT_A}.json").write_text(json.dumps(data))
    result = client(home, "--switch", ACCOUNT_A)
    check("expired: asks for a sign-in", "no session saved for a@example.com" in result.stdout)
    check("expired: signed out instead of a dead session", "sessionKey" not in names(jar))

    result = client(home, "--switch", "not-an-account")
    check("bad account: refused", result.returncode == 1)

    # A second host of the same Chrome takes the socket over, and the first one leaving does not remove it.
    first_socket = (store / ".chrome.sock").stat().st_ino
    second = FakeExtension(home, jar)
    for _ in range(100):
        if (store / ".chrome.sock").exists() and (store / ".chrome.sock").stat().st_ino != first_socket:
            break
        time.sleep(0.05)
    extension.stop()
    time.sleep(0.3)
    result = client(home, "--status")
    check("second host: still reachable after the first left", "claude.ai in Chrome: signed out" in result.stdout)
    extension = second

    # A host of another profile takes the socket and leaves: the one still running serves again.
    taken = (store / ".chrome.sock").stat().st_ino
    third = FakeExtension(home, jar)
    for _ in range(100):
        if (store / ".chrome.sock").exists() and (store / ".chrome.sock").stat().st_ino != taken:
            break
        time.sleep(0.05)
    third.stop()
    for _ in range(200):
        if (store / ".chrome.sock").exists():
            break
        time.sleep(0.05)
    result = client(home, "--status")
    check("newest host gone: the one left serves again", "claude.ai in Chrome: signed out" in result.stdout)

    extension.stop()
    check("Chrome closed: socket removed", wait_for(store / ".chrome.sock", present=False))
    result = client(home, "--switch", ACCOUNT_B)
    check("Chrome closed: says it follows later", result.returncode == 0 and "when it starts" in result.stdout)
    check("Chrome closed: pending kept", (store / ".chrome-pending").read_text().strip() == ACCOUNT_B)
    extension = FakeExtension(home, jar)
    for _ in range(100):
        if names(jar).get("sessionKey") == "session-b":
            break
        time.sleep(0.05)
    check("Chrome started: the pending switch is applied", names(jar).get("sessionKey") == "session-b")
    check("Chrome started: pending gone", wait_for(store / ".chrome-pending", present=False))

    # Chrome starts while claude.ai can't name the open session: the pending switch waits for claude.ai.
    extension.stop()
    wait_for(store / ".chrome.sock", present=False)
    jar[:] = [c for c in jar if c["name"] not in SESSION] + web("e", ORG_A)
    ClaudeAi.up = False
    client(home, "--switch", ACCOUNT_B)
    extension = FakeExtension(home, jar)
    wait_for(store / ".chrome.sock")
    time.sleep(1)
    check("pending, claude.ai down: kept",
          (store / ".chrome-pending").exists() and names(jar).get("sessionKey") == "session-e")
    ClaudeAi.up = True
    for _ in range(120):
        if names(jar).get("sessionKey") == "session-b":
            break
        time.sleep(0.5)
    check("pending, claude.ai back: applied",
          names(jar).get("sessionKey") == "session-b" and wait_for(store / ".chrome-pending", present=False))

    # A switch asked meanwhile replaces the pending one.
    extension.stop()
    wait_for(store / ".chrome.sock", present=False)
    jar[:] = [c for c in jar if c["name"] not in SESSION] + web("e", ORG_A)
    ClaudeAi.up = False
    client(home, "--switch", ACCOUNT_B)
    extension = FakeExtension(home, jar)
    wait_for(store / ".chrome.sock")
    time.sleep(1)
    ClaudeAi.up = True
    client(home, "--switch", ACCOUNT_C)
    check("pending replaced: the new switch applies", names(jar).get("sessionKey") == "session-c")
    check("pending replaced: the old one is dropped", not (store / ".chrome-pending").exists())

    # Something on the port the host can't read: it leaves, so that the extension starts a new one.
    extension.garbage()
    try:
        extension.process.wait(timeout=10)
        check("unreadable message: the host leaves", True)
    except subprocess.TimeoutExpired:
        check("unreadable message: the host leaves", False)
    check("unreadable message: its socket goes", wait_for(store / ".chrome.sock", present=False))
    extension.stop()

    # A host killed outright leaves its socket file: the client clears it and the switch waits for a host.
    extension = FakeExtension(home, jar)
    wait_for(store / ".chrome.sock")
    time.sleep(0.5)
    extension.process.kill()
    extension.process.wait()
    result = client(home, "--switch", ACCOUNT_A)
    check("killed host: the switch waits for the next one", result.returncode == 0 and "when it starts" in result.stdout)
    check("killed host: its socket file is cleared", not (store / ".chrome.sock").exists())
finally:
    server.shutdown()
    shutil.rmtree(root)

print(f"{passes} passed, {len(failures)} failed")
sys.exit(1 if failures else 0)
