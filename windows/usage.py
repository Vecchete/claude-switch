#!/usr/bin/env -S python3 -I
"""What each Claude account has left, the account the machine should be on, and whether a session is open. The
Windows port of share/usage.py, which it follows function by function: a session is told by the creation time of its
process, JSON is read as UTF-8, a reading another run has open is replaced once it lets go, and claude-switch gets
UTF-8.

A claude.ai account is read from the endpoint Claude Code's /usage reads, with the account's own access token.
Claude Code keeps its last reading of the account it is on in ~/.claude.json, which goes first while it is the
newer one. A gateway key is read from the /key/info of its gateway, LiteLLM's, with the key itself. An Anthropic
API key has nothing to read: Anthropic gives out no remaining credit, so it counts as available until a request
says otherwise. Nothing here sends a login anywhere but to the one that issued it.

The readings are kept in ~/.claude-accounts/.usage, one file per account, and a refusal a session met beside it.
Only you can read them.

Only the week counts. The best account with room is of the first kind in the order, by default: ultra, a
subscription with no 5-hour window, then key, an API or gateway key, then max, a subscription with a 5-hour window
(Max, Pro, Team). Within a kind, what expires first goes first: the week that resets soonest, a gateway budget that
resets before one that doesn't, credit nobody can read last.

    usage.py sessions                     exit 0 when Claude Code has a session open here, 1 when not
    usage.py read [--max-age S] [--timeout S] FILE...
                                          read again the accounts whose reading is older than S seconds (300)
    usage.py left FILE...                 FILE, a tab and what it has left, one line each
    usage.py best [--hold FILE] [--order KIND,...] FILE...
                                          the account the machine should be on, exit 1 when none has room
    usage.py refuse FILE REASON [MESSAGE] FILE refused a session for REASON: rate_limit, billing_error, budget or
                                          authentication_failed. Prints why, as a phrase
    usage.py why FILE                     why FILE can't be used now, as a phrase, exit 1 when it can
    usage.py first FILE...                the account that has room again first, and when
"""
import argparse
import concurrent.futures
import datetime
import json
import math
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

WINDOWS = os.name == "nt"
HOME = pathlib.Path.home()
STORE = HOME / ".claude-accounts"
USAGE = STORE / ".usage"
CLAUDE_JSON = HOME / ".claude.json"
SESSIONS = HOME / ".claude" / "sessions"
# Where the usage of a claude.ai account is read. Never ANTHROPIC_BASE_URL: a gateway must not get a login.
ANTHROPIC = (os.environ.get("CLAUDE_SWITCH_ANTHROPIC_URL") or "https://api.anthropic.com").rstrip("/")
AGENT = "claude-switch"
KEY_ENV = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL",
           "ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
           "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_CUSTOM_HEADERS")
ULTRA_TIER = "default_claude_max_20x"
# The kinds of account, in the order they rank by default.
KINDS = ("ultra", "key", "max")
# How long a refusal holds when nothing says when it ends.
MINUTE = 60 * 1000
HOLDS = {"limit": 15 * MINUTE, "rate": 5 * MINUTE, "credit": 24 * 60 * MINUTE, "budget": 60 * MINUTE,
         "sign-in": 60 * MINUTE}


def now_ms():
    return int(time.time() * 1000)


def load(path):
    """The JSON object in PATH, None when there is none. UTF-8, whatever the code page of Windows, and a BOM a Windows
    editor may have added is skipped."""
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def replace(source, target):
    """os.replace. On Windows a file another run reads can't be replaced for a moment, so it tries again."""
    for _ in range(49 if WINDOWS else 0):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            time.sleep(0.1)
    os.replace(source, target)


def write_private(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data))
    replace(temporary, path)


def reading_path(file):
    return USAGE / f"{pathlib.Path(file).stem}.json"


def refusal_path(file):
    return USAGE / f"{pathlib.Path(file).stem}.refused.json"


