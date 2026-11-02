# claude-switch

Switches the Claude account of this machine with one command, from any folder. Claude Code, claude.ai
in Chrome and Claude Desktop all move to the account you pick, and nothing else changes: one
`~/.claude`, one `~/.claude.json`, the same rules, MCP servers, sessions, settings and history for
every account.

Linux, tested on Ubuntu 24.04 with Claude Code 2.1.314, Chrome Beta 155 and Claude Desktop
4.4212.0 (the official Linux beta). A Windows 11 port is in [windows/](windows/README.md): it passes
the suites of the Linux version, and has not run on Windows yet.

## Install

```bash
git clone git@github.com:Vecchete/claude-switch.git ~/claude-switch
~/claude-switch/install.sh
```

`install.sh` copies the files where they run from and changes nothing else:

| From | To |
|---|---|
| `bin/claude-switch` | `~/.local/bin/claude-switch` |
| `share/chrome-host.py`, `share/desktop.py` | `~/.local/share/claude-switch/` |
| `share/chrome-extension/` | `~/.local/share/claude-switch/chrome-extension/` |
| `share/completion.bash` | `~/.local/share/bash-completion/completions/claude-switch` |
| `systemd/claude-switch-refresh.*` | `~/.config/systemd/user/`, the timer enabled |
| the native host manifest, written by `install.sh` | `NativeMessagingHosts` of each Chrome installed |

Run it again after any change in the checkout. `./install.sh --check` says what differs and changes
nothing. It needs `jq`, `flock`, `python3` and `claude` in the `PATH`.

For claude.ai in Chrome, load the extension once: `chrome://extensions`, Developer mode, Load
unpacked, `~/.local/share/claude-switch/chrome-extension`. Claude Desktop needs nothing more than
being installed from Anthropic's apt repository.

## Use

```text
claude-switch                          the prompt: switch, add (a) or rename (r)
claude-switch NAME                     switch to NAME, a name or an email
claude-switch list                     the accounts, * marks the active one
claude-switch login [ACCOUNT]          sign in an account and keep it, or renew ACCOUNT
claude-switch rename NAME NEW_NAME     rename an account
claude-switch refresh [--force]        refresh the accounts that are not active (the timer does it)
claude-switch help                     this list, -h and --help too
```

Add each account once with `claude-switch login`. From then on a switch never asks for a sign-in.
Tab completes the commands and the accounts, by name or email, in bash with bash-completion.

A sign-in lasts about 28 days and only a new sign-in moves its end. From 7 days before, `list`, the
prompt and a switch say when it ends, with the command that renews it:

```text
claude-switch: The sign-in of work ends on 09/11/2026, in 7 days. Sign in again before then: claude-switch login work
```

## What a switch moves

- Claude Code: the login keys of `~/.claude/.credentials.json` and `oauthAccount` in `~/.claude.json`.
  Running sessions move to the new account at their next request.
- claude.ai in Chrome: the four session cookies and the Remote Control registration cookie, swapped by
  the companion extension while claude.ai is blocked, then the claude.ai tabs reload.
- Claude Desktop: its claude.ai session, its Remote Control registration and the tokens of its Code
  tab. Desktop has to be closed for that, so an open Desktop is restarted when you say so.

Everything else stays: MCP logins, settings, projects, sessions, Chrome and Desktop preferences. Each
account keeps its own history on claude.ai, and it comes back with the account.

The accounts live in `~/.claude-accounts`, one file per account, readable only by you. They hold
logins, so they never go in this repository.

## Docs

- [How a switch works](docs/switch.md)
- [Keeping the other accounts signed in](docs/refresh.md)
- [claude.ai in Chrome](docs/chrome.md)
- [Claude Desktop](docs/desktop.md)
- [Tests](docs/testing.md)
