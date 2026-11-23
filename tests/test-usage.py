#!/usr/bin/env python3
"""Tests of usage.py on a scratch HOME, against a stand-in usage endpoint and a stand-in gateway on this machine.
Usage: test-usage.py USAGE_PY"""
import datetime
import http.server
import json
import os
import pathlib
import pty
import socketserver
import subprocess
import sys
import tempfile
import threading
import time

# usage.py runs with TZ=UTC: the times this test expects are built in the same zone.
os.environ["TZ"] = "UTC"
time.tzset()

HELPER = pathlib.Path(sys.argv[1]).resolve()
DAY = 24 * 3600 * 1000
HOUR = 3600 * 1000
failures = []
passes = 0


def check(name, condition):
    global passes
    if condition:
        passes += 1
    else:
        failures.append(name)
        print("FAIL", name)


def now_ms():
    return int(time.time() * 1000)


def iso(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).isoformat()


class Server(http.server.BaseHTTPRequestHandler):
    """Answers each token with what ANSWERS holds for it: a status and a body. Logs every request."""
    answers = {}
    requests = []

    def do_GET(self):
        token = self.headers.get("Authorization", "").removeprefix("Bearer ") if hasattr(str, "removeprefix") else \
            self.headers.get("Authorization", "")[7:]
        Server.requests.append({"path": self.path, "token": token,
                                "headers": {name.lower(): value for name, value in self.headers.items()}})
        status, body = Server.answers.get((self.path, token), (404, {"error": "not here"}))
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *arguments):
        pass


def usage(limits=None, week=None, window=None, scoped=None, old=False):
    """An answer of the usage endpoint: WEEK and WINDOW are (percent, resets ms), WINDOW None for no 5-hour one."""
    if old:
        return {"five_hour": None if window is None else {"utilization": window[0], "resets_at": iso(window[1])},
                "seven_day": {"utilization": week[0], "resets_at": iso(week[1])}, "extra_usage": {"is_enabled": False}}
    entries = [{"kind": "weekly_all", "group": "weekly", "percent": week[0], "resets_at": iso(week[1])}]
    if window is not None:
        entries.append({"kind": "session", "group": "session", "percent": window[0], "resets_at": iso(window[1])})
    for name, percent, resets in scoped or []:
        entries.append({"kind": "weekly_scoped", "group": "weekly", "percent": percent, "resets_at": iso(resets),
                        "scope": {"model": {"id": None, "display_name": name}}})
    return {"five_hour": None if window is None else {"utilization": window[0], "resets_at": iso(window[1])},
            "seven_day": {"utilization": week[0], "resets_at": iso(week[1])}, "limits": entries}


class Machine:
    def __init__(self, root, port):
        self.home = root / "home"
        self.store = self.home / ".claude-accounts"
        self.store.mkdir(parents=True, mode=0o700)
        (self.home / ".claude" / "sessions").mkdir(parents=True)
        self.env = {**os.environ, "HOME": str(self.home), "TZ": "UTC",
                    "CLAUDE_SWITCH_ANTHROPIC_URL": f"http://127.0.0.1:{port}"}
        self.env.pop("http_proxy", None)
        self.env.pop("HTTP_PROXY", None)
        self.env["no_proxy"] = "127.0.0.1"
        self.port = port

    def account(self, name, email, tier, token, expires=None, ends=None, **extra):
        path = self.store / f"{email}_x.json"
        path.write_text(json.dumps({
            "name": name, "login": {"claudeAiOauth": {"accessToken": token, "refreshToken": f"{token}-refresh",
                                                      "expiresAt": expires or now_ms() + 8 * HOUR,
                                                      "refreshTokenExpiresAt": ends or now_ms() + 20 * DAY,
                                                      "rateLimitTier": tier}},
            "oauthAccount": {"emailAddress": email, "accountUuid": f"uuid-{name}", "organizationRateLimitTier": tier},
            **extra}))
        return str(path)

    def key(self, name, env):
        path = self.store / f"key_{name}.json"
        path.write_text(json.dumps({"kind": "key", "name": name, "env": env}))
        return str(path)

    def run(self, *arguments):
        result = subprocess.run([sys.executable, "-I", str(HELPER), *arguments], env=self.env, capture_output=True,
                                text=True, timeout=60)
        return result.returncode, result.stdout, result.stderr

    def reading(self, file):
        try:
            return json.loads((self.store / ".usage" / f"{pathlib.Path(file).stem}.json").read_text())
        except OSError:
            return None


def start_of(pid):
    return pathlib.Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[19]


