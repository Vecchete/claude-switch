# claude-switch

Switch the Claude account of a Linux machine with one command. Claude Code, claude.ai in Chrome and
Claude Desktop all move to the account you pick, and nothing else changes: the same `~/.claude`, the same
settings, rules, MCP servers, sessions and memory for every account.

```text
$ claude-switch personal
Switched to personal.
```

claude-switch is an independent project. It is not made or endorsed by Anthropic. It works on the files
where Claude Code, claude.ai and Claude Desktop keep their sign-in, so an update of one of them can break
it. [Compatibility](#compatibility) lists the versions it was tested with.

## Contents

- [Why](#why)
- [Features](#features)
- [Requirements](#requirements)
- [Install](#install)
- [Quick start](#quick-start)
- [Usage](#usage)
- [What a switch changes](#what-a-switch-changes)
- [Gateway keys](#gateway-keys)
- [Automatic switching](#automatic-switching)
- [Keeping the other accounts signed in](#keeping-the-other-accounts-signed-in)
- [Your data](#your-data)
- [Limitations](#limitations)
- [Compatibility](#compatibility)
- [Uninstall](#uninstall)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)

## Why

Claude Code keeps one login per configuration folder. With more than one Claude account, a work account
and a personal one for instance, there are two usual ways to change accounts, and both have a cost:

- `/logout` and `/login` every time. Each change asks for a sign-in, and `/logout` revokes the login of
  the account you leave.
- One `CLAUDE_CONFIG_DIR` per account. Each account then has its own settings, rules, MCP servers,
  sessions and memory, and the rules of `~/.claude` load twice.

claude-switch keeps every account signed in and swaps only the login. A switch never asks for a
sign-in, and everything else stays shared.

## Features

- One command switches Claude Code, claude.ai in Chrome and Claude Desktop, from any folder. Running
  Claude Code sessions move to the new account at their next request.
- The accounts that are not active stay signed in. A systemd user timer refreshes their logins in the
  background, and claude-switch reminds you a week before a sign-in ends.
- Keys of LLM gateways such as LiteLLM, and Anthropic API keys, are accounts too.
- `list` and the prompt show what each account has left: of its week for a subscription, of its budget for a
  gateway key. The credit of an Anthropic API key is not readable, and they say so.
- Automatic switching, once you turn it on: the machine stays on the best account with room, and a session
  that meets a limit moves to the next account and carries on by itself.
- An interactive prompt, `list`, `rename`, `delete`, and Tab completion in bash.
- No token is ever printed or put on a command line. The logins stay in `~/.claude-accounts`, readable
  only by you.

## Requirements

- Linux. macOS is not supported, because Claude Code keeps its login in the Keychain there. A Windows
  port is in [windows/](windows/README.md), and it's still experimental.
- bash 4.3 or later with GNU coreutils, `jq` 1.6 or later, `flock` from util-linux, Python 3.8 or later,
  and Claude Code with `claude` in the `PATH`.
- A systemd user session, for the background refresh.
- Optional: `notify-send` for desktop notifications, bash-completion for Tab completion, Google Chrome
  or Chrome Beta for claude.ai, and Claude Desktop, the Linux beta from Anthropic.

On Debian and Ubuntu:

```bash
sudo apt install jq python3 libnotify-bin bash-completion
```

## Install

```bash
git clone https://github.com/Vecchete/claude-switch.git
cd claude-switch
./install.sh
```

`install.sh` copies the files to where they run from and enables the refresh timer, and the watch timer
while [automatic switching](#automatic-switching) is on. It needs no root and changes nothing else:

| File | Installed to |
|---|---|
| `bin/claude-switch` | `~/.local/bin/claude-switch` |
| `share/chrome-host.py`, `share/desktop.py`, `share/usage.py`, `share/chrome-extension/` | `~/.local/share/claude-switch/` |
| `share/completion.bash` | `~/.local/share/bash-completion/completions/claude-switch` |
| `systemd/claude-switch-refresh.service` and `.timer`, `systemd/claude-switch-watch.service` and `.timer` | `~/.config/systemd/user/` |
| The manifest of the Chrome native messaging host | `NativeMessagingHosts/` in `~/.config/google-chrome` and `~/.config/google-chrome-beta`, when they exist |

`~/.local/bin` must be in your `PATH`. Debian and Ubuntu add it at login once the folder exists, so log
in again after the first install if your shell can't find `claude-switch`.

To update, pull and run `install.sh` again. It only copies what changed. `./install.sh --check` lists
what differs from the checkout and changes nothing.

```bash
git pull
./install.sh
```

### claude.ai in Chrome

To have claude.ai in Chrome follow the switch, load the companion extension once:

1. Open `chrome://extensions` and turn on Developer mode.
2. Click Load unpacked and pick `~/.local/share/claude-switch/chrome-extension`. In the file picker,
   Ctrl+L lets you type the path.

`install.sh` writes the host manifest only for the Chrome folders that exist, so run it again after you
install Chrome. [docs/chrome.md](docs/chrome.md) says how the swap works.

### Claude Desktop

Nothing more to do. When Claude Desktop is installed, it follows the switch.

## Quick start

1. Run `claude-switch list`. The first time any claude-switch command runs, it saves the account Claude
   Code is signed in to, and `list` shows it with a `*`.

2. Add your other accounts, one at a time:

   ```bash
   claude-switch login
   ```

   It opens the Claude sign-in page in your browser. Sign in with the other account, then give it a
   short name, or press Enter to keep its email as the name. When the browser offers an account that is
   already saved, change accounts on that page or open the link in a private window. In the prompt, `a`
   asks for the kind of account first: `s` signs in a subscription, and `k` keeps an API key or a gateway
   key. For a key it asks for a name and the URL of the gateway (Enter for the Anthropic API). Then it asks
   whether to switch to the new account once it's added, no by default, and signs it in or asks for the key.

3. Switch:

   ```bash
   claude-switch personal          # by name
   claude-switch you@example.com   # or by email
   claude-switch                   # or pick one in a numbered list
   ```

```text
$ claude-switch list
  ACCOUNT                      EMAIL                                PLAN   UNTIL       LEFT
* work                         you@company.example                  team   12/12/2026  49% until Fri 22:00
  personal                     you@example.com                      ultra  05/12/2026  none until Tue 09:00
  team-gw                      https://llm.company.example          key    -           $21.80 until 01/12
```

`UNTIL` is the day the sign-in of the account ends. `LEFT` is what the account has left: of its week, until
the week resets, or of the budget of a gateway key. See [docs/auto.md](docs/auto.md#what-list-shows).

## Usage

| Command | What it does |
|---|---|
| `claude-switch` | Opens a numbered list of the accounts: type a number to switch, `a` to add an account, a subscription or a key, `r` to rename one, `d` to delete one, `q` to quit. |
| `claude-switch NAME` | Switches to the account `NAME`, a name or an email. |
| `claude-switch list` | Prints the accounts and what each has left. `*` marks the active one. |
| `claude-switch login [ACCOUNT] [--switch] [OPTIONS]` | Signs in an account and saves it, without switching unless with `--switch`. With `ACCOUNT`, renews the sign-in of that account. `OPTIONS` go to `claude auth login`. |
| `claude-switch rename NAME NEW_NAME` | Renames an account. |
| `claude-switch delete NAME` | Deletes an account: a gateway key as `key remove` does, a claude.ai account with its login saved here and its saved sessions of Chrome and Desktop. Not the account Claude Code is on. |
| `claude-switch key import NAME` | Saves the gateway key in `~/.claude/settings.json` as the account `NAME`. |
| `claude-switch key add NAME [--switch] [OPTIONS]` | Saves another gateway key, and switches to it with `--switch`. Its token is asked on the terminal, where it shows as you type or paste it, or read from the standard input. |
| `claude-switch key renew NAME` | Gives a gateway key a new token. |
| `claude-switch key remove NAME` | Forgets a gateway key. |
| `claude-switch refresh [--force]` | Refreshes the logins of the accounts that are not active. The timer runs it for you. |
| `claude-switch auto [on\|off]` | Turns automatic switching on or off, or says how it stands. |
| `claude-switch auto order [KIND...]` | Says which kind of account comes first, `ultra`, `max` or `key`, or prints the order. |
| `claude-switch help` | Prints the help, as `-h` and `--help` do. |

Tab completes the commands and the account names and emails in bash, once bash-completion is installed.

> [!WARNING]
> Don't run `/logout` in Claude Code on an account you want to keep. It revokes the login on the
> server, and the copy claude-switch saved stops working with it. `/login` to renew the active account
> is fine, and so is `claude-switch login NAME`.

## What a switch changes

- **Claude Code**: the login in `~/.claude/.credentials.json` and the account in `~/.claude.json`. New
  sessions start on the new account, and running sessions move to it at their next request.
- **claude.ai in Chrome**: the claude.ai session cookies, swapped by the companion extension. The
  claude.ai tabs reload on the new account. See [docs/chrome.md](docs/chrome.md).
- **Claude Desktop**: its claude.ai session and the tokens of its Code tab. Desktop has to be closed for
  that, so when it's open, claude-switch asks whether to restart it, Yes by default, and an automatic
  switch restarts it without asking. See [docs/desktop.md](docs/desktop.md).
- **A gateway key**: its entries in the `env` of `~/.claude/settings.json`. See
  [Gateway keys](#gateway-keys).

Everything else stays as it is: settings, rules, MCP servers and their logins, projects, sessions,
memory, and the preferences of Chrome and Desktop. Each account keeps its own conversations on claude.ai.
[docs/switch.md](docs/switch.md) has the details.

## Gateway keys

When you reach Claude through an LLM gateway such as LiteLLM, its key, URL and model names sit in the
`env` of `~/.claude/settings.json`, and Claude Code takes that key before the claude.ai login.
claude-switch keeps such a key as one more account:

```bash
claude-switch key import work-gw              # save the key settings.json holds now
claude-switch key add team-gw --like work-gw  # another token for the same gateway and models
claude-switch key add console --api-key       # an Anthropic API key
claude-switch team-gw                         # switch to a key
claude-switch personal                        # back to a claude.ai account
```

A switch to a key puts its entries in `settings.json`, and a switch to a claude.ai account empties them
again. Running sessions follow both ways. claude.ai in Chrome and Claude Desktop stay on their account.
[docs/gateway-keys.md](docs/gateway-keys.md) explains the rest.

## Automatic switching

```bash
claude-switch auto on
```

From then on, the machine stays on the best account that has room left:

- Only the week counts. By default an Ultra subscription comes first, then a key, then Max and the other
  plans, which have a 5-hour limit. `claude-switch auto order max` puts Max first on this machine, for
  instance. Within a kind, the account in use stays until it runs out, so one is used up before the next
  starts. Otherwise the allowance that resets first goes first.
- When a session meets a limit, a hook claude-switch adds to `~/.claude/settings.json` moves the machine to
  the next account and wakes the session, which sends its request again on the new account.
- Every 5 minutes while a Claude Code session is open, a timer checks the accounts and goes back to a better
  one once its limit resets.
- A switch you type stays while its account has room.
- An account you add is ranked with the others at once, and the machine goes to it when it comes first.
  Added with `--switch`, the machine goes to it right away, and the next check moves on when another account
  comes first.
- An automatic switch between two claude.ai accounts restarts Claude Desktop without asking.

`claude-switch auto off` takes the hook and the timer out again. [docs/auto.md](docs/auto.md) has the
details.

## Keeping the other accounts signed in

The access token of an account lasts about 8 hours, and an account that is not active has no session to
renew it. The `claude-switch-refresh` timer runs `claude-switch refresh` 2 and 15 minutes after boot,
then every 4 hours, and renews the logins close to expiring. It never touches the active account, which
belongs to the running sessions.

A sign-in lasts about 28 days, and only a new sign-in moves its end. From 7 days before, `list`, the
prompt and every switch say when it ends, with the command that renews it:

```text
claude-switch: The sign-in of work ends on 09/12/2026, in 7 days. Sign in again before then: claude-switch login work
```

From 3 days before, the refresh also shows a desktop notification. See [docs/refresh.md](docs/refresh.md).

## Your data

claude-switch keeps what it saves in `~/.claude-accounts`, a folder only you can read (mode 700, files
mode 600):

| Path | What it holds |
|---|---|
| `<email>_<organization>.json` | A claude.ai account: its name and its login |
| `key_<name>.json` | A gateway key account |
| `.web/<account>.json` | The claude.ai session cookies saved for Chrome |
| `.desktop/<account>.json` | The Claude Desktop sign-in, still encrypted by Desktop |
| `.usage/<account>.json` | What the account had left when it was last read |

The folder also holds small state files: the lock, the notifications already shown, the socket of the
Chrome host, a switch that waits for Chrome, and what automatic switching keeps, see
[docs/auto.md](docs/auto.md#files). The account files hold logins, so keep the folder out of backups you
share and out of any repository.

`claude` signs you in and renews the logins. claude-switch itself asks a server only for what an account
has left: `https://api.anthropic.com/api/oauth/usage` with the login of that account, and the `/key/info`
of a gateway with that gateway's own key. The Chrome host only asks `https://claude.ai/api/account` which
account a session belongs to, with that session's own cookies. [SECURITY.md](SECURITY.md) has the details
and says how to report a problem.

## Limitations

- Only Google Chrome and Chrome Beta get the native host manifest. Chromium, Brave, Edge and Firefox
  don't follow the switch, and only one Chrome profile at a time does.
- The Claude in Chrome extension keeps a login of its own, which claude-switch can't switch.
- Claude Desktop must be closed for a switch, so an open Desktop is restarted unless you say no, and on
  an automatic switch without asking. The first Remote Control session of an account in Chrome or in
  Desktop asks for one sign-in.
- A switch to another claude.ai account takes Remote Control from the sessions. claude-switch types
  `/remote-control` back in those that run in tmux, once each waits idle at an empty prompt, and names the
  others. While one outside tmux has Remote Control on, automatic switching doesn't go to a better
  claude.ai account until the one in use runs out. A phone or another browser shows the sessions once
  it's signed in to the new account.
- What a subscription has left comes from an endpoint Anthropic doesn't document. When it changes, `list`
  shows `-` and automatic switching moves on only when a session meets a limit.
- Automatic switching wakes a session through a `StopFailure` hook with `asyncRewake`, checked with Claude
  Code 2.1.329. Its documentation says it ignores the exit status of that hook, so a later version could
  stop waking sessions.
- An update of Claude Code, claude.ai or Claude Desktop can change where the sign-in is kept. When Claude
  Code adds a login field claude-switch doesn't know, every switch says it doesn't move it.

## Compatibility

| Part | Tested with |
|---|---|
| Linux | Ubuntu 24.04 and 22.04 |
| Claude Code | 2.1.314, 2.1.326 and 2.1.329 |
| jq | 1.6 and 1.7 |
| Chrome | Chrome Beta 155 |
| Claude Desktop | 4.4212.0, the Linux beta from Anthropic's apt repository |
| Windows port | Windows 11 with Claude Code 2.1.302, Chrome 156 and Claude Desktop 4.23118.0 |

A newer version may work as well. When it doesn't, please open an issue with the versions you run.

## Uninstall

```bash
claude-switch auto off
systemctl --user disable --now claude-switch-refresh.timer
rm -f ~/.config/systemd/user/claude-switch-refresh.service ~/.config/systemd/user/claude-switch-refresh.timer
rm -f ~/.config/systemd/user/claude-switch-watch.service ~/.config/systemd/user/claude-switch-watch.timer
systemctl --user daemon-reload
rm -f ~/.local/bin/claude-switch ~/.local/share/bash-completion/completions/claude-switch
rm -f ~/.config/google-chrome*/NativeMessagingHosts/com.lucasvecchete.claude_switch.json
rm -rf ~/.local/share/claude-switch
```

Then remove Claude account switch in `chrome://extensions`. Claude Code stays signed in to the active
account. `~/.claude-accounts` still holds the logins of every account: delete it once you don't need
them anymore.

## Documentation

- [How a switch works](docs/switch.md)
- [Gateway keys](docs/gateway-keys.md)
- [Automatic switching](docs/auto.md)
- [Keeping the other accounts signed in](docs/refresh.md)
- [claude.ai in Chrome](docs/chrome.md)
- [Claude Desktop](docs/desktop.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Tests](docs/testing.md)
- [The Windows port](windows/README.md)

## Contributing

Bug reports and pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) says how to run the tests,
which never touch your own accounts, and what a pull request needs. To report a security problem,
follow [SECURITY.md](SECURITY.md) instead of opening an issue.

## License

claude-switch is released under the [MIT License](LICENSE).
