# Troubleshooting

The messages of claude-switch start with `claude-switch:`, and those of the Chrome companion with
`claude.ai in Chrome:`. This page goes through the ones that need you to do something, and a few problems
that come without a message.

## A switch refuses

### `~/.claude/settings.json holds a gateway key no account keeps, nothing changed.`

`settings.json` holds a gateway token that no saved account has, so a switch would lose it. Keep it, then
switch:

```bash
claude-switch key import NAME
```

When you don't need that token anymore, keep it first anyway, then forget it: `claude-switch key remove
NAME` also empties it in `settings.json`. See [gateway-keys.md](gateway-keys.md#a-token-no-account-keeps).

### `NAME was signed out, nothing changed.` or `The login of NAME ended on DATE, nothing changed.`

The server refused to refresh that account, or its sign-in ended. Sign in again:

```bash
claude-switch login NAME
```

### `Several accounts match NAME.`

That name or email belongs to more than one saved account, the same email in two organizations for
instance. Give the accounts different names in the prompt (`r`) or with `claude-switch rename`, then
switch by name.

### `A running session holds PATH, nothing changed. Try again.`

A Claude Code session held one of its own locks for more than 20 seconds. Wait a moment and run the
switch again.

### `A running session wrote over the switch. Run it again.`

A session wrote its login back while the switch was writing. Run the same switch again.

### `~/.claude/settings.json is PATH, in a folder this user can't write, nothing changed.`

`settings.json` is a link to a file in a folder you can't write, the read-only store of a dotfiles
manager for instance. A switch from or to a gateway key has to write that file. Make it writable, or
keep your gateway keys out of claude-switch.

### `~/.claude/settings.json can't be read as JSON, nothing changed.`

Fix the JSON of `settings.json`, then switch again. Claude Code can't read the file either.

## Waiting

### `Waiting for the background refresh of an account...`

Another claude-switch command holds the store, usually the background refresh. The switch waits up to 5
minutes, then gives up with `The accounts stayed locked for 5 minutes, nothing changed.` When it gives
up, look at the last runs of the refresh:

```bash
journalctl --user -u claude-switch-refresh.service
```

## Chrome and Desktop

### `claude.ai in Chrome: the companion is not running, it opens NAME when it starts.`

