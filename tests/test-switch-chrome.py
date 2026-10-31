#!/usr/bin/env python3
"""claude-switch drives claude.ai in Chrome through the companion host. Fake extension, scratch HOME.
Usage: test-switch-chrome.py SWITCH_SCRIPT HOST"""
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

SCRIPT = pathlib.Path(sys.argv[1]).resolve()
HOST = pathlib.Path(sys.argv[2]).resolve()
HERE = pathlib.Path(__file__).resolve().parent
ORG_A = "aaaaaaaa-0000-4000-8000-00000000000a"
ORG_B = "bbbbbbbb-0000-4000-8000-00000000000b"
ACCOUNT_A = "aaaaaaaa-0000-4000-8000-0000000000a1"
ACCOUNT_B = "bbbbbbbb-0000-4000-8000-0000000000b1"
failures = []
passes = 0


def check(name, condition):
    global passes
    if condition:
        passes += 1
    else:
        failures.append(name)
        print("FAIL", name)


def cookie(name, value):
    return {"name": name, "value": value, "domain": ".claude.ai", "path": "/", "hostOnly": False, "httpOnly": True,
            "secure": True, "sameSite": "lax", "session": False, "expirationDate": 4102444800, "storeId": "0"}


def web(letter, org):
    return [cookie("sessionKey", f"session-{letter}"), cookie("lastActiveOrg", org), cookie("routingHint", letter)]


class ClaudeAi(http.server.BaseHTTPRequestHandler):
    accounts = {"session-a": ACCOUNT_A, "session-b": ACCOUNT_B}

    def do_GET(self):
        jar = dict(part.strip().split("=", 1) for part in self.headers.get("Cookie", "").split(";") if "=" in part)
        account = ClaudeAi.accounts.get(jar.get("sessionKey"))
        self.send_response(200 if account else 403)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"uuid": account}).encode())

    def log_message(self, *args):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ClaudeAi)
threading.Thread(target=server.serve_forever, daemon=True).start()


class FakeExtension:
    def __init__(self, home, jar):
        self.jar = jar
        self.process = subprocess.Popen([sys.executable, "-I", str(home / ".local/share/claude-switch/chrome-host.py"),
                                         "chrome-extension://fake/"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        env=dict(os.environ, HOME=str(home),
                                                 CLAUDE_SWITCH_CLAUDE_AI=f"http://127.0.0.1:{server.server_address[1]}"))
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            header = self.process.stdout.read(4)
            if len(header) < 4:
                return
            message = json.loads(self.process.stdout.read(struct.unpack("@I", header)[0]))
            if message["cmd"] == "get":
                reply = {"id": message["id"], "cookies": [dict(c) for c in self.jar]}
            elif message["cmd"] == "capture":
                reply = {"id": message["id"], "cookies": [dict(c) for c in self.jar if c["name"] in message["names"]]}
            elif message["cmd"] == "apply":
                gone = {c["name"] for c in message["remove"]}
                self.jar[:] = [c for c in self.jar if c["name"] not in gone] + message["add"]
                reply = {"id": message["id"], "tabs": 1}
            else:
                reply = {"id": message["id"]}
            data = json.dumps(reply).encode()
            self.process.stdin.write(struct.pack("@I", len(data)) + data)
            self.process.stdin.flush()

    def stop(self):
        self.process.stdin.close()
        self.process.wait(timeout=10)


def run(home, *args):
    env = dict(os.environ, HOME=str(home), PATH=f"{home / 'bin'}:{os.environ['PATH']}", FAKE_STATE=str(home / "state"))
    for name in ("CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
        env.pop(name, None)
    return subprocess.run([str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=120)


def session(jar):
    return next((c["value"] for c in jar if c["name"] == "sessionKey"), None)


root = pathlib.Path(tempfile.mkdtemp())
try:
    home = root / "home"
    (home / ".claude").mkdir(parents=True)
    (home / "bin").mkdir()
    (home / "state").mkdir()
    shutil.copy(HERE / "fake-claude.sh", home / "bin/claude")
    (home / "bin/claude").chmod(0o755)
    (home / ".local/share/claude-switch").mkdir(parents=True)
    shutil.copy(HOST, home / ".local/share/claude-switch/chrome-host.py")
    (home / ".local/share/claude-switch/chrome-host.py").chmod(0o755)
    login = {"accessToken": "A-access", "refreshToken": "A-refresh", "expiresAt": 4102444800000,
             "refreshTokenExpiresAt": 4102444800000, "scopes": ["user:inference"], "subscriptionType": "max"}
    (home / ".claude/.credentials.json").write_text(json.dumps({"claudeAiOauth": login}))
    (home / ".claude.json").write_text(json.dumps({"oauthAccount": {"emailAddress": "a@example.com", "accountUuid": ACCOUNT_A, "organizationUuid": ORG_A}}))
    store = home / ".claude-accounts"
    store.mkdir(mode=0o700)
    (store / f"b@example.com_{ORG_B[:8]}.json").write_text(json.dumps({
        "name": "bee", "login": {"claudeAiOauth": dict(login, accessToken="B-access", refreshToken="B-refresh")},
        "oauthAccount": {"emailAddress": "b@example.com", "accountUuid": ACCOUNT_B, "organizationUuid": ORG_B}}))

    jar = web("a", ORG_A)
    extension = FakeExtension(home, jar)
    for _ in range(100):
        if (store / ".chrome.sock").exists():
            break
        time.sleep(0.05)

    result = run(home, "bee")
    check("switch: Claude Code switched", "Switched to bee." in result.stdout)
    check("switch: claude.ai asks for one sign-in", "no session saved for bee yet" in result.stdout)
    check("switch: the open web session was saved", (store / ".web" / f"{ACCOUNT_A}.json").exists())

    jar[:] = web("b", ORG_B)
    result = run(home, "a@example.com")
    check("back: Claude Code switched", "Switched to a@example.com." in result.stdout)
    check("back: claude.ai switched", "claude.ai in Chrome: switched to a@example.com" in result.stdout and session(jar) == "session-a")

    result = run(home, "a@example.com")
    check("same account: Claude Code already on it", "Already on a@example.com." in result.stdout)
    check("same account: claude.ai already on it", "claude.ai in Chrome: already on a@example.com." in result.stdout)

    jar[:] = web("b", ORG_B)
    result = run(home, "a@example.com")
    check("web on the other account: the same switch puts it right", session(jar) == "session-a")

    extension.stop()
    result = run(home, "bee")
    check("Chrome closed: Claude Code still switches", "Switched to bee." in result.stdout and result.returncode == 0)
    check("Chrome closed: claude.ai follows later", "when it starts" in result.stdout)

    (home / ".local/share/claude-switch/chrome-host.py").unlink()
    result = run(home, "a@example.com")
    check("no companion: nothing about Chrome", "Chrome" not in result.stdout + result.stderr and result.returncode == 0)
finally:
    server.shutdown()
    shutil.rmtree(root)

print(f"{passes} passed, {len(failures)} failed")
sys.exit(1 if failures else 0)
