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

### `Claude Desktop is open, so it stays on its account.`

Desktop must be closed for a switch, and you answered no, or there was no terminal to ask. Close
Desktop, then run the same switch again to bring it along.

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
