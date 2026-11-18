# Keeping the other accounts signed in

The access token of a Claude Code login lasts about 8 hours. The active account is renewed by the
running sessions, but an account that is not active has no session to renew it. `claude-switch refresh`
does it in the background, so a switch never lands on an expired login.

## The timer

`install.sh` installs and enables a systemd user timer, `claude-switch-refresh.timer`. It runs
`claude-switch refresh` 2 and 15 minutes after boot, then every 4 hours, and catches up after a suspend
(`Persistent=true`). To see it and its last runs:

```bash
systemctl --user list-timers claude-switch-refresh.timer
journalctl --user -u claude-switch-refresh.service
```

`claude-switch refresh` can run by hand too. `--force` refreshes every account that is not active, also
the ones that are not due yet.

## How an account is refreshed

The refresh takes every saved claude.ai account that is not active and has less than 4.5 hours left on
its access token:

1. The login goes into a folder of its own, `~/.claude-accounts/.refresh.XXXXXX`, with an access token
   that expired a minute ago.
2. `claude auth status` runs on that folder through `CLAUDE_CONFIG_DIR`. It refreshes an expired login
   without a model request. When a later version stops doing that, the fallback is
   `claude -p --model haiku` with no tools, no MCP server and no session saved.
3. The rotated login goes back to the account file, but only while that file still holds the tokens the
   folder started from.

claude runs with every `CLAUDE*` and `ANTHROPIC*` variable removed, so nothing of a calling Claude Code
session or of an API key takes the place of the login. Offline, claude retries without end, so each call
gets 60 seconds. The service has `KillMode=mixed`: a stop at shutdown reaches the script only, the claude
it runs finishes, and the rotated login is kept.

The active account is never refreshed here, because its login belongs to the running sessions and the
stored copy is older than theirs. Gateway keys have no login to refresh, and Claude Desktop renews its
own sign-in while it runs, see [desktop.md](desktop.md).

## The end of a sign-in

A refresh renews the access token, not the sign-in. A sign-in lasts about 28 days, and only a new
sign-in moves its end. claude-switch reminds you of it ahead of time:

- From 7 days before, `list`, the prompt and every switch print when it ends, with the command that
  renews it.
- From 3 days before, the refresh also shows a desktop notification, once a day.

```text
claude-switch: The sign-in of work ends on 09/12/2026, in 7 days. Sign in again before then: claude-switch login work
```

The days are days of the calendar, counted from noon to noon, so a change of the clock never shifts them.

## When a refresh is refused

When the server refuses a refresh, claude empties both tokens. The account is then marked signed out:
`list` says so, a switch to it is refused, and a desktop notification says to sign in again with the
`claude-switch login` of that account. A notification counts as shown only once `notify-send`
succeeded, so one lost at boot, before the desktop is up, comes again at the next run.

## Interrupted runs

A refresh killed after claude rotated the login leaves the new login in its folder, and the copy in the
store is already used up. Every command brings that login back first, a switch included, so a switch
never puts live a token that was already used.

A `claude-switch login` that was interrupted can leave its sign-in folder behind. After an hour, a
finished one is imported when it's newer than the stored login of its account, then removed. An
unfinished one is removed after a day.
