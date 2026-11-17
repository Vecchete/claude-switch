# claude-switch

Switches the Claude account of this machine with one command, from any folder. Claude Code, claude.ai
in Chrome and Claude Desktop all move to the account you pick, and nothing else changes: one
`~/.claude`, one `~/.claude.json`, the same rules, MCP servers, sessions, settings and history for
every account.

Linux, tested on Ubuntu 24.04 with Claude Code 2.1.314, Chrome Beta 155 and Claude Desktop
4.4212.0 (the official Linux beta). The Windows 11 port is in [windows/](windows/README.md): it runs on
the Windows PC since 09/11/2026, with Claude Code 2.1.302, Chrome 156 and Claude Desktop 4.23118.0.

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
claude-switch key import NAME          keep the gateway key now in ~/.claude/settings.json as NAME
claude-switch key add NAME [--like ACCOUNT] [--base-url URL] [--env VAR=VALUE]...
                                       keep another gateway key, its token asked without echo
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

## Gateway keys

A key of an LLM gateway such as LiteLLM is an account too, beside the claude.ai ones. It is the set of
gateway entries in the `env` of `~/.claude/settings.json`: `ANTHROPIC_AUTH_TOKEN` or
`ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL`, `ANTHROPIC_SMALL_FAST_MODEL`, the
`ANTHROPIC_DEFAULT_*_MODEL` names and `ANTHROPIC_CUSTOM_HEADERS`. Claude Code takes such a token before
the claude.ai login, so:

- a switch to a key replaces those entries with the key's, and the claude.ai login stays where it is;
- a switch to a claude.ai account takes them out, and the login is used again;
- the other entries of `env` and the rest of `settings.json` stay, with the file's mode.

```bash
claude-switch key import work-gw                                   # the key settings.json holds now
claude-switch key add team-gw --like work-gw                       # another token, same gateway and models
claude-switch key add other-gw --base-url https://gw.example --env ANTHROPIC_DEFAULT_SONNET_MODEL=sonnet
claude-switch team-gw
```

The token is asked without echo, or read from the standard input, never from the command line. A
switch refuses while `settings.json` holds a token no account keeps, so none is lost. claude.ai in
Chrome and Claude Desktop stay on their account, and the refresh skips keys. A token exported in the
shell can keep a claude started from it on that token, so the switch warns about one.

## What a switch moves

- Claude Code: the login keys of `~/.claude/.credentials.json` and `oauthAccount` in `~/.claude.json`.
  Running sessions move to the new account at their next request.
- claude.ai in Chrome: the four session cookies and the Remote Control registration cookie, swapped by
  the companion extension while claude.ai is blocked, then the claude.ai tabs reload.
- Claude Desktop: its claude.ai session, its Remote Control registration and the tokens of its Code
  tab. Desktop has to be closed for that, so an open Desktop is restarted when you say so.

Everything else stays: MCP logins, settings, projects, sessions, Chrome and Desktop preferences. Each
account keeps its own history on claude.ai, and it comes back with the account. On Windows the settings
Claude Desktop keeps per account go over to the next account too, and what an account can't hand over
comes back with it: [windows/](windows/README.md).

The accounts live in `~/.claude-accounts`, one file per account, readable only by you. They hold
logins, so they never go in this repository.

## Docs

- [How a switch works](docs/switch.md)
- [Keeping the other accounts signed in](docs/refresh.md)
- [claude.ai in Chrome](docs/chrome.md)
- [Claude Desktop](docs/desktop.md)
- [Tests](docs/testing.md)