def number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def epoch_ms(value):
    """The time an ISO 8601 date names, in milliseconds, None when it names none."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00").replace("z", "+00:00")
    # Python before 3.11 takes 3 or 6 digits of a second only.
    if "." in text:
        head, _, rest = text.partition(".")
        digits = len(rest) - len(rest.lstrip("0123456789"))
        text = f"{head}.{(rest[:digits] + '000000')[:6]}{rest[digits:]}"
    try:
        moment = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return int(moment.timestamp() * 1000)


def is_key(account):
    return account.get("kind") == "key"


def key_env(account):
    env = account.get("env") if isinstance(account.get("env"), dict) else {}
    return {name: value for name, value in env.items() if name in KEY_ENV and isinstance(value, str) and value}


def gateway_of(account):
    """The URL of the gateway a key goes to, None for an Anthropic API key or a key in headers only."""
    env = key_env(account)
    base = env.get("ANTHROPIC_BASE_URL")
    if not base or not (env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY")):
        return None
    host = urllib.parse.urlsplit(base).hostname or ""
    if host == "api.anthropic.com" or host.endswith(".anthropic.com"):
        return None
    return base.rstrip("/")


def label(account):
    if is_key(account):
        return account.get("name") or "a gateway key"
    return account.get("name") or (account.get("oauthAccount") or {}).get("emailAddress") or "an account"


def when(moment):
    """A time to come, as a person reads it: 17:20 today, Fri 22:00 this week, 01/12 later."""
    local = datetime.datetime.fromtimestamp(moment / 1000)
    days = (local.date() - datetime.date.today()).days
    if days <= 0:
        return local.strftime("%H:%M")
    if days < 7:
        return local.strftime("%a %H:%M")
    return local.strftime("%d/%m")


def get_json(url, headers, timeout):
    request = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "application/json", **headers})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(1_000_000))


def failure(error):
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    if isinstance(error, (urllib.error.URLError, OSError)):
        return "no answer"
    return "an answer it can't read"


def parse_usage(data):
    """The week, the 5-hour window and the scoped weekly limits, from what the usage endpoint answers. A limit
    is {used: percent, resets: ms}. The window is None when the account has none: a verified Ultra account."""
    if not isinstance(data, dict):
        raise ValueError("not an object")
    weekly = window = None
    scoped = []
    limits = data.get("limits")
    if isinstance(limits, list):
        for limit in limits:
            if not isinstance(limit, dict):
                continue
            entry = {"used": number(limit.get("percent")), "resets": epoch_ms(limit.get("resets_at"))}
            kind = limit.get("kind")
            if kind == "weekly_all":
                weekly = entry
            elif kind == "session":
                window = entry
            elif kind == "weekly_scoped":
                model = ((limit.get("scope") or {}).get("model") or {}) if isinstance(limit.get("scope"), dict) else {}
                entry["name"] = model.get("display_name") or model.get("id") or "a model"
                scoped.append(entry)
    if weekly is None and isinstance(data.get("seven_day"), dict):
        weekly = {"used": number(data["seven_day"].get("utilization")),
                  "resets": epoch_ms(data["seven_day"].get("resets_at"))}
    if not isinstance(limits, list) and isinstance(data.get("five_hour"), dict):
        window = {"used": number(data["five_hour"].get("utilization")),
                  "resets": epoch_ms(data["five_hour"].get("resets_at"))}
    if weekly is None or weekly["used"] is None:
        raise ValueError("no week in it")
    return {"weekly": weekly, "window": window, "scoped": scoped}


def claude_cache(account):
    """Claude Code's own last reading of ACCOUNT, with the time it was taken, or None."""
    config = load(CLAUDE_JSON) or {}
    cached = config.get("cachedUsageUtilization")
    uuid = (account.get("oauthAccount") or {}).get("accountUuid")
    if not isinstance(cached, dict) or not uuid or cached.get("accountUuid") != uuid:
        return None
    try:
        reading = parse_usage(cached.get("utilization"))
    except ValueError:
        return None
    at = cached.get("fetchedAtMs")
    return (int(at), reading) if isinstance(at, (int, float)) else None


def read_claude(account, timeout):
    login = (account.get("login") or {}).get("claudeAiOauth") or {}
    token = login.get("accessToken")
    if not token or (login.get("expiresAt") or 0) <= now_ms() + MINUTE:
        raise LookupError("its access token ran out, the refresh renews it")
    data = get_json(f"{ANTHROPIC}/api/oauth/usage", {"Authorization": f"Bearer {token}",
                                                      "anthropic-beta": "oauth-2025-04-20"}, timeout)
    return parse_usage(data)


def custom_headers(env):
    headers = {}
    for line in env.get("ANTHROPIC_CUSTOM_HEADERS", "").splitlines():
        name, _, value = line.partition(":")
        if name.strip() and value.strip():
            headers[name.strip()] = value.strip()
    return headers


