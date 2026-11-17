# How a switch works

## One ~/.claude for every account

Claude Code keeps the login in `~/.claude/.credentials.json` and the account in `oauthAccount` of
`~/.claude.json`. A switch changes those two and nothing else, so the rules, MCP servers, sessions and
memory are the same whatever the account. A `CLAUDE_CONFIG_DIR` per account was the first version and
it was wrong: the rules load twice (user memory plus the project walk up to the home folder), the
sessions of one account disappear from the other, and dev-ai only syncs `~/.claude`.

## The store

Each account is one file, `~/.claude-accounts/<email>_<first 8 characters of the organization>.json`,
mode 600 in a folder of mode 700. It holds `name`, `login`, `oauthAccount`, and `dead` once a refresh
was refused.

`login` is what Claude Code's own logout deletes with the login (read in the 2.1.314 binary):
`claudeAiOauth`, `designOauth`, `cloudSessionsOauth`, `organizationUuid`, `trustedDeviceToken`,
`trustedDeviceEnrolledAfterRefusalAtMs` and `enterpriseGateway`. `mcpOAuth` never moves. When a later
Claude Code adds another key to `.credentials.json`, the switch says so, because it doesn't move it.

## A switch

1. Every command takes the store lock (`flock` on `~/.claude-accounts/.lock`) and first repairs what an
   interruption left: a login a killed refresh rotated comes back to its account, and a switch cut
   between its two renames is completed (the live tokens equal to another stored account's).
2. The live login is saved to its account, since refresh tokens rotate. A login Claude Code emptied
   after a refused refresh is never saved over the stored one.
3. The switch takes the locks Claude Code takes itself with `mkdir`: `~/.claude/.oauth_refresh.lock`,
   `~/.claude.lock` and `~/.claude.json.lock`. A holder touches its lock every 5 seconds, so one older
   than 15 seconds is dead.
4. Both files are written aside, then renamed. TERM, INT and HUP wait until the second rename, so a
   stop never leaves the login of one account under the name of the other.
5. Claude Code clears some account caches of `~/.claude.json` at its own logout. The switch clears the
   same ones, and Claude Code fetches them again.
6. claude.ai in Chrome and Claude Desktop follow, see [chrome.md](chrome.md) and
   [desktop.md](desktop.md).

A name or an email that matches several accounts is refused, for a switch, a login and a rename.

## Gateway keys

A gateway key account is `~/.claude-accounts/key_<name>.json`, mode 600, holding `kind: "key"`, `name`
and `env`: the gateway entries listed in the README. The active key is the account whose token is the
one in the `env` of `~/.claude/settings.json`, compared inside jq and never printed. It goes before the
claude.ai login in `list`, the prompt and the choice claude makes.

A switch to a key takes every gateway entry out of that `env`, puts in the key's, writes the file aside
with the mode the old one had and renames it. A switch to a claude.ai account does the claude.ai switch
above, then takes the gateway entries out, only when a key was active, so a machine without keys never
has `settings.json` rewritten. A token in `settings.json` that no account keeps stops every switch
until `claude-switch key import NAME` keeps it.

## Signing in and renewing

`claude-switch login` signs in beside the store, in a folder of its own, so the live account changes
only on a switch. `claude-switch login ACCOUNT` renews an account: claude.ai in Chrome goes to it
first, the sign-in page gets its email (`claude auth login --email`), and Chrome comes back to the
live account however the sign-in ends.

Only a new sign-in moves the date a sign-in ends (about 28 days), the refresh doesn't. From 7 days
ahead, `list`, the prompt and a switch say when it ends, with the command that renews the account,
`claude-switch login NAME`. The days are the days of the calendar here, so a sign-in that ends on
09/11 is in 7 days all through 02/11. The refresh announces it on the desktop from 3 days ahead. To
renew the live account, `/login` in a session works too. Never `/login` to another account and never
`/logout`: `/logout` revokes the refresh token on the server, and the copy in the store dies with it.

## Remote Control

Each session registers with the account the CLI is on. A switch disconnects Remote Control, and
`/remote-control` registers the session with the new account.
