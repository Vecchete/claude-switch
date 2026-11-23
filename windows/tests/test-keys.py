#!/usr/bin/env python3
"""Smoke tests of the gateway keys of the Windows port: the checks of tests/test-keys.sh, with a stand-in claude, on
scratch profiles. Import, add, renew and remove, switches between keys and claude.ai accounts, how a key is told from
another, settings.json as it can be found, the warnings and the prompt. Then what only Windows does to them: a
settings.json marked read-only, a folder an ACL keeps from writing, a BOM before a piped token, a name a file name can't
hold, and names that differ only by case. They run on Windows, where tests/test-keys.sh can't, and on Linux.
Usage: test-keys.py [SCRIPT], windows/claude-switch.py by default."""
import contextlib
import csv
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import types

WINDOWS = os.name == "nt"
HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else HERE.parent / "claude-switch.py").resolve()
FAR = 4102444800000
failures = []
passes = 0


def check(name, condition):
    global passes
    if condition:
        passes += 1
    else:
        failures.append(name)
        print("FAIL", name)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def executable(path, text):
    """A script the port runs: a .cmd on Windows, a file with a #! line elsewhere."""
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def user_sid():
    """The SID of this user, for icacls."""
    line = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True).stdout
    return next(csv.reader([line.strip()]))[1]


root = pathlib.Path(tempfile.mkdtemp(prefix="claude-switch-keys."))
# Folders kept from writing and files marked read-only, made writable again before the scratch folder goes.
denied = []
read_only = []


def deny_writes(folder):
    """Keeps this user from making files in FOLDER: its mode on Linux, an ACL entry on Windows, where the read-only
    attribute of a folder means nothing."""
    if WINDOWS:
        subprocess.run(["icacls", str(folder), "/deny", f"*{user_sid()}:(WD,AD)"], capture_output=True, check=True)
    else:
        folder.chmod(0o555)
    denied.append(folder)


def allow_writes(folder):
    if WINDOWS:
        subprocess.run(["icacls", str(folder), "/remove:d", f"*{user_sid()}"], capture_output=True)
    else:
        folder.chmod(0o755)
    if folder in denied:
        denied.remove(folder)