Chrome is closed, or the extension is not loaded. With Chrome closed, that's expected: Chrome follows
when it starts. With Chrome open, load the extension in `chrome://extensions`, see
[chrome.md](chrome.md#setup).

### `claude.ai in Chrome did not follow` and `Switch claude.ai in Chrome by hand this time.`

The Chrome host answered with an error, printed on the first line. Claude Code switched anyway. Check
the state of Chrome:

```bash
~/.local/share/claude-switch/chrome-host.py --status
```

### `claude.ai in Chrome: no session saved for NAME yet, sign in there once.`

That account was never open in this Chrome, so there is no session to put back. Sign in on claude.ai
once, and the next switch saves the session.

### claude.ai asks to sign in after a switch

The saved session of that account expired after 28 days without using the account on claude.ai. Sign in
once, and the next switch saves the new session.

### Remote Control asks to sign in after a switch

The first Remote Control session of an account in a browser or in Desktop asks for a fresh sign-in.
After that the registration moves with the account. See [chrome.md](chrome.md#remote-control).

### Remote Control didn't come back after a switch

The notification `Remote Control is off in ...` names the sessions where `claude-switch remote` couldn't
run `/remote-control`: one outside tmux without the claude-switch-remote service, one of Desktop or of an
IDE, a pane scrolled back in copy mode, one that stayed busy for 15 minutes, or one where the key didn't
take 3 times, a dialog left open for instance. Run `/remote-control` in it. When the sessions are back but
don't show on your phone or another computer, sign that device in to the account the machine moved to:
the sessions are under that account now.

The key is `Ctrl+X Ctrl+Q`, bound in `~/.claude/keybindings.json`. When a binding of yours takes
`Ctrl+X` or `Ctrl+Q`, claude-switch can't bind it: it then types `/remote-control` in tmux at an empty
prompt only. Outside tmux, `journalctl -u 'claude-switch-remote@*'` says what the service pressed and
what it refused, and why: a session that wasn't the foreground of its terminal, or a program of another
user on that terminal, `sudo` for instance.

On a Claude Code older than 2.1.330, the mobile app can show a session as working after Remote Control was
turned on mid-conversation, which is what `/remote-control` after a switch does. Exit such a session and
resume it with `claude --resume` to run the installed version.

### A session sends its last reply again after a switch

That's `/after-switch`, which claude-switch runs in every running session once Remote Control is back, so
the Claude app shows the last reply. Change what it asks in `~/.claude/skills/after-switch/SKILL.md`, or
turn it off: bind `ctrl+x ctrl+r` to `null` in the `Chat` context of `~/.claude/keybindings.json`. See
[switch.md](switch.md#the-last-reply-again).

### The machine stays on an account while a better one has room

`claude-switch auto` then says `It waits to go there while Remote Control is on in ...`. Nothing could
bring Remote Control back in those sessions after the switch, so the better account waits until they end
or the account in use runs out. Install the claude-switch-remote service with `sudo ./install.sh --root`
for the sessions outside tmux, run them in tmux, close them, or switch by hand with `claude-switch NAME`
and run `/remote-control` in them afterwards.

### `Claude Desktop is open, so it stays on its account.`

Desktop must be closed for a switch, and you answered no, or the switch came from a script without a
terminal. Close Desktop, then run the same switch again to bring it along.

### `This runs inside Claude Desktop, so Desktop stays on its account.`

The switch ran inside Desktop: typed in its terminal, or the hook of one of its Code sessions that met a limit.
Closing Desktop would have ended the switch with it. Close Desktop, then run the `claude-switch NAME` the
message gives in another terminal, and Desktop comes along.

## Automatic switching

### `LEFT` shows `-`

Nothing could be read for that account: a gateway that doesn't answer `/key/info`, a key in headers only,
an access token that ran out and waits for the background refresh, or no answer from the usage endpoint.
Each reading says why in `~/.claude-accounts/.usage/<account>.json`, in `error`. A reading is tried again
5 minutes later.

### `LEFT` shows `not readable`

The account is an Anthropic API key. No API gives out the credit such a key has left: the Billing page of
the Claude Console shows it. Automatic switching counts the key as having room until a request answers
that its credit is too low, and then waits a day, or until `claude-switch key renew`.

### The machine didn't move when an account ran out

Check how automatic switching stands, and what it did:

```bash
claude-switch auto
systemctl --user list-timers claude-switch-watch.timer
tail ~/.claude-accounts/.auto.log
```

`claude-switch auto` says when the hook went missing from `~/.claude/settings.json`, and `claude-switch
auto on` puts it back. The watch runs only while a Claude Code session is open. A switch you typed holds its
account until it runs out.

### A session stopped on a limit and didn't carry on

When no account had room, nothing switched and the session waits for its limit to reset: Claude Code says
when. A desktop notification names the account that has room again first. A session is woken 3 times in 10
minutes at most. Otherwise, type `continue` in it, and please open an issue with the output of
`claude --version`.

### `usage.py is not in ~/.local/share/claude-switch, run install.sh first.`

`claude-switch auto on` needs the helper `install.sh` puts there. Run `./install.sh` in the checkout.

## Warnings after a switch

### `VARIABLE is set in this shell, a claude started from here doesn't follow the switch.`

A variable exported in your shell goes before the account you switched to, so a `claude` started from
this shell uses it. Unset it, or start claude from another shell. See
[gateway-keys.md](gateway-keys.md#what-goes-before-the-account).

### `~/.claude/settings.json sets SETTING, which claude uses before NAME.`

`settings.json` sets a cloud provider, `apiKeyHelper`, `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_PROFILE`,
and Claude Code uses it before that account. Take it out of `settings.json` when you want the account to
be used.

### `A running session that started on the model MODEL keeps it: /model in it picks another.`

The key you left set `ANTHROPIC_MODEL`, and the sessions that started with it keep that model. Run
`/model` in them to pick a model of the account you switched to.

### `~/.claude/.credentials.json also holds FIELD, which a switch does not move.`

Your Claude Code keeps a login field that claude-switch doesn't know yet, so that field stays as it is
on a switch. Please open an issue with the output of `claude --version`.

## Other problems

### `claude-switch: command not found`

`~/.local/bin` is not in your `PATH` yet. Log in again, or add it to the `PATH` in `~/.profile`.

### Tab completion doesn't work

Install bash-completion and open a new shell. In a shell that is already open, load the completion by
hand:

```bash
source ~/.local/share/bash-completion/completions/claude-switch
```

### The other accounts are not refreshed

Check that the timer is enabled and when it runs next:

```bash
systemctl --user enable --now claude-switch-refresh.timer
systemctl --user list-timers claude-switch-refresh.timer
```

Without a systemd user session nothing runs in the background, so run `claude-switch refresh` from time to
time instead.

### Something else

Open an issue with the output of `claude-switch list`, `claude --version`, your distribution, and the
versions of Chrome and Claude Desktop when they are involved. `list` prints no token, but remove any email
you don't want public.
