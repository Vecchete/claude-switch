#!/usr/bin/env python3
"""Tests of usage.py on a scratch HOME, against a stand-in usage endpoint and a stand-in gateway on this machine.
Usage: test-usage.py USAGE_PY"""
import datetime
import http.server
import json
import os
import pathlib
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

        # No token ever reaches the output.
        everything = "".join(m.run(*command)[1] + m.run(*command)[2] for command in
                             (("read", "--max-age", "0", *everyone), ("left", *everyone), ("best", *everyone)))
        check("no token in the output", not any(token in everything for token in ("U-token", "M-token", "G-token", "K-token")))
    server.shutdown()
    print(f"{passes} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
