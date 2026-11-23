#!/usr/bin/env -S python3 -I
"""What each Claude account has left, the account the machine should be on, and whether a session is open.

A claude.ai account is read from the endpoint Claude Code's /usage reads, with the account's own access token.
Claude Code keeps its last reading of the account it is on in ~/.claude.json, which goes first while it is the
newer one. A gateway key is read from the /key/info of its gateway, LiteLLM's, with the key itself. An Anthropic
API key has nothing to read: Anthropic gives out no remaining credit, so left says it's not readable, and it
counts as available until a request says otherwise. Nothing here sends a login anywhere but to the one that
issued it.

The readings are kept in ~/.claude-accounts/.usage, one file per account, and a refusal a session met beside it.
Only you can read them.

Only the week counts. The best account with room is of the first kind in the order, by default: ultra, a
subscription with no 5-hour window, then key, an API or gateway key, then max, a subscription with a 5-hour window
(Max, Pro, Team). Within a kind, the account the machine is on stays while it has room, so one runs out before the
next one starts. Among the others, what expires first goes first: the week that resets soonest, a gateway budget that
resets before one that doesn't, credit nobody can read last.

    usage.py sessions                     exit 0 when Claude Code has a session open here, 1 when not
    usage.py read [--max-age S] [--timeout S] FILE...
                                          read again the accounts whose reading is older than S seconds (300)
    usage.py left FILE...                 FILE, a tab and what it has left, one line each
    usage.py best [--hold FILE] [--active FILE] [--order KIND,...] FILE...
                                          the account the machine should be on, exit 1 when none has room
    usage.py refuse FILE REASON [MESSAGE] FILE refused a session for REASON: rate_limit, billing_error, budget or
                                          authentication_failed. Prints why, as a phrase
    usage.py why FILE                     why FILE can't be used now, as a phrase, exit 1 when it can
    usage.py first FILE...                the account that has room again first, and when
    usage.py remote save                  remember the sessions with Remote Control on, before a switch to another
                                          claude.ai account takes it from them, and bind Ctrl+X Ctrl+Q to
                                          /remote-control in ~/.claude/keybindings.json
    usage.py remote restore               run /remote-control in each of them once it dropped Remote Control and
                                          waits idle: press Ctrl+X Ctrl+Q at its prompt in its tmux pane, or have the
                                          claude-switch-remote service press it outside tmux. Prints back or lost, a
                                          tab and the name of each session, exit 1 when another restore runs
    usage.py remote held                  the sessions with Remote Control on that restore can't reach, outside tmux
                                          without the service, one name a line, exit 1 when there are none
"""
import argparse
import concurrent.futures
import datetime
import fcntl
import json
import math
import os
import pathlib
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

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
# The sessions a switch took Remote Control from, and how far bringing it back went.
REMOTE = STORE / ".remote.json"
# In seconds: how long a session has to drop Remote Control after the switch, which Claude Code does within seconds,
# how long it has to be back once /remote-control was run, and how long a session is waited for to be idle. It's
# run 3 times at most: a dialog can be open when the key is pressed outside tmux, where nothing shows it.
REMOTE_DROP = float(os.environ.get("CLAUDE_SWITCH_REMOTE_DROP") or 60)
REMOTE_BACK = float(os.environ.get("CLAUDE_SWITCH_REMOTE_BACK") or 30)
REMOTE_WAIT = float(os.environ.get("CLAUDE_SWITCH_REMOTE_WAIT") or 900)
REMOTE_TRIES = 3
# The prompt of Claude Code, where the cursor waits when nothing is typed.
PROMPTS = (chr(0x276F), ">")
# The key that runs /remote-control in the Chat context of Claude Code, whatever is typed at the prompt, and does
# nothing elsewhere. Claude Code reads its keybindings again when the file changes.
KEYBINDINGS = HOME / ".claude" / "keybindings.json"
CHORD = "ctrl+x ctrl+q"
COMMAND = "command:remote-control"
# The claude-switch-remote service, which runs as root: only root can press a key in a session outside tmux.
REMOTE_SOCKET = os.environ.get("CLAUDE_SWITCH_REMOTE_SOCKET") or "/run/claude-switch-remote.sock"


def now_ms():
    return int(time.time() * 1000)


