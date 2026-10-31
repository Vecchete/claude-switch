# Keeping the other accounts signed in

An access token lasts about 8 hours. An account that is not live gets no session to refresh it, so
`claude-switch refresh` does it in the background. The `claude-switch-refresh` timer runs it 2 and 15
minutes after boot and every 4 hours, and it catches up after a suspend (`Persistent=true`).

## How

For each stored account that is not live and has less than 4.5 hours left (all of them with
`--force`):

1. The login goes into a folder of its own, `~/.claude-accounts/.refresh.XXXXXX`, with `expiresAt` in
   the past.
2. `claude auth status` runs with `CLAUDE_CONFIG_DIR` on that folder. It refreshes an expired login
   without a model request. When a later version stops doing that, the fallback is
   `claude -p --model haiku` with no tools, no MCP server and no session saved.
3. The rotated login goes back to the account file, but only while the file still holds the tokens the
   folder started from.

Claude runs with every `CLAUDE*` and `ANTHROPIC*` variable removed, so nothing of a calling Claude Code
session or an API key takes the place of the login. Offline, claude retries without end, so each call
gets 60 seconds. The service has `KillMode=mixed`: a stop at shutdown reaches the script only, claude
finishes, and the rotated login is kept.

## When it can't

When the server refuses a refresh, claude empties both tokens. The account gets `dead`, a switch to it
is refused, and a desktop notification says to sign in again. A sign-in that ends within 3 days is
announced once a day. A notification counts only once `notify-send` succeeded, so one lost at boot
comes again at the next run.

A refresh killed after claude rotated the login leaves the new one in its folder. Every command brings
it back first, a switch included, so a switch never puts live a token already used. Sign-in folders
left by an interrupted `login` are imported after an hour when they are newer than the store, the rest
goes after a day.
