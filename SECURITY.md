# Security policy

claude-switch moves logins. It keeps the Claude Code login of every account, gateway and API keys, the
claude.ai session cookies of Chrome and the sign-in of Claude Desktop. A flaw in it can expose an
account, so please report one privately.

## Reporting a vulnerability

Use the private vulnerability reporting of GitHub: the Report a vulnerability button on the security
advisories page of the repository, or straight
<https://github.com/Vecchete/claude-switch/security/advisories/new>. Please don't open a public issue for
it.

Say what an attacker can do, how to reproduce it, and what you tested it on: the commit of claude-switch
and the output of `claude --version`. Never send a real token, cookie or key. A made-up one shows the
problem as well.

claude-switch is maintained in spare time, so answers come on a best effort basis. A confirmed problem is
fixed on `main`, and published as an advisory once the fix is out.

## Supported versions

Only the latest commit of `main` gets fixes. There are no releases yet.

## How claude-switch handles secrets

- Every login it saves is in `~/.claude-accounts`, a folder of mode 700 with files of mode 600. The
  Claude Code files it writes keep their own modes.
- It never prints a token, and never puts one on a command line, where the shell history and the process
  list would keep it. A gateway token is asked on the terminal, where it shows as you type or paste it, or
  read from the standard input, and tokens are compared inside jq.
- `claude` signs in and refreshes the logins. claude-switch itself asks for what an account has left
  only: the access token of a claude.ai account goes to `https://api.anthropic.com/api/oauth/usage`, never
  to `ANTHROPIC_BASE_URL`, and a gateway key to the `/key/info` of its own gateway. The Chrome host sends
  the claude.ai cookies of the browser to `https://claude.ai/api/account` only, to learn which account a
  session belongs to.
- With automatic switching on, `~/.claude/settings.json` holds a hook that runs `claude-switch hook` when
  a Claude Code turn ends on an API error. What the hook prints goes to Claude, so it prints one sentence
  that names accounts and never a token, and says the rest in `~/.claude-accounts/.auto.log`.
- After a switch to another claude.ai account, it brings Remote Control back in each session that had it,
  while the session waits idle, 3 times at most. It presses `Ctrl+X Ctrl+Q`, which it binds to
  `/remote-control` in `~/.claude/keybindings.json`, or types `/remote-control` at an empty prompt when
  that key can't be bound, and nothing else. In tmux it does so only at the prompt of the session's own
  pane, out of copy mode.
- Outside tmux, the optional claude-switch-remote service presses that key. It runs as root, since only
  root can put a key in a terminal that isn't one's own, and anyone on the machine can ask it. It presses
  `Ctrl+X` then `Ctrl+Q` and nothing else, only when all of these hold:
  - the session runs as the user who asks, read from the socket, is called `claude`, and started when
    the request says, so a pid used again is never taken for it,
  - its controlling terminal is a pseudo-terminal of that user, where it is the foreground, in raw mode,
  - every process on that terminal runs as that user, so no program of another user, a root shell the
    session runs under for one, ever reads the keys,
  - it didn't press in that session in the last 20 seconds.

  systemd runs it with no capability but `CAP_SYS_ADMIN` and `CAP_DAC_OVERRIDE`, no network, no home
  folders, a read-only system and pseudo-terminals as its only devices. Every press and every refusal goes
  to the journal.
- The Chrome extension sees claude.ai only, and talks to the host through the native messaging of Chrome.
  claude-switch reaches the host through a Unix socket in `~/.claude-accounts`.
- The Claude Desktop sign-in is saved as Desktop encrypted it, and never decrypted.

## What it doesn't protect against

- Anyone who can read your files as you can read the saved logins, as they can read the
  `~/.claude/.credentials.json` of Claude Code itself.
- Deleting `~/.claude-accounts` doesn't revoke the logins it held. To end the login of an account on the
  server, switch to it and run `/logout` in Claude Code.