try:
    bin_, state = root / "bin", root / "state"
    for folder in (bin_, state):
        folder.mkdir()
    # claude is never run for a key: this one logs any call.
    fake = HERE / "fake-claude.py"
    if WINDOWS:
        executable(bin_ / "claude.cmd", f'@"{sys.executable}" "{fake}" %*\n')
    else:
        executable(bin_ / "claude", f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n')
    clean = {key: value for key, value in os.environ.items() if not key.upper().startswith(("CLAUDE", "ANTHROPIC"))}
    clean.update(PATH=f"{bin_}{os.pathsep}{os.environ['PATH']}", FAKE_STATE=str(state))
    calls = state / "calls"

    # The profile in use: fresh() makes another one.
    P = types.SimpleNamespace()

    def use(name):
        P.home = root / name
        P.credentials = P.home / ".claude" / ".credentials.json"
        P.config = P.home / ".claude.json"
        P.settings = P.home / ".claude" / "settings.json"
        P.store = P.home / ".claude-accounts"
        P.env = dict(clean, HOME=str(P.home), USERPROFILE=str(P.home), APPDATA=str(P.home / "roaming"),
                     LOCALAPPDATA=str(P.home / "local"))

    def run(*arguments, token="", cwd=None, **extra):
        """claude-switch ARGUMENTS on the profile in use, TOKEN on its standard input."""
        return subprocess.run([sys.executable, "-I", str(SCRIPT), *arguments], env=dict(P.env, **extra),
                              cwd=cwd or P.home, input=token, capture_output=True, text=True, encoding="utf-8",
                              timeout=180)

    def out(result):
        return result.stdout + result.stderr

    def key(name):
        return P.store / f"key_{name}.json"

    def env_of(name):
        return read(P.settings).get("env", {}).get(name, "none")

    def emptied(*names):
        """The gateway entries NAMES are in settings.json, emptied."""
        found = read(P.settings).get("env", {})
        return all(found.get(name) == "" for name in names)

    def marked(name):
        return any(line.startswith(f"* {name} ") for line in run("list").stdout.splitlines())

    def live_access():
        return read(P.credentials)["claudeAiOauth"]["accessToken"]

    def set_env(change):
        data = read(P.settings)
        change(data)
        write(P.settings, data)

    def login(letter, email, plan):
        return {"accessToken": f"{letter}-access", "refreshToken": f"{letter}-refresh", "expiresAt": FAR,
                "refreshTokenExpiresAt": FAR, "scopes": ["user:inference", "user:profile"], "subscriptionType": plan}

    def no_secret(text):
        return "-secret" not in text

    use("home")
    write(P.credentials, {"claudeAiOauth": login("A", "a@example.com", "max")})
    write(P.config, {"oauthAccount": {"emailAddress": "a@example.com", "accountUuid": "acct-a",
                                      "organizationUuid": "org-of-a-0000"}, "hasCompletedOnboarding": True})
    write(P.settings, {"env": {"ANTHROPIC_AUTH_TOKEN": "G1-secret", "ANTHROPIC_BASE_URL": "https://gw1.example",
                               "ANTHROPIC_DEFAULT_SONNET_MODEL": "vertex/sonnet", "ENABLE_TOOL_SEARCH": "true"},
                       "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]}, "model": "opus"})
    if not WINDOWS:
        P.settings.chmod(0o660)
    credentials_before = P.credentials.read_bytes()

    # A token in settings.json that no account keeps would be lost: a switch refuses.
    before = P.settings.read_bytes()
    result = run("a@example.com")
    check("an unkept key stops a switch", result.returncode == 1)
    check("it says how to keep it", "key import" in out(result))
    check("and how to forget it", "key remove" in out(result))
    check("settings unchanged by the refusal", P.settings.read_bytes() == before)
    result = run("list")
    check("list reminds of the unkept key", "no account keeps" in out(result))
    check("list marks no account for an unkept key", not any(line.startswith("*") for line in result.stdout.splitlines()))

    result = run("key", "import", "gw1")
    check("import keeps it", key("gw1").is_file())
    check("import says so", "Kept the gateway key gw1" in out(result))
    if not WINDOWS:
        check("the key file is private", mode(key("gw1")) == 0o600)
    check("import takes the gateway entries", read(key("gw1"))["env"].get("ANTHROPIC_DEFAULT_SONNET_MODEL") == "vertex/sonnet")
    check("import leaves the other entries", "ENABLE_TOOL_SEARCH" not in read(key("gw1"))["env"])
    check("import prints no token", no_secret(out(result)))
    result = run("key", "import", "again")
    check("the same key twice is refused", "already kept as gw1" in out(result) and not key("again").exists())
    result = run("list")
    rows = result.stdout.splitlines()
    check("the key is the active one", any(line.split()[:4] == ["*", "gw1", "https://gw1.example", "key"] for line in rows))
    check("the login is not marked active", any(line.startswith("  a@example.com") for line in rows))
    check("list prints no token", no_secret(out(result)))

    result = run("key", "add", "gw2", "--like", "gw1", "--base-url", "https://gw2.example", token="G2-secret\n")
    gw2 = read(key("gw2"))["env"] if key("gw2").is_file() else {}
    check("add keeps it", gw2.get("ANTHROPIC_AUTH_TOKEN") == "G2-secret")
    check("add takes the URL given", gw2.get("ANTHROPIC_BASE_URL") == "https://gw2.example")
    check("add --like takes its model names", gw2.get("ANTHROPIC_DEFAULT_SONNET_MODEL") == "vertex/sonnet")
    check("add does not switch", env_of("ANTHROPIC_AUTH_TOKEN") == "G1-secret")
    check("add prints no token", no_secret(out(result)))
    run("key", "add", "gw3", "--env", "ANTHROPIC_AUTH_TOKEN=x", token="x\n")
    check("a token on the command line is refused", not key("gw3").exists())
    run("key", "add", "gw3", "--env", "PATH=/x", token="x\n")
    check("an entry that is not a gateway one is refused", not key("gw3").exists())
    run("key", "add", "gw3", "--env", "ANTHROPIC_MODEL=", token="x\n")
    check("an entry without a value is refused", not key("gw3").exists())
    run("key", "add", "gw3", "--like", "a@example.com", token="x\n")
    check("--like a claude.ai account is refused", not key("gw3").exists())
    run("key", "add", "gw3", token="\n")
    check("an empty key is refused", not key("gw3").exists())
    run("key", "add", "key", token="x\n")
    check("a command as a name is refused", not key("key").exists())
    run("key", "add", "gw2", token="x\n")
    check("a name already used is refused", read(key("gw2"))["env"]["ANTHROPIC_AUTH_TOKEN"] == "G2-secret")

    result = run("gw2", cwd=root)
    check("switch to a key reports it", "Switched to gw2, a gateway key" in out(result))
    check("its token is in settings", env_of("ANTHROPIC_AUTH_TOKEN") == "G2-secret")
    check("its URL is in settings", env_of("ANTHROPIC_BASE_URL") == "https://gw2.example")
    check("the other env entries stay", env_of("ENABLE_TOOL_SEARCH") == "true")
    settings = read(P.settings)
    check("the rest of settings stays", settings.get("model") == "opus"
          and settings["hooks"]["Stop"][0]["hooks"][0]["command"] == "x")
    if not WINDOWS:
        check("settings keeps its mode", mode(P.settings) == 0o660)
    check("the claude.ai login is untouched", P.credentials.read_bytes() == credentials_before)
    check("no temp file left", not pathlib.Path(f"{P.settings}.switch").exists())
    check("switch prints no token", no_secret(out(result)))
    check("the same key again", "Already on gw2" in out(run("gw2")))

    result = run("a@example.com")
    check("switch to the login reports it", "Switched to a@example.com" in out(result))
    # Running sessions keep a variable that is gone from settings.json, and take in one that changes.
    check("the gateway entries are emptied, not taken out",
          emptied("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_DEFAULT_SONNET_MODEL"))
    check("the other env entries stay after", env_of("ENABLE_TOOL_SEARCH") == "true")
    check("the login is active again", marked("a@example.com"))
    before = P.settings.read_bytes()
    check("already on the login", "Already on a@example.com" in out(run("a@example.com")))
    check("settings not rewritten for nothing", P.settings.read_bytes() == before)

    run("gw1")
    check("back to the first key", env_of("ANTHROPIC_AUTH_TOKEN") == "G1-secret"
          and env_of("ANTHROPIC_DEFAULT_SONNET_MODEL") == "vertex/sonnet")
    run("gw2")
    check("a key to a key leaves no entry of the first", env_of("ANTHROPIC_BASE_URL") == "https://gw2.example")

    run("rename", "gw2", "second")
    check("a key renamed", marked("second"))

    calls.unlink(missing_ok=True)
    result = run("refresh")
    check("refresh runs beside keys", result.returncode == 0)
    check("refresh never runs claude for a key", not calls.exists())
    check("refresh says nothing of a key", not any(line.startswith(("gw1:", "second:")) for line in out(result).splitlines()))

    def fresh(name):
        """A new profile: the claude.ai account a live, b kept, and settings.json with the gateway key G1, kept as gw1,
        and G2 at another URL kept as gw2."""
        use(name)
        write(P.credentials, {"claudeAiOauth": login("A", "a@example.com", "max")})
        write(P.config, {"oauthAccount": {"emailAddress": "a@example.com", "accountUuid": "acct-a",
                                          "organizationUuid": "org-of-a-0000"}, "hasCompletedOnboarding": True})
        write(P.store / "b@example.com_org-of-b.json",
              {"name": "", "login": {"claudeAiOauth": login("B", "b@example.com", "pro")},
               "oauthAccount": {"emailAddress": "b@example.com", "accountUuid": "acct-b",
                                "organizationUuid": "org-of-b-0000"}})
        write(P.settings, {"env": {"ANTHROPIC_AUTH_TOKEN": "G1-secret", "ANTHROPIC_BASE_URL": "https://gw1.example",
                                   "ANTHROPIC_DEFAULT_SONNET_MODEL": "vertex/sonnet", "ENABLE_TOOL_SEARCH": "true"},
                           "model": "opus"})
        if not WINDOWS:
            for path in (P.store, P.credentials, P.config, P.settings, *P.store.glob("*.json")):
                path.chmod(0o700 if path.is_dir() else 0o600)
        run("key", "import", "gw1")
        run("key", "add", "gw2", "--like", "gw1", "--base-url", "https://gw2.example", token="G2-secret\n")

    # From a key to a claude.ai account other than the live login: the login changes and the key goes out.
    fresh("to-other")
    run("gw2")
    run("b@example.com")
    check("key to another login: switched", live_access() == "B-access")
    check("key to another login: the key is emptied", emptied("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"))
    check("key to another login: marked", marked("b@example.com"))

    # A key switch never writes over a token no account keeps.
    fresh("unkept")
    set_env(lambda data: data["env"].update(ANTHROPIC_AUTH_TOKEN="G9-secret"))
    before = P.settings.read_bytes()
    result = run("gw2")
    check("unkept: a switch to a key refuses", result.returncode == 1)
    check("unkept: settings unchanged", P.settings.read_bytes() == before)
    check("unkept: says how to keep it", "key import" in out(result))

    # Two keys with the same token at two gateways are two accounts.
    fresh("same-token")
    run("key", "add", "gw3", "--like", "gw1", "--base-url", "https://gw3.example", token="G1-secret\n")
    check("same token: kept", key("gw3").is_file())
    run("gw3")
    check("same token: the one switched to is marked", marked("gw3"))
    result = run("gw1")
    check("same token: a switch back is no 'Already on'", "Switched to gw1" in out(result))
    check("same token: its URL is back", env_of("ANTHROPIC_BASE_URL") == "https://gw1.example")
    result = run("key", "add", "gw4", "--like", "gw1", token="G1-secret\n")
    check("the same key twice under two names is refused", "already kept as gw1" in out(result))
    check("and not kept", not key("gw4").exists())

    # Entries changed by hand in settings.json belong to the active key.
    fresh("edits")
    set_env(lambda data: data["env"].update(ANTHROPIC_DEFAULT_SONNET_MODEL="vertex/sonnet-2"))
    check("hand edit: the key stays active", marked("gw1"))
    check("hand edit: saved to the key", read(key("gw1"))["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "vertex/sonnet-2")
    run("gw2")
    run("gw1")
    check("hand edit: back after a round trip", env_of("ANTHROPIC_DEFAULT_SONNET_MODEL") == "vertex/sonnet-2")
    set_env(lambda data: data["env"].update(ANTHROPIC_BASE_URL="https://gw1-moved.example"))
    check("a moved URL: the switch goes on", "Switched to gw2" in out(run("gw2")))
    run("gw1")
    check("a moved URL: kept by its key", env_of("ANTHROPIC_BASE_URL") == "https://gw1-moved.example")

    # A running session keeps the ANTHROPIC_MODEL it started on.
    fresh("model")
    set_env(lambda data: data["env"].update(ANTHROPIC_MODEL="vertex/main"))
    result = run("a@example.com")
    check("a key's ANTHROPIC_MODEL: a switch says running sessions keep it",
          "started on the model vertex/main keeps it" in out(result))
    check("to a key with ANTHROPIC_MODEL: nothing said", "keeps it" not in out(run("gw1")))
    check("from it to a key without: said", "started on the model vertex/main keeps it" in out(run("gw2")))

    # A gateway key has no sign-in.
    fresh("login")
    calls.unlink(missing_ok=True)
    result = run("login", "gw1")
    check("login with a key: refused", result.returncode == 1)
    check("login with a key: says what renews it", "key renew gw1" in out(result))
    check("login with a key: claude not run", not calls.exists())

    # A new token for a key.
    fresh("renew")
    run("gw2")
    result = run("key", "renew", "gw2", token="G2-new-secret\n")
    check("renew: the key has its new token", read(key("gw2"))["env"]["ANTHROPIC_AUTH_TOKEN"] == "G2-new-secret")
    check("renew: the active key's token is in settings too", env_of("ANTHROPIC_AUTH_TOKEN") == "G2-new-secret")
    check("renew: the rest stays", env_of("ANTHROPIC_BASE_URL") == "https://gw2.example")
    check("renew: the key is still active", marked("gw2"))
    check("renew prints no token", no_secret(out(result)))
    run("key", "renew", "gw1", token="G1-new-secret\n")
    check("renew another key: its file only", read(key("gw1"))["env"]["ANTHROPIC_AUTH_TOKEN"] == "G1-new-secret"
          and env_of("ANTHROPIC_AUTH_TOKEN") == "G2-new-secret")
    result = run("key", "renew", "a@example.com", token="x\n")
    check("renew a claude.ai account: refused", "claude-switch login" in out(result))

    # Forgetting a key.
    fresh("remove")
    run("key", "remove", "gw2")
    check("remove: the key is gone", not key("gw2").exists())
    check("remove another key: settings stays", env_of("ANTHROPIC_AUTH_TOKEN") == "G1-secret")
    run("key", "remove", "gw1")
    check("remove the active key: gone", not key("gw1").exists())
    check("remove the active key: emptied in settings",
          emptied("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_DEFAULT_SONNET_MODEL"))
    check("remove the active key: the login is active", marked("a@example.com"))
    run("key", "remove", "b@example.com")
    check("remove a claude.ai account: refused", (P.store / "b@example.com_org-of-b.json").is_file())
    fresh("removed-by-hand")
    key("gw1").unlink()
    result = run("b@example.com")
    check("the active key's file removed by hand: switches refuse", live_access() == "A-access"
          and "key remove" in out(result))
    run("key", "import", "gone")
    run("key", "remove", "gone")
    run("b@example.com")
    check("the way out it gives works", live_access() == "B-access")

    # settings.json as it can be found.
    fresh("empty")
    P.settings.write_text("", encoding="utf-8")
    run("b@example.com")
    check("empty settings: a login switch leaves it alone", live_access() == "B-access" and P.settings.stat().st_size == 0)
    run("gw2")
    check("empty settings: a key switch fills it", env_of("ANTHROPIC_AUTH_TOKEN") == "G2-secret")
    check("empty settings: then marked", marked("gw2"))
    fresh("missing")
    P.settings.unlink()
    run("gw2")
    check("no settings: a key switch creates it", P.settings.is_file() and env_of("ANTHROPIC_AUTH_TOKEN") == "G2-secret")
    if not WINDOWS:
        check("no settings: private", mode(P.settings) == 0o600)
    fresh("invalid")
    P.settings.write_text('{"env": {', encoding="utf-8")
    result = run("gw2")
    check("invalid settings: a key switch refuses", "can't be read as JSON" in out(result)
          and P.settings.read_text(encoding="utf-8") == '{"env": {')

    def linked(folder):
        """settings.json moved to FOLDER and a symbolic link left in its place. False where links can't be made, on a
        Windows without Developer Mode for instance."""
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "settings.json"
        shutil.move(str(P.settings), str(target))
        try:
            os.symlink(target, P.settings)
        except OSError:
            shutil.move(str(target), str(P.settings))
            return False
        return True

    fresh("link")
    if linked(P.home / "dotfiles"):
        run("gw2")
        check("a symbolic link stays one", P.settings.is_symlink())
        check("the file it points to has the key",
              read(P.home / "dotfiles" / "settings.json")["env"]["ANTHROPIC_AUTH_TOKEN"] == "G2-secret")
        check("no temp file beside it", [path.name for path in (P.home / "dotfiles").iterdir()] == ["settings.json"])
        fresh("read-only")
        linked(P.home / "store")
        deny_writes(P.home / "store")
        result = run("b@example.com")
        check("a link to a folder that can't be written: a login switch refuses first",
              live_access() == "A-access" and "can't write" in out(result))
        run("gw2")
        check("a link to a folder that can't be written: a key switch refuses",
              read(P.home / "store" / "settings.json")["env"]["ANTHROPIC_AUTH_TOKEN"] == "G1-secret")
        allow_writes(P.home / "store")
    else:
        print("skipped: the symbolic link checks, this user can't make links")
    if not WINDOWS:
        groups = [group for group in os.getgroups() if group != os.getgid()]
        if groups:
            fresh("group")
            os.chown(P.settings, -1, groups[0])
            P.settings.chmod(0o660)
            run("gw2")
            check("settings keeps its group", mode(P.settings) == 0o660 and P.settings.stat().st_gid == groups[0])
        else:
            print("skipped: settings keeps its group, this user is in no other group")

    # A gateway that takes its key in headers.
    fresh("headers")
    write(P.settings, {"env": {"ANTHROPIC_BASE_URL": "https://hdr.example",
                               "ANTHROPIC_CUSTOM_HEADERS": "x-portkey-api-key: H-secret", "ENABLE_TOOL_SEARCH": "true"}})
    before = P.settings.read_bytes()
    run("b@example.com")
    check("headers no account keeps: no refusal", live_access() == "B-access")
    check("headers no account keeps: left alone", P.settings.read_bytes() == before)
    run("key", "import", "hdr")
    check("headers: kept", read(key("hdr"))["env"].get("ANTHROPIC_CUSTOM_HEADERS") == "x-portkey-api-key: H-secret")
    check("headers: marked", marked("hdr"))
    run("a@example.com")
    check("headers: emptied on a switch to a login", emptied("ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS"))
    run("hdr")
    check("headers: back", env_of("ANTHROPIC_CUSTOM_HEADERS") == "x-portkey-api-key: H-secret")

    # An Anthropic API key goes in ANTHROPIC_API_KEY, which gateways and the API take as x-api-key.
    fresh("api-key")
    write(P.settings, {"env": {"ANTHROPIC_API_KEY": "K1-secret", "ANTHROPIC_BASE_URL": "https://xkey.example"}})
    run("key", "import", "k1")
    run("key", "add", "k2", "--like", "k1", token="K2-secret\n")
    check("--like an API key: the token goes in ANTHROPIC_API_KEY",
          sorted(read(key("k2"))["env"]) == ["ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"])
    run("key", "add", "k3", "--api-key", "--base-url", "https://api.example", token="K3-secret\n")
    check("--api-key: the token goes in ANTHROPIC_API_KEY", read(key("k3"))["env"].get("ANTHROPIC_API_KEY") == "K3-secret")
    run("gw1")
    run("k1")
    check("an API key after a bearer token: the token is emptied", emptied("ANTHROPIC_AUTH_TOKEN"))
    check("an API key after a bearer token: marked", marked("k1"))

    # A key add waits for its token with the store unlocked. A list started before the add got there waits for it a
    # moment, so only a list that never ends says the store stayed locked: it would wait 5 minutes for it.
    fresh("unlocked")
    waiting = subprocess.Popen([sys.executable, "-I", str(SCRIPT), "key", "add", "slow"], env=P.env, cwd=P.home,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    time.sleep(2)
    try:
        result = subprocess.run([sys.executable, "-I", str(SCRIPT), "list"], env=P.env, cwd=P.home, input="",
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
        listed = result.returncode == 0
    except subprocess.TimeoutExpired:
        listed = False
    check("while a key add waits: list runs", listed)
    waiting.communicate(b"S-secret\n", timeout=180)
    check("then the key add goes on", key("slow").is_file())

    # Each run removes only what it was writing itself, so other runs never break a switch: here a run starts and ends
    # while the switch has its file aside.
    fresh("others")
    others = root / "others.py"
    others.write_text(
        "import importlib.util, signal, subprocess, sys\n"
        "spec = importlib.util.spec_from_file_location('claude_switch', sys.argv[1])\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "real = module.replace\n"
        "def replace(source, target):\n"
        "    subprocess.run([sys.executable, '-I', sys.argv[1], 'help'], capture_output=True)\n"
        "    real(source, target)\n"
        "module.replace = replace\n"
        "for number in module.STOPS:\n"
        "    signal.signal(number, module.stop)\n"
        "try:\n"
        "    sys.exit(module.main(sys.argv[2:]))\n"
        "finally:\n"
        "    module.cleanup()\n", encoding="utf-8")

    def beside_another(*arguments):
        return subprocess.run([sys.executable, "-I", str(others), str(SCRIPT), *arguments], env=P.env, cwd=P.home,
                              input="", capture_output=True, text=True, encoding="utf-8", timeout=180)

    result = beside_another("gw2")
    check("a key switch beside another run: done", result.returncode == 0 and env_of("ANTHROPIC_AUTH_TOKEN") == "G2-secret")
    result = beside_another("b@example.com")
    check("a login switch beside another run: done", result.returncode == 0 and live_access() == "B-access")
    # What a switch killed on the way left aside goes at the next run.
    pathlib.Path(f"{P.settings}.switch").write_text("partial", encoding="utf-8")
    pathlib.Path(f"{P.credentials}.switch").write_text("partial", encoding="utf-8")
    run("list")
    check("what a killed switch left aside is removed", not pathlib.Path(f"{P.settings}.switch").exists()
          and not pathlib.Path(f"{P.credentials}.switch").exists())

    # A variable of the shell is only worth a warning when it goes before the account switched to.
    fresh("shell")
    result = run("gw2", ANTHROPIC_AUTH_TOKEN="shell-token")
    check("a token in the shell, a key switch: no warning, settings goes first", "set in this shell" not in out(result))
    result = run("gw1", CLAUDE_CODE_OAUTH_TOKEN="shell-token")
    check("an OAuth token in the shell, a key switch: no warning, the key goes first", "set in this shell" not in out(result))
    result = run("b@example.com", CLAUDE_CODE_OAUTH_TOKEN="shell-token")
    check("an OAuth token in the shell, a login switch: warned", "CLAUDE_CODE_OAUTH_TOKEN is set in this shell" in out(result))
    result = run("a@example.com", ANTHROPIC_AUTH_TOKEN="shell-token")
    check("a token in the shell that the emptied entry cancels: no warning", "set in this shell" not in out(result))
    result = run("gw1", CLAUDE_CONFIG_DIR=str(root / "elsewhere"))
    check("CLAUDE_CONFIG_DIR: warned for a key too", "CLAUDE_CONFIG_DIR is set in this shell" in out(result))
    fresh("shell-api-key")
    write(P.settings, {"env": {"ANTHROPIC_API_KEY": "K1-secret"}})
    run("key", "import", "k1")
    run("b@example.com")
    result = run("k1", ANTHROPIC_AUTH_TOKEN="shell-token")
    check("a bearer token in the shell, a switch to an API key: warned",
          "ANTHROPIC_AUTH_TOKEN is set in this shell" in out(result))

    # What settings.json sets that claude takes before the account.
    fresh("overrides")
    run("b@example.com")
    set_env(lambda data: data.update(apiKeyHelper="echo helper"))
    check("apiKeyHelper before a login: list says so",
          "sets apiKeyHelper, which claude uses before b@example.com" in out(run("list")))
    check("apiKeyHelper after a gateway token: nothing said", "apiKeyHelper" not in out(run("gw2")))
    set_env(lambda data: data["env"].update(CLAUDE_CODE_USE_BEDROCK="1"))
    check("a cloud provider before a key: list says so",
          "sets CLAUDE_CODE_USE_BEDROCK, which claude uses before gw2" in out(run("list")))

    # The prompt marks what list marks. It needs a terminal, so it runs here with q typed in.
    fresh("prompt")
    run("gw2")
    prompt = root / "prompt.py"
    prompt.write_text(
        "import builtins, importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('claude_switch', sys.argv[1])\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "builtins.input = lambda question='': 'q'\n"
        "try:\n"
        "    module.cmd_prompt()\n"
        "finally:\n"
        "    module.cleanup()\n", encoding="utf-8")

    def prompted():
        return out(subprocess.run([sys.executable, "-I", str(prompt), str(SCRIPT)], env=P.env, cwd=P.home, input="",
                                  capture_output=True, text=True, encoding="utf-8", timeout=180))

    shown = prompted().splitlines()
    check("the prompt marks the active key", any("gw2" in line and "active" in line for line in shown))
    check("the prompt doesn't mark the login", not any("a@example.com" in line and "active" in line for line in shown))
    set_env(lambda data: data["env"].update(ANTHROPIC_AUTH_TOKEN="G9-secret"))
    text = prompted()
    check("the prompt reminds of an unkept key", "no account keeps" in text)
    check("the prompt prints no token", no_secret(text))

    # a asks first whether the account is a subscription or a key. What the prompt runs for it is printed here, and a
    # key is kept for real, by a claude-switch of its own that reads the token from the standard input.
    fresh("prompt-add")
    answering = root / "answering.py"
    answering.write_text(
        "import builtins, importlib.util, json, sys\n"
        "spec = importlib.util.spec_from_file_location('claude_switch', sys.argv[1])\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "answers = iter(json.loads(sys.argv[2]) + ['q'])\n"
        "builtins.input = lambda question='': next(answers)\n"
        "real = module.run\n"
        "def run(command):\n"
        "    print('RUN', *command[3:], flush=True)\n"
        "    return real(command) if command[3] == 'key' else 0\n"
        "module.run = run\n"
        "try:\n"
        "    module.cmd_prompt()\n"
        "finally:\n"
        "    module.cleanup()\n", encoding="utf-8")

    def answered(*answers, token=""):
        """The prompt with ANSWERS typed in, then q, and TOKEN on its standard input."""
        return out(subprocess.run([sys.executable, "-I", str(answering), str(SCRIPT), json.dumps(answers)], env=P.env,
                                  cwd=P.home, input=token, capture_output=True, text=True, encoding="utf-8",
                                  timeout=180))

    text = answered("a", "k", "console", "", "", token="K5-secret\n")
    kept = read(key("console"))["env"] if key("console").is_file() else {}
    check("a, k and no URL: an Anthropic API key", "RUN key add console --api-key\n" in text
          and kept == {"ANTHROPIC_API_KEY": "K5-secret"})
    check("a, k and no URL: says how to switch to it", "Switch to it with: claude-switch console" in text)
    check("a, k and no URL: prints no token", no_secret(text))
    text = answered("a", "K", "team", "https://gw5.example", "n", token="T5-secret\n")
    kept = read(key("team"))["env"] if key("team").is_file() else {}
    check("a, k and a URL: a gateway key", kept == {"ANTHROPIC_BASE_URL": "https://gw5.example",
                                                    "ANTHROPIC_AUTH_TOKEN": "T5-secret"})
    check("a, k and a URL: the key is not switched to", env_of("ANTHROPIC_AUTH_TOKEN") == "G1-secret")
    text = answered("a", "k", "fast", "https://gw7.example", "y", token="F7-secret\n")
    check("a, k and a switch: the key is added with --switch",
          "RUN key add fast --base-url https://gw7.example --switch" in text)
    check("a, k and a switch: the machine is on the new key", env_of("ANTHROPIC_AUTH_TOKEN") == "F7-secret"
          and "Switched to fast, a gateway key" in text and marked("fast"))
    check("a and s: a sign-in, as a was", "RUN login\n" in answered("a", "s", ""))
    check("a, s and a switch: a sign-in that switches", "RUN login --switch\n" in answered("a", "s", "Y"))
    text = answered("a", "")
    check("a and Enter: back to the list, nothing run", "RUN" not in text and text.count("Claude Code accounts") == 2)
    text = answered("a", "x")
    check("a and neither: says what to type", "Type s or k." in text and "RUN" not in text)
    check("a, k and Enter for the name: back to the list", "RUN" not in answered("a", "k", ""))
    text = answered("a", "k", "gw1")
    check("a name already used: refused before the URL is asked",
          "Another account is already called gw1." in text and "RUN" not in text)
    text = answered("a", "k", "bare", "gw6.example")
    check("a URL without https://: refused", "starts with https://" in text and "RUN" not in text
          and not key("bare").exists())
    result = run("key", "import", "other", "--switch")
    check("import --switch: refused, it's the key in use already", result.returncode != 0
          and "Usage: claude-switch key import NAME" in out(result) and not key("other").exists())

    # The key shows as it's typed or pasted: it's asked with input, never with getpass.
    asker = root / "asker.py"
    asker.write_text(
        "import builtins, getpass, importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('claude_switch', sys.argv[1])\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "def hidden(*arguments, **options):\n"
        "    raise SystemExit('asked without echo')\n"
        "getpass.getpass = hidden\n"
        "builtins.input = lambda question='': print('ASK', question, flush=True) or 'K9-secret'\n"
        "module.sys.stdin = type('Terminal', (), {'isatty': lambda self: True})()\n"
        "module.lock_store = module.unlock_store = lambda: None\n"
        "print('TOKEN', module.read_token('gw9') == 'K9-secret')\n", encoding="utf-8")
    text = out(subprocess.run([sys.executable, "-I", str(asker), str(SCRIPT)], env=P.env, cwd=P.home, input="",
                              capture_output=True, text=True, encoding="utf-8", timeout=60))
    check("the key is asked where it shows as it's typed or pasted", "ASK Gateway key for gw9: " in text
          and "TOKEN True" in text)

    # What only Windows does to the keys, checked on Linux as well where it means the same.
    # Windows PowerShell can put a BOM before what it pipes into a program.
    fresh("bom-token")
    run("key", "add", "bom", token=chr(0xFEFF) + "B1-secret\r\n")
    check("a BOM and a Windows line end around a piped token: not kept",
          key("bom").is_file() and read(key("bom"))["env"]["ANTHROPIC_AUTH_TOKEN"] == "B1-secret")
    # A Windows editor may save settings.json with a BOM and Windows line ends.
    fresh("bom-settings")
    P.settings.write_bytes(b"\xef\xbb\xbf" + json.dumps(read(P.settings), indent=2).replace("\n", "\r\n").encode())
    run("gw2")
    check("settings with a BOM: read", env_of("ANTHROPIC_AUTH_TOKEN") == "G2-secret" and env_of("ENABLE_TOOL_SEARCH") == "true")
    check("settings with a BOM: written back without it", P.settings.read_bytes()[:1] == b"{")
    # A colon names a stream of the file on NTFS, and * ? < > | can't be in a file name at all.
    fresh("names")
    run("key", "add", "gw:x*", "--like", "gw1", token="X1-secret\n")
    check("a name a file name can't hold: kept under a file name that can", key("gw_x_").is_file())
    result = run("gw:x*")
    check("a name a file name can't hold: switched to by its name", "Switched to gw:x*, a gateway key" in out(result)
          and env_of("ANTHROPIC_AUTH_TOKEN") == "X1-secret")
    # A key renamed keeps its file, so the old name is still taken by it.
    run("rename", "gw1", "first")
    set_env(lambda data: data["env"].update(ANTHROPIC_AUTH_TOKEN="G7-secret"))
    result = run("key", "import", "gw1")
    check("import under the file name of a renamed key: refused, the key stays",
          "Another account is kept as key_gw1.json" in out(result)
          and read(key("gw1"))["env"]["ANTHROPIC_AUTH_TOKEN"] == "G1-secret")
    if WINDOWS:
        # NTFS takes key_GW1.json and key_gw1.json for the same file.
        fresh("case")
        run("key", "add", "GW1", "--base-url", "https://other.example", token="C1-secret\n")
        check("a name that differs only by case: refused, the first key stays",
              read(key("gw1"))["env"]["ANTHROPIC_AUTH_TOKEN"] == "G1-secret" and marked("gw1"))
        # Windows can't rename a file over one marked read-only.
        fresh("read-only-file")
        P.settings.chmod(stat.S_IREAD)
        read_only.append(P.settings)
        before = P.settings.read_bytes()
        result = run("gw2")
        check("settings marked read-only: a key switch refuses and says why", result.returncode == 1
              and "read-only" in result.stderr and P.settings.read_bytes() == before)
        result = run("b@example.com")
        check("settings marked read-only: a login switch from a key refuses first", result.returncode == 1
              and live_access() == "A-access")
        P.settings.chmod(stat.S_IREAD | stat.S_IWRITE)
        run("b@example.com")
        check("settings writable again: the switch goes on", live_access() == "B-access" and emptied("ANTHROPIC_AUTH_TOKEN"))
        # An ACL that keeps this user from making files in ~/.claude, where settings.json is written aside.
        fresh("acl")
        deny_writes(P.home / ".claude")
        result = run("b@example.com")
        check("a folder an ACL keeps from writing: a login switch refuses first",
              live_access() == "A-access" and "can't write" in out(result))
        allow_writes(P.home / ".claude")
finally:
    for folder in list(denied):
        allow_writes(folder)
    for path in read_only:
        with contextlib.suppress(OSError):
            path.chmod(stat.S_IREAD | stat.S_IWRITE)
    shutil.rmtree(root, ignore_errors=True)

print(f"{passes} passed, {len(failures)} failed")
sys.exit(1 if failures else 0)