def read_gateway(account, timeout):
    env = key_env(account)
    base = gateway_of(account)
    token = env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY")
    headers = {**custom_headers(env), "Authorization": f"Bearer {token}"}
    if "ANTHROPIC_AUTH_TOKEN" not in env:
        headers["x-api-key"] = token
    parts = urllib.parse.urlsplit(base)
    urls = [f"{base}/key/info"]
    origin = f"{parts.scheme}://{parts.netloc}/key/info"
    if origin not in urls:
        urls.append(origin)
    for url in urls:
        try:
            data = get_json(url, headers, timeout)
        except urllib.error.HTTPError as error:
            if error.code == 404 and url != urls[-1]:
                continue
            raise
        info = data.get("info") if isinstance(data, dict) else None
        if not isinstance(info, dict):
            raise ValueError("no key info in it")
        return {"budget": {"spend": number(info.get("spend")) or 0.0, "max": number(info.get("max_budget")),
                           "resets": epoch_ms(info.get("budget_reset_at")), "blocked": bool(info.get("blocked"))}}
    raise ValueError("no key info")


def full_until(reading, now):
    """When a reading says the account has room again, None when it has room now."""
    if not reading:
        return None
    weekly = reading.get("weekly")
    if weekly and weekly.get("used") is not None and weekly["used"] >= 100:
        if weekly.get("resets") is None or weekly["resets"] > now:
            return weekly.get("resets") or now + HOLDS["limit"]
    budget = reading.get("budget")
    if budget and (budget.get("blocked") or (budget.get("max") is not None and budget["spend"] >= budget["max"])):
        if budget.get("resets") is None or budget["resets"] > now:
            return budget.get("resets") or now + HOLDS["budget"]
    return None


def reset_since(reading, now):
    """True when a limit the reading knows has reset since it was taken: what it says is over."""
    at = reading.get("at") or 0
    for entry in (reading.get("weekly"), reading.get("window"), reading.get("budget")):
        if entry and entry.get("resets") and at < entry["resets"] <= now:
            return True
    return False


def read_one(file, account, previous, timeout):
    """Reads FILE from its endpoint or its gateway. A reading that fails keeps what was known, and says why."""
    now = now_ms()
    reading = dict(previous)
    try:
        if is_key(account):
            reading.update(read_gateway(account, timeout), at=now, source="gateway", error=None)
        else:
            reading.update(read_claude(account, timeout), at=now, source="endpoint", error=None)
    except LookupError as error:
        reading.update(error=str(error), errorAt=now)
    except Exception as error:
        reading.update(error=failure(error), errorAt=now)
    write_private(reading_path(file), reading)


def latest(file, account):
    """The reading of FILE, with Claude Code's own when that one is newer, which costs nothing."""
    reading = load(reading_path(file)) or {}
    cached = None if is_key(account) else claude_cache(account)
    if cached and cached[0] > (reading.get("at") or 0):
        reading = {**reading, **cached[1], "at": cached[0], "source": "claude", "error": None}
        write_private(reading_path(file), reading)
    return reading


def command_read(arguments):
    now = now_ms()
    jobs = []
    for file in arguments.files:
        account = load(file)
        if account is None or (is_key(account) and gateway_of(account) is None):
            continue
        previous = latest(file, account)
        newest = max(previous.get("at") or 0, previous.get("errorAt") or 0)
        if now - newest < arguments.max_age * 1000 and not reset_since(previous, now):
            continue
        # A full account has nothing new to say before its limit resets.
        if full_until(previous, now) and previous.get("at") and not previous.get("error"):
            continue
        jobs.append((file, account, previous))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda job: read_one(*job, arguments.timeout), jobs))
    return 0


def refused_until(file, now):
    refusal = load(refusal_path(file))
    if refusal and isinstance(refusal.get("until"), (int, float)) and refusal["until"] > now:
        return refusal
    return None


def signed_out(account, now):
    if is_key(account):
        return False
    login = (account.get("login") or {}).get("claudeAiOauth") or {}
    ends = login.get("refreshTokenExpiresAt") or 0
    return bool(account.get("dead")) or not login.get("refreshToken") or (0 < ends < now)


def no_window(account, reading):
    """True for a subscription without a 5-hour window: an Ultra plan, or one whose reading shows none. An Ultra
    account that isn't verified yet keeps a window, which a refusal then says."""
    tier = ((account.get("oauthAccount") or {}).get("organizationRateLimitTier")
            or ((account.get("login") or {}).get("claudeAiOauth") or {}).get("rateLimitTier"))
    if tier == ULTRA_TIER:
        return True
    return bool(reading and reading.get("weekly") is not None and reading.get("window") is None)


def kind_of(account, reading):
    if is_key(account):
        return "key"
    return "ultra" if no_window(account, reading) else "max"


