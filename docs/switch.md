# How a switch works

This page explains what `claude-switch NAME` does to Claude Code. claude.ai in Chrome and Claude Desktop
have pages of their own, [chrome.md](chrome.md) and [desktop.md](desktop.md), and so do gateway keys,
[gateway-keys.md](gateway-keys.md).

## One ~/.claude for every account

Claude Code keeps the login in `~/.claude/.credentials.json` and the account in `oauthAccount` of
`~/.claude.json`. A switch changes those two and nothing else, so the settings, rules, MCP servers,
sessions and memory are the same on every account.

A `CLAUDE_CONFIG_DIR` per account was the first version of this tool, and it was the wrong one. The
sessions of one account disappear from the other, every account needs its own settings and MCP servers,
and the rules load twice: once as user memory, and once on the walk up from the project to the home
folder.

## The store

Each claude.ai account is one file, `~/.claude-accounts/<email>_<organization>.json`, where
`<organization>` is the first 8 characters of the organization id. The files are mode 600 in a folder of
mode 700. Each one holds:

- `name`, the name you gave the account, or nothing to show its email instead
- `login`, the part of `.credentials.json` that belongs to the account
- `oauthAccount`, the account of `~/.claude.json`
- `dead`, the day a refresh was refused, once that happened

`login` holds what Claude Code's own logout deletes with the login, read in Claude Code 2.1.314:
`claudeAiOauth`, `designOauth`, `cloudSessionsOauth`, `organizationUuid`, `trustedDeviceToken`,
`trustedDeviceEnrolledAfterRefusalAtMs` and `enterpriseGateway`. The logins of MCP servers, `mcpOAuth`,
never move. When a later Claude Code adds another field to `.credentials.json`, every switch says so,
because it doesn't move it.

## A switch, step by step

1. Every command takes the store lock, a `flock` on `~/.claude-accounts/.lock`, so one switch, sign-in
   or refresh runs at a time.
2. It repairs what an interruption left. A login that a killed refresh already rotated goes back to its
   account, and a switch cut between its two renames is completed.
3. It saves the live login to its account. Refresh tokens rotate, so the stored copy would be outdated
   otherwise. A login that Claude Code emptied after a refused refresh is never saved over the stored one.
4. It takes the locks Claude Code takes itself with `mkdir`: `~/.claude/.oauth_refresh.lock`,
   `~/.claude.lock` and `~/.claude.json.lock`. Without them, a session in the middle of a write would
   bring the previous account back. A holder touches its lock every 5 seconds, so a lock older than 15
   seconds is taken as dead.
5. It writes both files aside, then renames them over the old ones, so a running session never reads
   half a file. TERM, INT and HUP wait until the second rename, so a stop never leaves the login of one
   account under the name of the other.
6. It clears the caches Claude Code clears at its own logout, the model and usage caches of
   `~/.claude.json` for instance. Claude Code fetches them again.
7. claude.ai in Chrome and Claude Desktop follow.

A name or an email that matches several accounts is refused, for a switch, a sign-in and a rename. Name
the accounts in the prompt (`r`) and use the names.

## Running sessions

Running Claude Code sessions read the login again at their next request, so they move to the new
account without a restart. Remote Control is the exception. Each session registers with the account the
CLI is on, so Claude Code drops it within seconds of a switch to another claude.ai account, and only
`/remote-control` registers the session with the new account, carrying its conversation over.

claude-switch types it back for you. Before the switch it notes the sessions that have Remote Control on.
After it, `claude-switch remote` runs in the background and types `/remote-control` in the tmux pane of
each of them, once Claude Code dropped it there and the session waits idle with nothing typed at its
prompt. It never types into a busy session, a pane scrolled back in copy mode, a prompt you started
typing at, or a pane that runs another program, and it types once per session. A notification says when
Remote Control is back, and under which account. It names the sessions it couldn't bring back within 15
minutes, and those that don't run in tmux, so you can run `/remote-control` in them yourself.

Nothing can type in a session that runs outside tmux: in another terminal, in VS Code or in Desktop. So
while one of those has Remote Control on, [automatic switching](auto.md) doesn't go to a better claude.ai
account as long as the account in use has room. It stays, says so in a notification once a day and in
`claude-switch auto`, and moves on once those sessions end or the account in use runs out. A switch you
type, one a limit forces, and one to a key still go.

A phone or another computer shows the sessions once it's signed in to the account the machine moved to:
claude.ai in Chrome here follows the switch, see [chrome.md](chrome.md), but the Claude app and other
browsers keep the account they're on.

## Adding and renewing accounts

`claude-switch login` signs in beside the store, in a folder of its own, and never in `~/.claude`, so the
live account changes only on a switch. It runs `claude auth login` there, saves the new account and asks
for a name. The machine stays on its account, and claude.ai in Chrome goes back to it, see
[chrome.md](chrome.md). `claude-switch login --switch` switches to the new account once it's saved, and
`claude auth login` never sees `--switch`.

`claude-switch login NAME` renews the sign-in of an account that is already saved. The sign-in page uses
the claude.ai session of the browser, so claude.ai in Chrome goes to that account first, and the page
gets its email through `claude auth login --email`. Chrome comes back to the live account however the
sign-in ends. When the browser signs in another account anyway, that one is saved, and the account you
asked for stays as it was.

Only a new sign-in moves the day a sign-in ends, about 28 days later. The background refresh renews the
access token, not the sign-in, see [refresh.md](refresh.md). To renew the live account, `/login` in a
Claude Code session works too. Never `/login` to another account: the live login would be replaced
before its last rotation is saved. And never `/logout`, which revokes the refresh token on the server,
so the copy in the store stops working with it.

## Names

A name can't be empty, start with `-`, hold control characters, be a command of claude-switch (`list`,
`login`, `key`, `rename`, `delete`, `refresh`, `auto`, `watch`, `hook`, `help`) or be only digits, since
the prompt selects accounts by number. Two accounts can't share a name.
