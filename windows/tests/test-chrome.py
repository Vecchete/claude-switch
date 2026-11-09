#!/usr/bin/env python3
"""claude-switch drives claude.ai in Chrome through the companion host over its named pipe, the Windows branch of
chrome-host.py: a fake extension, a fake claude.ai, a scratch profile and a pipe of its own. Also with Chrome closed
(the pending file), and with a second host, which takes the pipe over once the first one is gone. Windows only, see
tests/test-switch-chrome.py for the socket. Usage: test-chrome.py [SWITCH_SCRIPT [HOST]], the ones of windows/ by
default."""
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

if os.name != "nt":
    print("skipped: the named pipe of chrome-host.py is Windows only")
    sys.exit(0)

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else HERE.parent / "claude-switch.py").resolve()
HOST = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else HERE.parent / "chrome-host.py").resolve()
ORG_A = "aaaaaaaa-0000-4000-8000-00000000000a"
ORG_B = "bbbbbbbb-0000-4000-8000-00000000000b"
ACCOUNT_A = "aaaaaaaa-0000-4000-8000-0000000000a1"
ACCOUNT_B = "bbbbbbbb-0000-4000-8000-0000000000b1"
NO_WINDOW = subprocess.CREATE_NO_WINDOW
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


def session(jar):
    return next((c["value"] for c in jar if c["name"] == "sessionKey"), None)


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
root = pathlib.Path(tempfile.mkdtemp(prefix="claude-switch-chrome-windows."))
home = root / "home"
share = home / ".local" / "share" / "claude-switch"
store = home / ".claude-accounts"
# The pipe is named after USERNAME: this one is the test's alone, whatever host of the real profile runs.
base = {key: value for key, value in os.environ.items() if not key.upper().startswith(("CLAUDE", "ANTHROPIC"))}
base.update(USERPROFILE=str(home), HOME=str(home), USERNAME=f"claude-switch-test-{os.getpid()}",
            PATH=f"{root / 'bin'}{os.pathsep}{os.environ['PATH']}", FAKE_STATE=str(root / "state"),
            CLAUDE_SWITCH_CLAUDE_AI=f"http://127.0.0.1:{server.server_address[1]}", PYTHONDONTWRITEBYTECODE="1")


class FakeExtension:
    """Chrome's side of the native messaging port: answers what the host asks of the extension, on a cookie jar."""

    def __init__(self, jar):
        self.jar = jar
        self.process = subprocess.Popen([sys.executable, "-I", str(share / "chrome-host.py"), "chrome-extension://fake/",
                                         "--parent-window=0"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=base,
                                        creationflags=NO_WINDOW)
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
            try:
                self.process.stdin.write(struct.pack("@I", len(data)) + data)
                self.process.stdin.flush()
            except OSError:
                return

    def stop(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            self.process.wait(timeout=20)


def run(*args):
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], env=base, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8", timeout=180, creationflags=NO_WINDOW)


def host_status():
    return subprocess.run([sys.executable, "-I", str(share / "chrome-host.py"), "--status"], env=base,
                          capture_output=True, text=True, encoding="utf-8", timeout=120, creationflags=NO_WINDOW)


def wait_for(condition, seconds):
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.2)
    return True


def serving():
    return "not running" not in host_status().stdout


hosts = []
try:
    for folder in (home / ".claude", root / "bin", root / "state", share):
        folder.mkdir(parents=True)
    (root / "bin" / "claude.cmd").write_text(f'@"{sys.executable}" "{HERE / "fake-claude.py"}" %*\n', encoding="utf-8")
    shutil.copy(HOST, share / "chrome-host.py")
    login = {"accessToken": "A-access", "refreshToken": "A-refresh", "expiresAt": 4102444800000,
             "refreshTokenExpiresAt": 4102444800000, "scopes": ["user:inference"], "subscriptionType": "max"}
    (home / ".claude" / ".credentials.json").write_text(json.dumps({"claudeAiOauth": login}))
    (home / ".claude.json").write_text(json.dumps({"oauthAccount": {
        "emailAddress": "a@example.com", "accountUuid": ACCOUNT_A, "organizationUuid": ORG_A}}))
    store.mkdir()
    (store / f"b@example.com_{ORG_B[:8]}.json").write_text(json.dumps({
        "name": "bee", "login": {"claudeAiOauth": dict(login, accessToken="B-access", refreshToken="B-refresh")},
        "oauthAccount": {"emailAddress": "b@example.com", "accountUuid": ACCOUNT_B, "organizationUuid": ORG_B}}))

    jar = web("a", ORG_A)
    hosts.append(FakeExtension(jar))
    check("the host serves its pipe", wait_for(serving, 15))

    result = run("bee")
    check("switch: Claude Code switched", "Switched to bee." in result.stdout)
    check("switch: claude.ai asks for one sign-in", "no session saved for bee yet" in result.stdout)
    check("switch: the open web session was saved", (store / ".web" / f"{ACCOUNT_A}.json").exists())

    jar[:] = web("b", ORG_B)
    result = run("a@example.com")
    check("back: claude.ai switched", "claude.ai in Chrome: switched to a@example.com" in result.stdout
          and session(jar) == "session-a")
    result = run("a@example.com")
    check("same account: claude.ai already on it", "claude.ai in Chrome: already on a@example.com." in result.stdout)
    status = host_status().stdout
    check("status: the account open and the saved ones", "claude.ai in Chrome: a@example.com" in status
          and "bee" in status)

    # A second profile that loads the extension waits until the first host is gone, then serves.
    second = web("b", ORG_B)
    hosts.append(FakeExtension(second))
    time.sleep(1)
    result = run("bee")
    check("two hosts: the first one serves", "switched to bee" in result.stdout and session(jar) == "session-b"
          and session(second) == "session-b")
    hosts[0].stop()
    check("two hosts: the second one takes the pipe over", wait_for(serving, 15))
    result = run("a@example.com")
    check("two hosts: the second one serves", "switched to a@example.com" in result.stdout
          and session(second) == "session-a")

    # Chrome closed: the switch waits in the pending file for the next host.
    hosts[1].stop()
    result = run("bee")
    check("Chrome closed: Claude Code still switches", "Switched to bee." in result.stdout and result.returncode == 0)
    check("Chrome closed: claude.ai follows later", "when it starts" in result.stdout
          and (store / ".chrome-pending").exists())
    third = web("a", ORG_A)
    hosts.append(FakeExtension(third))
    check("Chrome opens: the pending switch is done",
          wait_for(lambda: session(third) == "session-b" and not (store / ".chrome-pending").exists(), 20))

    (share / "chrome-host.py").unlink()
    result = run("a@example.com")
    check("no companion: nothing about Chrome", "Chrome" not in result.stdout + result.stderr
          and result.returncode == 0)
finally:
    for extension in hosts:
        try:
            extension.stop()
        except (OSError, subprocess.TimeoutExpired):
            extension.process.kill()
    server.shutdown()
    shutil.rmtree(root, ignore_errors=True)

print(f"{passes} passed, {len(failures)} failed")
sys.exit(1 if failures else 0)
