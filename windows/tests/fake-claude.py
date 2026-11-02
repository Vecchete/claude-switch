"""Fake claude for test-switch.py: auth login, auth status and -p, as tests/fake-claude.sh does for Linux.
FAKE_MODE: ok, dead or offline. FAKE_STATE is the folder of its log."""
import json
import os
import pathlib
import sys
import time

state = pathlib.Path(os.environ["FAKE_STATE"])
folder = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or pathlib.Path.home() / ".claude")
credentials = folder / ".credentials.json"
mode = os.environ.get("FAKE_MODE", "ok")
arguments = sys.argv[1:]
with open(state / "calls", "a", encoding="utf-8") as log:
    log.write(json.dumps({"args": arguments, "config": os.environ.get("CLAUDE_CONFIG_DIR", ""), "cwd": os.getcwd(),
                          "apiKey": os.environ.get("ANTHROPIC_API_KEY", ""), "session": "CLAUDECODE" in os.environ,
                          "noUpdate": os.environ.get("DISABLE_AUTOUPDATER", "")}) + "\n")


def refresh():
    """What claude does with an expired access token."""
    data = json.loads(credentials.read_text(encoding="utf-8"))
    login = data.get("claudeAiOauth", {})
    if login.get("expiresAt", 0) >= time.time() * 1000 or mode == "offline":
        return
    if mode == "dead":
        login.update(accessToken="", refreshToken="")
    else:
        count = int((state / "count").read_text()) + 1 if (state / "count").exists() else 1
        (state / "count").write_text(str(count))
        login.update(accessToken=f"acc-{count}", refreshToken=f"ref-{count}", expiresAt=int(time.time() * 1000) + 28800000)
    data["claudeAiOauth"] = login
    credentials.write_text(json.dumps(data), encoding="utf-8")


if arguments[:2] == ["auth", "login"]:
    token = os.environ["FAKE_TOKEN"]
    credentials.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": f"{token}-access", "refreshToken": f"{token}-refresh", "expiresAt": 4102444800000,
        "refreshTokenExpiresAt": 4102444800000, "scopes": ["user:inference"], "subscriptionType": "max"}}),
        encoding="utf-8")
    (folder / ".claude.json").write_text(json.dumps({"oauthAccount": {
        "emailAddress": os.environ["FAKE_EMAIL"], "accountUuid": os.environ.get("FAKE_ACCOUNT", "acct-new"),
        "organizationUuid": os.environ.get("FAKE_ORG", "org-new-0000")}}), encoding="utf-8")
    sys.exit(0)
if arguments[:2] == ["auth", "status"]:
    refresh()
    print('{"loggedIn": true}')
    sys.exit(0)
if "-p" in arguments:
    refresh()
    if mode == "offline":
        print("API Error: Unable to connect to API")
        sys.exit(1)
    print("OK")
    sys.exit(0)
print(f"fake claude: not expected: {arguments}", file=sys.stderr)
sys.exit(3)