class RootService(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """Stands in for the claude-switch-remote service: it logs each request with when it came, the status the session
    had then and the letter of the second key. Ctrl+Q turns Remote Control on in the sessions of RECONNECTS, as the
    key does, and Ctrl+R starts a turn in those of LISTENS, as /after-switch does. It refuses the others, as for a
    session that isn't the foreground of its terminal."""
    daemon_threads = True

    def __init__(self, path, sessions):
        self.sessions = sessions
        self.requests = []
        self.reconnects = set()
        self.listens = set()
        super().__init__(str(path), RootRequest)


class RootRequest(socketserver.StreamRequestHandler):
    def handle(self):
        pid, start, *letter = self.rfile.readline().decode().split()
        if letter == ["q"]:
            # A service installed before Ctrl+R reads Ctrl+Q only without its letter.
            self.wfile.write(b"refused request\n")
            return
        letter = letter[0] if letter else "q"
        record = self.server.sessions / f"{pid}.json"
        session = json.loads(record.read_text())
        self.server.requests.append((int(pid), start, session.get("status"), time.time(), letter))
        if int(pid) not in (self.server.reconnects if letter == "q" else self.server.listens):
            self.wfile.write(b"refused foreground\n")
            return
        if letter == "q":
            session["bridgeSessionId"] = "session_new"
        else:
            session["statusUpdatedAt"] = now_ms()
        record.write_text(json.dumps(session))
        self.wfile.write(b"pressed\n")


def remote_checks(m, sessions):
    """After a switch to another claude.ai account: the sessions that had Remote Control get /remote-control run once
    they dropped it and wait idle, then every running session gets /after-switch. Ctrl+X Ctrl+Q and Ctrl+X Ctrl+R,
    which save binds to them, are pressed at the prompt of their tmux pane, or by the root service outside tmux, and
    /remote-control is typed at an empty prompt when its key isn't bound or didn't take."""
    fake = m.home.parent / "tmux"
    fake.mkdir()
    bin_folder = m.home.parent / "bin"
    bin_folder.mkdir()
    (bin_folder / "tmux").write_text((pathlib.Path(__file__).parent / "fake-tmux.py").read_text())
    (bin_folder / "tmux").chmod(0o755)
    service = RootService(m.home.parent / "remote.sock", sessions)
    threading.Thread(target=service.serve_forever, daemon=True).start()
    env = {**m.env, "PATH": f"{bin_folder}:{m.env['PATH']}", "FAKE_TMUX": str(fake), "CLAUDE_SWITCH_REMOTE_DROP": "3",
           "CLAUDE_SWITCH_REMOTE_BACK": "2", "CLAUDE_SWITCH_REMOTE_WAIT": "12", "CLAUDE_SWITCH_REMOTE_SETTLE": "1",
           "CLAUDE_SWITCH_REMOTE_SOCKET": str(m.home.parent / "remote.sock")}
    keybindings = m.home / ".claude" / "keybindings.json"
    message = m.home / ".claude" / "skills" / "after-switch" / "SKILL.md"
    bound = {"ctrl+x ctrl+q": "command:remote-control", "ctrl+x ctrl+r": "command:after-switch"}
    pressed = [["C-x"], ["C-q"]]
    told = [["C-x"], ["C-r"]]
    typed = [["-l", "/remote-control"], ["Enter"]]

    def run(*arguments, **changes):
        result = subprocess.run([sys.executable, "-I", str(HELPER), "remote", *arguments], env={**env, **changes},
                                capture_output=True, text=True, timeout=90)
        return result.returncode, result.stdout

    names = ("idle", "busy", "typing", "elsewhere", "scrolled", "without", "keeps", "other", "stuck", "shell",
             "working", "deaf", "nowhere")
    # With no terminal of their own, also when the suite runs from one.
    processes = {name: subprocess.Popen(["sleep", "300"], start_new_session=True) for name in names}
    pids = {name: process.pid for name, process in processes.items()}
    masters = []
    # Outside tmux, on a terminal of their own.
    outside = ("terminal", "frozen", "plain", "unheard")
    for name in outside:
        pid, master = pty.fork()
        if pid == 0:
            try:
                os.execvp("sleep", ["sleep", "300"])
            finally:
                os._exit(127)
        pids[name] = pid
        masters.append(master)
    empty = chr(0x276F) + chr(0xA0)
    records, panes = {}, {}

    def session(name, pane=None, bridge="session_old", status="idle", x=2, line=empty, mode="0", reconnects=True,
                listens=True, pane_pid=None):
        pid = pids[name]
        record = {"pid": pid, "procStart": start_of(pid), "kind": "interactive", "status": status, "name": name,
                  "bridgeSessionId": bridge}
        if pane:
            record["tmux"] = f"claude-{name}:@1.{pane}"
            panes[pane] = {"pane_pid": pane_pid or pid, "x": x, "y": 40, "mode": mode, "line": line,
                           "record": str(sessions / f"{pid}.json"), "reconnects": reconnects, "listens": listens}
        records[name] = sessions / f"{pid}.json"
        records[name].write_text(json.dumps(record))

    def update(name, **fields):
        record = json.loads(records[name].read_text())
        record.update(fields)
        records[name].write_text(json.dumps(record))

    def restore(**changes):
        """Runs a restore to its end, the busy session idle after a moment: what a second one at once got, and what
        came of each session."""
        first = subprocess.Popen([sys.executable, "-I", str(HELPER), "remote", "restore"], env={**env, **changes},
                                 stdout=subprocess.PIPE, text=True)
        time.sleep(2.5)
        rc, _ = run("restore", **changes)
        if records["busy"].exists():
            update("busy", status="idle")
        out, _ = first.communicate(timeout=90)
        return rc, out, dict(reversed(line.split("\t")) for line in out.splitlines())

    def sent():
        """The keys sent to each pane, in order, when each went, and the status of its session each time."""
        log = [json.loads(line) for line in (fake / "log").read_text().splitlines()] if (fake / "log").exists() else []
        keys, times = {}, {}
        for entry in log:
            if entry["args"][0] == "send-keys":
                keys.setdefault(entry["args"][2], []).append(entry["args"][3:])
                times.setdefault(entry["args"][2], []).append(entry["at"])
        return keys, times, [entry["status"] for entry in log if entry["args"][0] == "send-keys"]

    def asked(name):
        """The letters the root service was asked to press in the session NAME, and when."""
        return [(request[4], request[3]) for request in service.requests if request[0] == pids[name]]

    def again(moments, gap):
        return all(later - earlier >= gap for earlier, later in zip(moments, moments[1:]))

    try:
        session("idle", "%1")
        (fake / "panes.json").write_text(json.dumps(panes))
        rc, _ = run("save")
        written = json.loads(keybindings.read_text())
        check("remote save: binds both keys in a new keybindings.json", rc == 0
              and written.get("$schema") == "https://www.schemastore.org/claude-code-keybindings.json"
              and written.get("bindings") == [{"context": "Chat", "bindings": bound}])
        check("remote save: a new keybindings.json is readable, as Claude Code writes it",
              keybindings.stat().st_mode & 0o777 == 0o644)
        text = message.read_text() if message.exists() else ""
        check("remote save: writes /after-switch, a skill Claude doesn't run on its own",
              "\ndisable-model-invocation: true\n" in text and "send that reply again in full" in text)
        check("remote save: /after-switch readable, as Claude Code writes its files",
              message.exists() and message.stat().st_mode & 0o777 == 0o644)
        message.write_text("my own words\n")
        (m.store / ".remote.json").unlink()
        keybindings.write_text(json.dumps({"bindings": [{"context": "Chat", "bindings": {"ctrl+e": "chat:externalEditor"}}]}))
        keybindings.chmod(0o600)
        session("busy", "%2", status="busy")
        session("typing", "%3", x=17, line=chr(0x276F) + " fix the tests")
        session("elsewhere")
        session("scrolled", "%5", mode="1")
        session("without", "%6", bridge=None)
        session("keeps", "%7")
        session("other", "%8", pane_pid=pids["shell"])
        session("stuck", "%9", reconnects=False)
        session("working", "%10", bridge=None, status="busy")
        session("deaf", "%11", listens=False)
        session("terminal")
        session("frozen")
        session("plain", bridge=None)
        session("unheard", bridge=None)
        session("nowhere", bridge=None)
        service.reconnects.add(pids["terminal"])
        service.listens.update((pids["terminal"], pids["plain"]))
        (fake / "panes.json").write_text(json.dumps(panes))
        rc, _ = run("save")
        state = json.loads((m.store / ".remote.json").read_text())
        check("remote save: every running session", rc == 0
              and sorted(s["name"] for s in state["sessions"]) == sorted(records))
        unplugged = sorted(s["name"] for s in state["sessions"] if not s["remote"])
        check("remote save: which of them have Remote Control on",
              unplugged == ["nowhere", "plain", "unheard", "without", "working"])
        check("remote save: each with its pane", {s["name"]: s["pane"] for s in state["sessions"]}["idle"] == "%1"
              and {s["name"]: s["pane"] for s in state["sessions"]}["elsewhere"] is None)
        check("remote save: private", (m.store / ".remote.json").stat().st_mode & 0o777 == 0o600)
        check("remote save: both keys bound beside the bindings of the user, in a file that keeps its mode",
              json.loads(keybindings.read_text())["bindings"]
              == [{"context": "Chat", "bindings": {"ctrl+e": "chat:externalEditor", **bound}}]
              and keybindings.stat().st_mode & 0o777 == 0o600)
        check("remote save: an /after-switch the user changed stays as it is", message.read_text() == "my own words\n")
        rc, out = run("held")
        check("remote held: what a switch would take for good, with no terminal of its own or in another pane",
              rc == 0 and sorted(out.split()) == ["elsewhere", "other"])
        rc, out = run("held", CLAUDE_SWITCH_REMOTE_SOCKET=str(m.home.parent / "none.sock"))
        check("remote held: outside tmux without the root service", rc == 0
              and sorted(out.split()) == ["elsewhere", "frozen", "other", "terminal"])
        # Claude Code drops Remote Control within seconds of the switch, but in a session that keeps it.
        for name in ("idle", "busy", "typing", "elsewhere", "scrolled", "other", "stuck", "terminal", "frozen", "deaf"):
            update(name, bridgeSessionId=None)
        # Another switch before they're back: the sessions that wait stay as they were, a session with Remote Control
        # on starts again.
        run("save")
        waiting = {s["name"]: s for s in json.loads((m.store / ".remote.json").read_text())["sessions"]}
        check("remote save: a session still waiting from a switch before stays as it was",
              {name: entry for name, entry in waiting.items() if name != "keeps"}
              == {s["name"]: s for s in state["sessions"] if s["name"] != "keeps"})
        rc, out, outcomes = restore()
        check("remote restore: one at a time", rc == 1)
        check("remote restore: back where the key was pressed and took",
              all(outcomes.get(name) == "back" for name in ("idle", "busy", "typing", "terminal", "deaf")))
        check("remote restore: lost where nothing can press it, or it didn't take",
              all(outcomes.get(name) == "lost" for name in ("elsewhere", "scrolled", "other", "stuck", "frozen")))
        check("remote restore: told where /after-switch took in a session without Remote Control",
              outcomes.get("without") == "told" and outcomes.get("plain") == "told")
        check("remote restore: nothing said of a session that kept it, one in use, or one /after-switch never reached",
              not {"keeps", "working", "unheard", "nowhere"} & set(outcomes))
        check("remote restore: a session nothing can reach is said first", out.splitlines()[0] == "lost\telsewhere")
        keys, times, statuses = sent()
        check("remote restore: Ctrl+X Ctrl+Q at the prompt of each pane, with text typed or not, then Ctrl+X Ctrl+R",
              all(keys.get(pane) == pressed + told for pane in ("%1", "%2", "%3")))
        check("remote restore: /remote-control typed at the empty prompt when the key didn't take, 3 tries in all",
              keys.get("%9") == pressed + typed * 2)
        check("remote restore: nothing in a pane scrolled back or that runs another program",
              "%5" not in keys and "%8" not in keys)
        check("remote restore: /after-switch in a session without Remote Control too", keys.get("%6") == told)
        check("remote restore: nothing in a session in use", "%10" not in keys)
        check("remote restore: /after-switch again where it didn't take, 3 times in all",
              keys.get("%11") == pressed + told * 3)
        check("remote restore: the next /after-switch once the last had time to take",
              again(times.get("%11", [])[3::2], 1.9))
        check("remote restore: only in an idle session", statuses and all(status == "idle" for status in statuses))
        start = start_of(pids["terminal"])
        check("remote restore: outside tmux the root service presses both keys, for the session that started then",
              [(request[1], request[4]) for request in service.requests if request[0] == pids["terminal"]]
              == [(start, "q"), (start, "r")])
        check("remote restore: outside tmux /after-switch in a session without Remote Control too",
              [letter for letter, _ in asked("plain")] == ["r"])
        check("remote restore: 3 tries outside tmux where the service refused, and no /after-switch then",
              [letter for letter, _ in asked("frozen")] == ["q"] * 3)
        check("remote restore: the next try once the last had time to take",
              again([moment for _, moment in asked("frozen")], 1.9))
        check("remote restore: nothing outside tmux in a session with no terminal of its own", asked("nowhere") == [])
        check("remote restore: /after-switch 3 times outside tmux where the service refused it",
              [letter for letter, _ in asked("unheard")] == ["r"] * 3
              and again([moment for _, moment in asked("unheard")], 1.9))
        check("remote restore: the root service only for an idle session",
              all(request[2] == "idle" for request in service.requests))
        check("remote restore: nothing left", not (m.store / ".remote.json").exists())
        rc, out = run("held")
        check("remote held: none once those sessions have it off", rc == 1 and out == "")

        # A keybindings.json Claude Code can't read, or a key of the user's in the way: the file stays as it is but for
        # the other key, and a key that isn't bound is never pressed. Without its key /remote-control is typed at an
        # empty prompt in tmux, and /after-switch goes nowhere.
        for name in set(records) - {"idle", "typing", "terminal"}:
            records[name].unlink(missing_ok=True)
        message.unlink()

        def round_of(text, **changes):
            """Saves with TEXT in keybindings.json, then restores once the sessions dropped Remote Control."""
            service.requests.clear()
            (fake / "log").unlink(missing_ok=True)
            for name in ("idle", "typing", "terminal"):
                update(name, bridgeSessionId="session_old")
            keybindings.write_text(text)
            held = run("held")
            run("save")
            saved = keybindings.read_text()
            for name in ("idle", "typing", "terminal"):
                update(name, bridgeSessionId=None)
            outcomes = restore(CLAUDE_SWITCH_REMOTE_WAIT="5", **changes)[2]
            keys, times, _ = sent()
            return held, saved, outcomes, keys, times

        held, saved, outcomes, keys, _ = round_of("{ not json")
        check("remote held: outside tmux when the keys can't be bound", held == (0, "terminal\n"))
        check("remote save: a keybindings.json Claude Code can't read left as it was", saved == "{ not json")
        check("remote save: no /after-switch without its key", not message.exists())
        check("remote restore: without the key, /remote-control typed at an empty prompt",
              outcomes.get("idle") == "back" and keys.get("%1") == typed)
        check("remote restore: without the key, nothing over text typed at the prompt",
              outcomes.get("typing") == "lost" and "%3" not in keys)
        check("remote restore: without the key, nothing outside tmux",
              outcomes.get("terminal") == "lost" and service.requests == [])

        theirs = {"context": "Select", "bindings": {"ctrl+q": "select:cancel"}}
        # A longer wait once Remote Control is back, which the next step alone doesn't give.
        held, saved, outcomes, keys, times = round_of(json.dumps({"bindings": [theirs]}),
                                                      CLAUDE_SWITCH_REMOTE_SETTLE="4")
        check("remote held: outside tmux when Ctrl+Q is the user's", held == (0, "terminal\n"))
        check("remote save: with Ctrl+Q the user's, the key of /after-switch alone",
              json.loads(saved)["bindings"]
              == [theirs, {"context": "Chat", "bindings": {"ctrl+x ctrl+r": "command:after-switch"}}])
        check("remote save: /after-switch written with its key", message.exists())
        check("remote restore: /remote-control typed at an empty prompt, then /after-switch by its key",
              outcomes.get("idle") == "back" and keys.get("%1") == typed + told)
        check("remote restore: /after-switch a few seconds after Remote Control is back",
              len(times.get("%1", [])) == 4 and times["%1"][2] - times["%1"][1] >= 3.9)
        check("remote restore: without the key of /remote-control, nothing over text typed, nor outside tmux",
              outcomes.get("typing") == "lost" and "%3" not in keys and outcomes.get("terminal") == "lost"
              and service.requests == [])

        # /after-switch stays from the round before.
        theirs = {"context": "Chat", "bindings": {"ctrl+x ctrl+r": None}}
        held, saved, outcomes, keys, _ = round_of(json.dumps({"bindings": [theirs]}))
        check("remote save: with Ctrl+X Ctrl+R the user's, the key of /remote-control alone",
              json.loads(saved)["bindings"]
              == [{"context": "Chat", "bindings": {"ctrl+x ctrl+r": None, "ctrl+x ctrl+q": "command:remote-control"}}])
        check("remote restore: no /after-switch when its key is the user's",
              all(outcomes.get(name) == "back" for name in ("idle", "typing", "terminal"))
              and keys.get("%1") == pressed and keys.get("%3") == pressed
              and [request[4] for request in service.requests] == ["q"])
    finally:
        for process in processes.values():
            process.kill()
            process.wait()
        for name in outside:
            try:
                os.kill(pids[name], 9)
                os.waitpid(pids[name], 0)
            except OSError:
                pass
        for master in masters:
            os.close(master)
        for path in records.values():
            path.unlink(missing_ok=True)
        service.shutdown()
        service.server_close()


def main():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Server)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    with tempfile.TemporaryDirectory(prefix="claude-switch-usage.") as folder:
        m = Machine(pathlib.Path(folder), port)
        now = now_ms()
        ultra = m.account("ultra", "u@example.com", "default_claude_max_20x", "U-token")
        maxed = m.account("max", "m@example.com", "default_claude_max_5x", "M-token")
        pro = m.account("pro", "p@example.com", "default_claude_pro", "P-token")
        gw = m.key("gw", {"ANTHROPIC_AUTH_TOKEN": "G-token", "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}/gw",
                          "ANTHROPIC_DEFAULT_SONNET_MODEL": "x/sonnet"})
        api = m.key("api", {"ANTHROPIC_API_KEY": "K-token"})
        headers_only = m.key("hdr", {"ANTHROPIC_BASE_URL": "https://hdr.example",
                                     "ANTHROPIC_CUSTOM_HEADERS": "x-portkey-api-key: H-token"})
        everyone = [ultra, maxed, pro, gw, api, headers_only]
        routable = [ultra, maxed, pro, gw, api]
        week_ends = now + 2 * DAY
        Server.answers = {
            ("/api/oauth/usage", "U-token"): (200, usage(week=(30, week_ends))),
            ("/api/oauth/usage", "M-token"): (200, usage(week=(51, now + 3 * DAY), window=(48, now + 2 * HOUR))),
            ("/api/oauth/usage", "P-token"): (200, usage(week=(10, now + DAY), window=(5, now + HOUR), old=True)),
            ("/key/info", "G-token"): (200, {"key": "hashed", "info": {"key_alias": "me", "spend": 3.2,
                                                                     "max_budget": 25.0, "budget_duration": "30d",
                                                                     "budget_reset_at": iso(now + 10 * DAY)}}),
        }

        # Reading.
        rc, out, err = m.run("read", *everyone)
        check("read: exit 0", rc == 0)
        check("read: says nothing", out == "" and err == "")
        u = m.reading(ultra)
        check("read: the week of a subscription", u and u["weekly"]["used"] == 30 and abs(u["weekly"]["resets"] - week_ends) < 1000)
        check("read: no 5-hour window", u and u["window"] is None)
        mx = m.reading(maxed)
        check("read: a 5-hour window", mx and mx["window"]["used"] == 48)
        check("read: from the endpoint", mx and mx["source"] == "endpoint")
        p = m.reading(pro)
        check("read: the older answer, without limits", p and p["weekly"]["used"] == 10 and p["window"]["used"] == 5)
        g = m.reading(gw)
        check("read: a gateway budget", g and g["budget"]["spend"] == 3.2 and g["budget"]["max"] == 25.0)
        check("read: the gateway at its origin after a 404 under its URL",
              [r["path"] for r in Server.requests if r["token"] == "G-token"] == ["/gw/key/info", "/key/info"])
        check("read: nothing for an Anthropic API key", m.reading(api) is None)
        check("read: nothing for a key in headers", m.reading(headers_only) is None)
        usage_requests = [r for r in Server.requests if r["path"] == "/api/oauth/usage"]
        check("read: the beta header", all(r["headers"].get("anthropic-beta") == "oauth-2025-04-20" for r in usage_requests))
        check("read: says who reads", all(r["headers"].get("user-agent") == "claude-switch" for r in Server.requests))
        check("read: each login to its own reading only", {r["token"] for r in usage_requests} == {"U-token", "M-token", "P-token"})
        check("read: no API key goes anywhere", not any(r["token"] == "K-token" for r in Server.requests))
        mode = (m.store / ".usage").stat().st_mode & 0o777
        check("read: the folder is private", mode == 0o700)
        check("read: the files are private",
              all(path.stat().st_mode & 0o777 == 0o600 for path in (m.store / ".usage").glob("*.json")))

        count = len(Server.requests)
        m.run("read", *everyone)
        check("read: fresh readings are not read again", len(Server.requests) == count)
        m.run("read", "--max-age", "0", *everyone)
        check("read: --max-age 0 reads again", len(Server.requests) > count)
        path = m.store / ".usage" / f"{pathlib.Path(ultra).stem}.json"
        old = json.loads(path.read_text())
        path.write_text(json.dumps({**old, "at": now_ms() - 2000, "weekly": {"used": 100, "resets": now_ms() - 1000}}))
        Server.requests.clear()
        m.run("read", ultra)
        check("read: a week that reset since is read again at once", [r["token"] for r in Server.requests] == ["U-token"])

        # A login never goes to a gateway, ANTHROPIC_BASE_URL included.
        Server.requests.clear()
        environment = dict(m.env)
        m.env = {**{k: v for k, v in environment.items() if k != "CLAUDE_SWITCH_ANTHROPIC_URL"},
                 "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}", "https_proxy": "http://127.0.0.1:9",
                 "HTTPS_PROXY": "http://127.0.0.1:9"}
        m.run("read", "--max-age", "0", "--timeout", "2", ultra, maxed)
        m.env = environment
        check("a login never goes to ANTHROPIC_BASE_URL", not any(r["path"] == "/api/oauth/usage" for r in Server.requests))
        m.run("read", "--max-age", "0", *everyone)

        # Claude Code's own reading of the account it is on goes first while it's the newer one.
        Server.requests.clear()
        (m.home / ".claude.json").write_text(json.dumps({"cachedUsageUtilization": {
            "fetchedAtMs": now_ms(), "accountUuid": "uuid-max",
            "utilization": usage(week=(77, now + 3 * DAY), window=(10, now + HOUR))}}))
        m.run("read", "--max-age", "60", maxed)
        check("Claude Code's reading: taken", m.reading(maxed)["weekly"]["used"] == 77)
        check("Claude Code's reading: no call", not Server.requests)
        (m.home / ".claude.json").write_text(json.dumps({"cachedUsageUtilization": {
            "fetchedAtMs": now_ms(), "accountUuid": "uuid-other", "utilization": usage(week=(99, now + DAY))}}))
        m.run("read", "--max-age", "0", maxed)
        check("Claude Code's reading of another account: not taken", m.reading(maxed)["weekly"]["used"] == 51)

        # A refused reading keeps what was known, and isn't tried again at once.
        Server.answers[("/api/oauth/usage", "M-token")] = (429, {"error": {"type": "rate_limit_error"}})
        m.run("read", "--max-age", "0", maxed)
        mx = m.reading(maxed)
        check("429: the last values stay", mx["weekly"]["used"] == 51)
        check("429: said in the reading", mx["error"] == "HTTP 429")
        # The values are an hour old, the refusal is new: no new call before the max age.
        path = m.store / ".usage" / f"{pathlib.Path(maxed).stem}.json"
        path.write_text(json.dumps({**mx, "at": now_ms() - HOUR}))
        Server.requests.clear()
        m.run("read", maxed)
        check("429: not tried again within the max age", not Server.requests)
        Server.answers[("/api/oauth/usage", "M-token")] = (200, usage(week=(51, now + 3 * DAY), window=(48, now + 2 * HOUR)))

        # An access token that ran out is left to the refresh.
        stale = m.account("stale", "s@example.com", "default_claude_max_5x", "S-token", expires=now - HOUR)
        Server.requests.clear()
        m.run("read", stale)
        check("expired token: no call", not Server.requests)
        check("expired token: said", "ran out" in (m.reading(stale) or {}).get("error", ""))
        os.remove(stale)

        # What each account has left, for list and the prompt.
        rc, out, _ = m.run("left", *everyone)
        left = dict(line.split("\t", 1) for line in out.splitlines())
        when = datetime.datetime.fromtimestamp(week_ends / 1000).strftime("%a %H:%M")
        check("left: the week of a subscription", left.get(ultra) == f"70% until {when}")
        check("left: a gateway budget", left.get(gw, "").startswith("$21.80 until "))
        check("left: an Anthropic API key is not readable", left.get(api) == "not readable")
        check("left: a gateway key in headers only, nothing read", left.get(headers_only) == "-")
        direct = m.key("direct", {"ANTHROPIC_API_KEY": "D-token", "ANTHROPIC_BASE_URL": "https://api.anthropic.com"})
        rc, out, _ = m.run("left", direct)
        check("left: a key whose URL is the Anthropic API is not readable", out.strip().endswith("\tnot readable"))
        os.remove(direct)
        check("left: one line per account", len(left) == len(everyone))

        # The best account with room: Ultra, then a key, then a plan with a 5-hour window.
        rc, out, _ = m.run("best", *routable)
        check("best: Ultra first", out.strip() == ultra)
        # Between two accounts of the same kind, the one the machine is on stays while it has room.
        ultra2 = m.account("ultra2", "u2@example.com", "default_claude_max_20x", "U2-token")
        Server.answers[("/api/oauth/usage", "U2-token")] = (200, usage(week=(60, now + DAY)))
        m.run("read", "--max-age", "0", ultra2)
        rc, out, _ = m.run("best", ultra, ultra2, maxed)
        check("best: of two Ultras, the week that resets first", out.strip() == ultra2)
        rc, out, _ = m.run("best", "--active", ultra, ultra, ultra2, maxed)
        check("best: of two Ultras, the one the machine is on", out.strip() == ultra)
        rc, out, _ = m.run("best", "--active", maxed, ultra, ultra2, maxed)
        check("best: on an account of another kind, the kinds keep their order", out.strip() == ultra2)
        os.remove(ultra2)
        os.remove(m.store / ".usage" / f"{pathlib.Path(ultra2).stem}.json")
        full = (200, usage(week=(100, week_ends)))
        Server.answers[("/api/oauth/usage", "U-token")] = full
        m.run("read", "--max-age", "0", ultra)
        rc, out, _ = m.run("best", *routable)
        check("best: a full Ultra, the gateway key with a budget that resets", out.strip() == gw)
        rc, out, _ = m.run("left", ultra)
        check("left: a full week", out.strip().endswith(f"none until {when}"))
        rc, out, _ = m.run("why", ultra)
        check("why: a full week", rc == 0 and out.strip() == f"reached its weekly limit, until {when}")
        # An hour old, and no failed read since: only being full keeps it from being read.
        path = m.store / ".usage" / f"{pathlib.Path(ultra).stem}.json"
        old = json.loads(path.read_text())
        old.pop("errorAt", None)
        path.write_text(json.dumps({**old, "at": now_ms() - HOUR}))
        Server.requests.clear()
        m.run("read", ultra)
        check("a full account is not read again before its reset", not Server.requests)
        Server.answers[("/key/info", "G-token")] = (200, {"info": {"spend": 25.5, "max_budget": 25.0,
                                                                  "budget_reset_at": iso(now + 10 * DAY)}})
        m.run("read", "--max-age", "0", gw)
        rc, out, _ = m.run("best", *routable)
        check("best: a spent budget, the next key", out.strip() == api)
        rc, out, _ = m.run("why", gw)
        check("why: a spent budget", out.startswith("spent its budget, until "))
        m.run("refuse", api, "billing_error", "Credit balance is too low")
        rc, out, _ = m.run("best", *routable)
        check("best: keys out, the plans with a 5-hour window, the week that resets first", out.strip() == pro)
        rc, out, _ = m.run("why", api)
        check("why: no credit, for a day", out.startswith("ran out of credit, until "))
        rc, out, _ = m.run("best", "--hold", maxed, *routable)
        check("best: a hold with room wins", out.strip() == maxed)
        rc, out, _ = m.run("best", "--hold", ultra, *routable)
        check("best: a hold without room doesn't", out.strip() == pro)

        # A refusal holds until the limit that stopped the session resets.
        Server.answers[("/api/oauth/usage", "P-token")] = (200, usage(week=(40, now + DAY), window=(100, now + HOUR)))
        rc, out, _ = m.run("refuse", pro, "rate_limit", "You've hit your 5-hour limit")
        # An hour from now is tomorrow in the last hour of the day: then it shows with its day.
        moment = datetime.datetime.fromtimestamp((now + HOUR) / 1000)
        hour = moment.strftime("%H:%M" if moment.date() == datetime.date.today() else "%a %H:%M")
        check("refuse: names the 5-hour window and its end", rc == 0 and out.strip() == f"reached its 5-hour limit, until {hour}")
        refusal = json.loads((m.store / ".usage" / f"{pathlib.Path(pro).stem}.refused.json").read_text())
        check("refuse: held until the window resets", abs(refusal["until"] - (now + HOUR)) < 1000)
        check("refuse: the file is private", (m.store / ".usage" / f"{pathlib.Path(pro).stem}.refused.json").stat().st_mode & 0o777 == 0o600)
        rc, out, _ = m.run("left", pro)
        check("left: a refusal", out.strip().endswith(f"refused until {hour}"))
        rc, out, _ = m.run("best", *routable)
        check("best: past the refused one", out.strip() == maxed)
        Server.answers[("/api/oauth/usage", "M-token")] = (200, usage(
            week=(60, now + 3 * DAY), window=(10, now + HOUR), scoped=[("Fable", 100, now + 3 * DAY)]))
        rc, out, _ = m.run("refuse", maxed, "rate_limit", "")
        check("refuse: a model's weekly limit", out.startswith("reached its Fable limit, until "))
        Server.answers[("/api/oauth/usage", "M-token")] = (500, {})
        os.remove(m.store / ".usage" / f"{pathlib.Path(maxed).stem}.refused.json")
        os.remove(m.store / ".usage" / f"{pathlib.Path(maxed).stem}.json")
        rc, out, _ = m.run("refuse", maxed, "rate_limit", "")
        refusal = json.loads((m.store / ".usage" / f"{pathlib.Path(maxed).stem}.refused.json").read_text())
        check("refuse: no reading, a quarter of an hour", out.strip().startswith("hit a usage limit")
              and abs(refusal["until"] - (now_ms() + 15 * 60000)) < 60000)
        rc, out, _ = m.run("refuse", gw, "rate_limit", "")
        check("refuse: a key's rate limit, a few minutes", out.startswith("hit a rate limit"))
        rc, out, _ = m.run("refuse", gw, "budget", "Budget has been exceeded!")
        check("refuse: a budget, until it resets", out.startswith("spent its budget, until ")
              and json.loads((m.store / ".usage" / "key_gw.refused.json").read_text())["until"] > now + 9 * DAY)
        rc, _, err = m.run("refuse", gw, "made_up")
        check("refuse: an unknown reason", rc == 2)
        rc, out, _ = m.run("best", *routable)
        check("best: nothing has room", rc == 1 and out == "")
        rc, out, _ = m.run("first", *routable)
        check("first: the account that has room again first", rc == 0 and out.startswith("max, "))

        # Signed out or ended: never chosen.
        for path in everyone:
            for suffix in (".json", ".refused.json"):
                target = m.store / ".usage" / f"{pathlib.Path(path).stem}{suffix}"
                if target.exists():
                    os.remove(target)
        dead = m.account("dead", "d@example.com", "default_claude_max_20x", "D-token", dead="01/11/2026")
        ended = m.account("ended", "e@example.com", "default_claude_max_20x", "E-token", ends=now - DAY)
        rc, out, _ = m.run("best", dead, ended, pro)
        check("best: a signed out or ended Ultra is never chosen", out.strip() == pro)
        rc, out, _ = m.run("left", dead)
        check("left: signed out", out.strip().endswith("signed out"))
        rc, out, _ = m.run("best", ultra, gw, pro)
        check("best: no readings, Ultra by its plan", out.strip() == ultra)
        rc, out, _ = m.run("best", gw, api, pro)
        check("best: no readings, a key before a plan with a window", out.strip() == gw)
        rc, out, _ = m.run("best", "--order", "max,key,ultra", ultra, gw, pro)
        check("best: an order that puts the plans with a window first", out.strip() == pro)
        rc, out, _ = m.run("best", "--order", "key", ultra, gw, pro)
        check("best: a key first, the rest in their own order", out.strip() == gw)
        rc, out, _ = m.run("best", "--order", "key", ultra, pro)
        check("best: no key, Ultra next", out.strip() == ultra)

        # A session is open while its process runs, started at the moment the session file says.
        sessions = m.home / ".claude" / "sessions"
        rc, _, _ = m.run("sessions")
        check("sessions: none", rc == 1)
        stat = pathlib.Path(f"/proc/{os.getpid()}/stat").read_text().rpartition(")")[2].split()
        (sessions / f"{os.getpid()}.json").write_text(json.dumps({"pid": os.getpid(), "procStart": stat[19]}))
        rc, _, _ = m.run("sessions")
        check("sessions: a running one", rc == 0)
        (sessions / f"{os.getpid()}.json").write_text(json.dumps({"pid": os.getpid(), "procStart": "1"}))
        rc, _, _ = m.run("sessions")
        check("sessions: a pid taken by another process", rc == 1)
        gone = subprocess.Popen(["true"])
        gone.wait()
        (sessions / "gone.json").write_text(json.dumps({"pid": gone.pid, "procStart": "1"}))
        (sessions / "junk.json").write_text("not json")
        rc, _, _ = m.run("sessions")
        check("sessions: a process that ended, a broken file", rc == 1)
        for path in sessions.glob("*.json"):
            path.unlink()
        remote_checks(m, sessions)

        # No token ever reaches the output.
        everything = "".join(m.run(*command)[1] + m.run(*command)[2] for command in
                             (("read", "--max-age", "0", *everyone), ("left", *everyone), ("best", *everyone)))
        check("no token in the output", not any(token in everything for token in ("U-token", "M-token", "G-token", "K-token")))
    server.shutdown()
    print(f"{passes} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