def parse_order(text):
    """The kinds in the order TEXT gives them, the ones it leaves out after, in their own order."""
    order = []
    for kind in (part.strip().lower() for part in (text or "").split(",")):
        if kind in KINDS and kind not in order:
            order.append(kind)
    return order + [kind for kind in KINDS if kind not in order]


def rank(file, account, reading, now, order=KINDS):
    """(the place of its kind in the order, then what expires first) for an account with room, smaller first."""
    tier = order.index(kind_of(account, reading))
    if is_key(account):
        budget = (reading or {}).get("budget")
        if budget and budget.get("max") is not None:
            expires = (0, budget["resets"]) if budget.get("resets") else (1, 0)
        elif budget:
            expires = (2, 0)
        else:
            # A gateway that gave no budget yet, before credit nobody can read.
            expires = (3, 0) if gateway_of(account) else (4, 0)
        return (tier,) + expires + (pathlib.Path(file).name,)
    weekly = (reading or {}).get("weekly") or {}
    resets = weekly.get("resets")
    expires = (0, resets, weekly.get("used") or 0) if resets and resets > now else (1, 0, 0)
    return (tier,) + expires + (pathlib.Path(file).name,)


def usable(file, account, now):
    if signed_out(account, now) or refused_until(file, now):
        return False
    return full_until(load(reading_path(file)), now) is None


def command_best(arguments):
    now = now_ms()
    accounts = {file: load(file) for file in arguments.files}
    if arguments.hold and accounts.get(arguments.hold) and usable(arguments.hold, accounts[arguments.hold], now):
        print(arguments.hold)
        return 0
    order = parse_order(arguments.order)
    ranked = sorted((rank(file, account, load(reading_path(file)), now, order), file)
                    for file, account in accounts.items() if account and usable(file, account, now))
    if not ranked:
        return 1
    print(ranked[0][1])
    return 0


def left_text(file, account, now):
    refusal = refused_until(file, now)
    if refusal:
        return f"refused until {when(refusal['until'])}"
    if signed_out(account, now):
        return "signed out"
    reading = load(reading_path(file)) or {}
    if is_key(account):
        budget = reading.get("budget")
        if not budget:
            return "-"
        if budget.get("max") is None:
            return "no limit"
        remaining = budget["max"] - budget["spend"]
        text = f"${remaining:.2f}" if remaining > 0 and not budget.get("blocked") else "none"
        return f"{text} until {when(budget['resets'])}" if budget.get("resets") and budget["resets"] > now else text
    weekly = reading.get("weekly")
    if not weekly or weekly.get("used") is None:
        return "-"
    if weekly.get("resets") and weekly["resets"] <= now:
        return "-"
    remaining = max(0.0, 100 - weekly["used"])
    text = f"{math.floor(remaining)}%" if remaining >= 1 else "none"
    return f"{text} until {when(weekly['resets'])}" if weekly.get("resets") else text


def command_left(arguments):
    now = now_ms()
    for file in arguments.files:
        account = load(file)
        print(f"{file}\t{left_text(file, account, now) if account else '-'}")
    return 0


def phrase(text, until):
    return f"{text}, until {when(until)}" if until else text


def why_text(file, account, now):
    refusal = refused_until(file, now)
    if refusal:
        return phrase(refusal.get("phrase") or "refused a session", refusal["until"])
    if signed_out(account, now):
        return "is signed out"
    reading = load(reading_path(file))
    until = full_until(reading, now)
    if until is None:
        return None
    return phrase("spent its budget" if reading.get("budget") else "reached its weekly limit", until)


def command_why(arguments):
    account = load(arguments.file)
    text = why_text(arguments.file, account, now_ms()) if account else None
    if text is None:
        return 1
    print(text)
    return 0


def binding_limit(reading, now):
    """The limit that stops the account now, as (phrase, until), the one that ends last when several do."""
    found = []
    for name, entry in (("weekly", reading.get("weekly")), ("5-hour", reading.get("window"))):
        if entry and entry.get("used") is not None and entry["used"] >= 100 and (entry.get("resets") or 0) > now:
            found.append((entry["resets"], f"reached its {name} limit"))
    for entry in reading.get("scoped") or []:
        if entry.get("used") is not None and entry["used"] >= 100 and (entry.get("resets") or 0) > now:
            found.append((entry["resets"], f"reached its {entry.get('name') or 'model'} limit"))
    if not found:
        return None
    until, text = max(found)
    return text, until