def load(path):
    try:
        data = json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_private(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
        handle.write(json.dumps(data))
    os.replace(temporary, path)


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
    if not base or not (env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY")) or anthropic_url(base):
        return None
    return base.rstrip("/")


def anthropic_url(base):
    host = urllib.parse.urlsplit(base).hostname or ""
    return host == "api.anthropic.com" or host.endswith(".anthropic.com")


def anthropic_api_key(account):
    """True for a key that goes to the Anthropic API itself, whose credit no API gives out."""
    base = key_env(account).get("ANTHROPIC_BASE_URL")
    return is_key(account) and (not base or anthropic_url(base))


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


def rank(file, account, reading, now, order=KINDS, active=None):
    """(the place of its kind in the order, then whether the machine is on it, then what expires first) for an
    account with room, smaller first."""
    place = (order.index(kind_of(account, reading)), 0 if active is not None and file == active else 1)
    if is_key(account):
        budget = (reading or {}).get("budget")
        if budget and budget.get("max") is not None:
            expires = (0, budget["resets"]) if budget.get("resets") else (1, 0)
        elif budget:
            expires = (2, 0)
        else:
            # A gateway that gave no budget yet, before credit nobody can read.
            expires = (3, 0) if gateway_of(account) else (4, 0)
        return place + expires + (pathlib.Path(file).name,)
    weekly = (reading or {}).get("weekly") or {}
    resets = weekly.get("resets")
    expires = (0, resets, weekly.get("used") or 0) if resets and resets > now else (1, 0, 0)
    return place + expires + (pathlib.Path(file).name,)


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
    ranked = sorted((rank(file, account, load(reading_path(file)), now, order, arguments.active), file)
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
            return "not readable" if anthropic_api_key(account) else "-"
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


def live_session(path):
    """The record of a session at PATH while its process runs: the pid it was started with, started at the same
    moment. None for any other."""
    session = load(path)
    if session is None:
        return None
    try:
        stat = pathlib.Path(f"/proc/{int(session.get('pid'))}/stat").read_text()
    except (OSError, TypeError, ValueError):
        return None
    fields = stat.rpartition(")")[2].split()
    started = session.get("procStart")
    if started is None or (len(fields) > 19 and fields[19] == str(started)):
        return session
    return None


def command_sessions(arguments):
    return 0 if any(live_session(path) for path in SESSIONS.glob("*.json")) else 1


def parent_of(pid):
    try:
        return int(pathlib.Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def descends(pid, ancestor):
    """True when the process PID is ANCESTOR or runs under it."""
    for _ in range(64):
        if pid == ancestor:
            return True
        if pid is None or pid <= 1:
            return False
        pid = parent_of(pid)
    return False


def tmux(*arguments):
    try:
        result = subprocess.run(["tmux", *arguments], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def prompt_of(pane, pid):
    """What PANE shows at its cursor while it runs the Claude Code of PID, out of copy mode: empty for the prompt with
    nothing typed, draft for the prompt with text, None for anything else."""
    info = tmux("display-message", "-p", "-t", pane, "#{pane_pid}\t#{cursor_x}\t#{cursor_y}\t#{pane_in_mode}")
    try:
        pane_pid, x, y, mode = info.rstrip("\n").split("\t")
        pane_pid, x, y = int(pane_pid), int(x), int(y)
    except (AttributeError, ValueError):
        return None
    if mode != "0" or not descends(pid, pane_pid):
        return None
    line = tmux("capture-pane", "-p", "-t", pane, "-S", str(y), "-E", str(y))
    if line is None:
        return None
    text = line.rstrip("\n").replace(chr(0xA0), " ").rstrip()
    if text in PROMPTS and x == len(text) + 1:
        return "empty"
    return "draft" if any(text.startswith(f"{prompt} ") for prompt in PROMPTS) else None


def pane_of(session):
    """The tmux pane a session runs in, %N, from the session:@window.%pane Claude Code records, None outside tmux."""
    return str(session.get("tmux") or "").rpartition(".")[2] or None


def name_of(session):
    return str(session.get("name") or session.get("cwd") or session.get("pid"))


def in_its_pane(session):
    """True when the tmux pane of SESSION is there and runs its Claude Code."""
    pane = pane_of(session)
    info = tmux("display-message", "-p", "-t", pane, "#{pane_pid}") if pane else None
    try:
        return descends(session.get("pid"), int(info.strip()))
    except (AttributeError, ValueError):
        return False


def with_remote_control():
    """The records of the running interactive sessions with Remote Control on."""
    for path in SESSIONS.glob("*.json"):
        session = live_session(path)
        if session is not None and session.get("kind") == "interactive" \
                and isinstance(session.get("bridgeSessionId"), str):
            yield session


def type_remote_control(pane):
    if tmux("send-keys", "-t", pane, "-l", "/remote-control") is None:
        return False
    # Text and Enter that arrive together read as a paste, which Claude Code doesn't send.
    time.sleep(0.5)
    return tmux("send-keys", "-t", pane, "Enter") is not None


def press_chord(pane):
    if tmux("send-keys", "-t", pane, "C-x") is None:
        return False
    # Two keys that arrive together read as a paste. Claude Code waits 3 seconds for the second key of a chord.
    time.sleep(0.3)
    return tmux("send-keys", "-t", pane, "C-q") is not None


def spelled(key):
    return " ".join(str(key).lower().replace("control+", "ctrl+").split())


def remote_binding(write=False):
    """Whether ~/.claude/keybindings.json binds CHORD to /remote-control: bound when it does, free when it doesn't yet
    and can, None when it can't, as the file isn't one Claude Code reads or a binding of the user's takes the chord,
    Ctrl+X or Ctrl+Q. With WRITE a free binding is written, and then bound."""
    try:
        data = json.loads(KEYBINDINGS.read_text())
    except FileNotFoundError:
        data = {"$schema": "https://www.schemastore.org/claude-code-keybindings.json",
                "$docs": "https://code.claude.com/docs/en/keybindings", "bindings": []}
    except (OSError, ValueError):
        return None
    blocks = data.get("bindings") if isinstance(data, dict) else None
    if not isinstance(blocks, list) or not all(isinstance(block, dict) and isinstance(block.get("bindings"), dict)
                                               for block in blocks):
        return None
    bound = False
    for block in blocks:
        for key, action in block["bindings"].items():
            key = spelled(key)
            if key == CHORD and action == COMMAND:
                bound = True
            elif key == CHORD or key.startswith(f"{CHORD} ") or (key in ("ctrl+x", "ctrl+q") and action is not None):
                return None
    if bound:
        return "bound"
    if not write:
        return "free"
    chat = next((block for block in blocks if block.get("context") == "Chat"), None)
    if chat is None:
        chat = {"context": "Chat", "bindings": {}}
        blocks.append(chat)
    chat["bindings"][CHORD] = COMMAND
    temporary = KEYBINDINGS.with_name(f"{KEYBINDINGS.name}.{os.getpid()}.tmp")
    try:
        mode = stat.S_IMODE(KEYBINDINGS.stat().st_mode) if KEYBINDINGS.exists() else 0o644
        with open(os.open(temporary, os.O_WRONLY | os.O_TRUNC | os.O_CREAT, 0o600), "w") as handle:
            handle.write(json.dumps(data, indent=2) + "\n")
        os.chmod(temporary, mode)
        os.replace(temporary, KEYBINDINGS)
    except OSError:
        temporary.unlink(missing_ok=True)
        return None
    return "bound"


def own_terminal(pid):
    """True when the process PID has a pseudo-terminal for its controlling terminal."""
    try:
        fields = pathlib.Path(f"/proc/{int(pid)}/stat").read_text().rpartition(")")[2].split()
        return 136 <= os.major(int(fields[4])) <= 143
    except (OSError, TypeError, ValueError, IndexError):
        return False


def root_service():
    try:
        return stat.S_ISSOCK(os.stat(REMOTE_SOCKET).st_mode)
    except OSError:
        return False


def through_root(session, ready=("bound",)):
    """True when the claude-switch-remote service can press the key in SESSION, outside tmux: the key is bound, or
    READY says free will do, and the session has a terminal of its own."""
    return remote_binding() in ready and root_service() and own_terminal(session.get("pid"))


def press_from_root(pid, start):
    """Has the claude-switch-remote service press the key in the session PID that started at START. True once it
    answered, pressed or refused, which counts as a try: what it refuses now, it refuses a second later."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(10)
            connection.connect(REMOTE_SOCKET)
            connection.sendall(f"{int(pid)} {int(start)}\n".encode())
            answer = connection.makefile("rb").readline()
    except (OSError, TypeError, ValueError):
        return False
    return answer.strip() != b""


def press(entry, tries):
    """Runs /remote-control in the session of ENTRY: presses the key in its tmux pane at the prompt, or has the root
    service press it outside tmux. /remote-control is typed at an empty prompt instead when the key isn't bound, or
    when it didn't take. True when it pressed or typed."""
    pane = entry.get("pane")
    if not pane:
        return press_from_root(entry.get("pid"), entry.get("start"))
    bound = remote_binding() == "bound"
    where = prompt_of(pane, entry.get("pid"))
    if where == "empty" and (tries or not bound):
        return type_remote_control(pane)
    return bound and where is not None and press_chord(pane)


def remote_lock(name):
    STORE.mkdir(mode=0o700, parents=True, exist_ok=True)
    return open(os.open(STORE / name, os.O_WRONLY | os.O_CREAT, 0o600), "w")


def command_remote_save(arguments):
    """Remembers the sessions with Remote Control on, before a switch takes it from them: each with its tmux pane, or
    none when it runs elsewhere. A session still waiting from a switch before stays. Binds the key that brings it
    back, which Claude Code reads before the sessions are idle again."""
    now = now_ms()
    with remote_lock(".remote.lock") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        kept = {entry.get("pid"): entry for entry in (load(REMOTE) or {}).get("sessions", []) if isinstance(entry, dict)}
        for session in with_remote_control():
            kept[session.get("pid")] = {"pid": session.get("pid"), "start": session.get("procStart"),
                                        "pane": pane_of(session), "name": name_of(session), "since": now,
                                        "off": False, "typed": None, "tries": 0}
        write_private(REMOTE, {"sessions": list(kept.values())})
    if kept:
        remote_binding(write=True)
    return 0


def command_remote_held(arguments):
    """The sessions a switch would take Remote Control from for good: outside tmux, unless the root service can press
    the key there."""
    held = [name_of(session) for session in with_remote_control()
            if not in_its_pane(session) and not through_root(session, ("bound", "free"))]
    for name in held:
        print(name)
    return 0 if held else 1


def remote_step(entry, now):
    """Moves one remembered session on. Returns back, lost or gone once it's done with, None while it waits."""
    session = live_session(SESSIONS / f"{entry.get('pid')}.json")
    if session is None or session.get("procStart") != entry.get("start"):
        return "gone"
    connected = isinstance(session.get("bridgeSessionId"), str)
    if not entry.get("off"):
        # Until it dropped Remote Control it's on the account it had: a session that keeps it needs nothing.
        if not connected:
            entry["off"] = True
            # Outside tmux only the root service can press the key, in a session on a terminal of its own.
            return None if entry.get("pane") or through_root(session) else "lost"
        return "gone" if now - entry.get("since", now) > REMOTE_DROP * 1000 else None
    if connected:
        return "back"
    tries = entry.get("tries") or 0
    if entry.get("typed") and now - entry["typed"] <= REMOTE_BACK * 1000:
        return None
    if tries >= REMOTE_TRIES or now - entry.get("since", now) > REMOTE_WAIT * 1000:
        return "lost"
    if session.get("status") == "idle" and press(entry, tries):
        entry["typed"] = now_ms()
        entry["tries"] = tries + 1
    return None


def command_remote_restore(arguments):
    """Brings Remote Control back in the remembered sessions, one step each second until none waits."""
    with remote_lock(".remote.restore.lock") as running:
        try:
            fcntl.flock(running, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 1
        while True:
            with remote_lock(".remote.lock") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                waiting = []
                for entry in (load(REMOTE) or {}).get("sessions", []):
                    if not isinstance(entry, dict):
                        continue
                    outcome = remote_step(entry, now_ms())
                    if outcome is None:
                        waiting.append(entry)
                    elif outcome != "gone":
                        print(f"{outcome}\t{entry.get('name')}", flush=True)
                if waiting:
                    write_private(REMOTE, {"sessions": waiting})
                else:
                    REMOTE.unlink(missing_ok=True)
            if not waiting:
                return 0
            time.sleep(1)


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
    best.add_argument("--active")
    best.add_argument("--order", default=",".join(KINDS))
    best.add_argument("files", nargs="*")
    refuse = commands.add_parser("refuse")
    refuse.add_argument("--timeout", type=float, default=10)
    refuse.add_argument("file")
    refuse.add_argument("reason")
    refuse.add_argument("message", nargs="?", default="")
    commands.add_parser("why").add_argument("file")
    commands.add_parser("remote").add_argument("step", choices=("save", "restore", "held"))
    arguments = parser.parse_args(argv)
    if arguments.command == "remote":
        return {"save": command_remote_save, "restore": command_remote_restore,
                "held": command_remote_held}[arguments.step](arguments)
    return {"sessions": command_sessions, "read": command_read, "left": command_left, "best": command_best,
            "refuse": command_refuse, "why": command_why, "first": command_first}[arguments.command](arguments)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