def command_refuse(arguments):
    file, reason = arguments.file, arguments.reason
    account = load(file)
    if account is None:
        print(f"{file} is not an account.", file=sys.stderr)
        return 2
    now = now_ms()
    text, until = "hit a usage limit", now + HOLDS["limit"]
    if reason == "rate_limit" and is_key(account):
        text, until = "hit a rate limit", now + HOLDS["rate"]
    elif reason == "rate_limit":
        read_one(file, account, latest(file, account), arguments.timeout)
        binding = binding_limit(load(reading_path(file)) or {}, now)
        if binding:
            text, until = binding
    elif reason == "billing_error":
        text, until = "ran out of credit", now + HOLDS["credit"]
    elif reason == "budget":
        text, until = "spent its budget", now + HOLDS["budget"]
        if gateway_of(account):
            read_one(file, account, latest(file, account), arguments.timeout)
            budget = (load(reading_path(file)) or {}).get("budget") or {}
            if budget.get("resets") and budget["resets"] > now:
                until = budget["resets"]
    elif reason == "authentication_failed":
        text, until = "had its sign-in refused", now + HOLDS["sign-in"]
    else:
        print(f"{reason} is not a reason this knows.", file=sys.stderr)
        return 2
    write_private(refusal_path(file), {"until": until, "reason": reason, "phrase": text, "at": now})
    print(phrase(text, until))
    return 0


def command_first(arguments):
    now = now_ms()
    soonest = None
    for file in arguments.files:
        account = load(file)
        if not account or signed_out(account, now):
            continue
        refusal = refused_until(file, now)
        until = max(refusal["until"] if refusal else 0, full_until(load(reading_path(file)), now) or 0)
        if until and (soonest is None or until < soonest[0]):
            soonest = (until, label(account))
    if soonest is None:
        return 1
    print(f"{soonest[1]}, {when(soonest[0])}")
    return 0


def started_at(pid):
    """When the process PID started, as Claude Code writes it in procStart, None when no process runs with that pid: the
    start time /proc gives it on Linux, the creation time of the process on Windows, in 100 ns since 1601 (on the PC on
    20/11/2026, the procStart of a running claude.exe was its creation time to the digit)."""
    if not WINDOWS:
        try:
            stat = pathlib.Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            return None
        fields = stat.rpartition(")")[2].split()
        return fields[19] if len(fields) > 19 else ""
    import ctypes
    from ctypes import wintypes
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        # PROCESS_QUERY_LIMITED_INFORMATION, which the processes of this user give without more rights.
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            code = wintypes.DWORD()
            times = [wintypes.FILETIME() for _ in range(4)]
            # A process that ended can still be opened while something holds it: STILL_ACTIVE says it runs.
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
                return None
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(entry) for entry in times)):
                return None
            return str(times[0].dwHighDateTime << 32 | times[0].dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    except Exception:
        return None


def command_sessions(arguments):
    """A session counts while its process runs: the pid it was started with, started at the same moment."""
    for path in SESSIONS.glob("*.json"):
        session = load(path) or {}
        try:
            running = started_at(int(session.get("pid")))
        except (TypeError, ValueError, OverflowError):
            continue
        started = session.get("procStart")
        if running is not None and (started is None or running == str(started)):
            return 0
    return 1


def main(argv):
    parser = argparse.ArgumentParser(prog="usage.py", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("sessions")
    read = commands.add_parser("read")
    read.add_argument("--max-age", type=float, default=300)
    read.add_argument("--timeout", type=float, default=10)
    read.add_argument("files", nargs="*")
    for name in ("left", "first"):
        commands.add_parser(name).add_argument("files", nargs="*")
    best = commands.add_parser("best")
    best.add_argument("--hold")
    best.add_argument("--order", default=",".join(KINDS))
    best.add_argument("files", nargs="*")
    refuse = commands.add_parser("refuse")
    refuse.add_argument("--timeout", type=float, default=10)
    refuse.add_argument("file")
    refuse.add_argument("reason")
    refuse.add_argument("message", nargs="?", default="")
    commands.add_parser("why").add_argument("file")
    arguments = parser.parse_args(argv)
    return {"sessions": command_sessions, "read": command_read, "left": command_left, "best": command_best,
            "refuse": command_refuse, "why": command_why, "first": command_first}[arguments.command](arguments)


if __name__ == "__main__":
    if WINDOWS:
        # The paths and names go to claude-switch in UTF-8, not in the code page of Windows.
        for stream in (sys.stdout, sys.stderr):
            if stream is not None:
                stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
