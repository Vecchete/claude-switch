# Tests

The tests run every part of claude-switch against the files of the checkout, never against the installed
ones, and each suite on a scratch `HOME`. They never read or change your own accounts.

## What they need

- bash, `jq`, `flock` and `script` from util-linux, Python 3, and `node` for the suite of the extension.
- The native Claude Code binary at `~/.local/bin/claude`. `test-switch.sh` and `test-keys.sh` run its
  `claude auth status` on the scratch `HOME`, to check what Claude Code itself makes of the files a
  switch writes. It needs no network.

## Running them

```bash
tests/run.sh                 # every suite
tests/run.sh switch keys     # some of them
```

| Suite | Name for `run.sh` | What it covers |
|---|---|---|
| `test-switch.sh` | `switch` | The switch, the prompt, login and rename, with stand-in logins and the real `claude auth status` |
| `test-refresh.sh` | `refresh` | The background refresh, with a stand-in claude |
| `test-keys.sh` | `keys` | Gateway keys: import, add, renew, remove, the prompt's question for a new account, switches between keys and claude.ai accounts, how a key is told from another, `settings.json` as it can be found (empty, missing, a link, read-only, shared with a group), the warnings, with the real `claude auth status` |
| `test-usage.py` | `usage` | `usage.py`: readings from a stand-in usage endpoint and a stand-in gateway, Claude Code's own reading, the order of the accounts, refusals, open sessions |
| `test-auto.sh` | `auto` | Automatic switching: `LEFT` in `list` and the prompt, `auto on`, `off` and `order`, the watch, the hook Claude Code runs, Desktop restarted without a question |
| `test-chrome-host.py` | `chrome-host` | The Chrome host, with a stand-in extension, a stand-in claude.ai and a cookie jar |
| `test-switch-chrome.py` | `switch-chrome` | claude-switch driving the Chrome host, end to end |
| `test-worker.mjs` | `worker` | The worker of the extension, with a stand-in `chrome` API |
| `test-desktop.py` | `desktop` | `desktop.py`, with a Desktop folder, a cookie database and stand-in Desktop processes |
| `test-switch-desktop.sh` | `switch-desktop` | claude-switch driving `desktop.py` |
| `test-completion.sh` | `completion` | The bash completion, called directly and through readline in a real interactive bash |
| `test-install.sh` | `install` | `install.sh`, with a stand-in `systemctl`, the service of root under a scratch root |
| `test-remote-keys.py` | `remote-keys` | `remote-keys.py`, the service of root, with stand-in sessions on pseudo-terminals of their own: who may ask, which session and terminal it presses in, and the keys themselves |

## Running them in a container

The suites never touch your accounts, but they run your claude binary and a lot of shell. To keep all of
it off your machine, run them in a throwaway container. The second group of the user lets
`test-keys.sh` check that `settings.json` keeps its group:

```bash
docker build -t claude-switch-tests - <<'EOF'
FROM ubuntu:24.04
RUN apt-get update \
 && apt-get install -y --no-install-recommends bash jq python3 nodejs util-linux procps bash-completion \
 && rm -rf /var/lib/apt/lists/*
RUN groupadd shared && useradd -m -G shared tester
USER tester
EOF

docker run --rm --network none \
    -v "$PWD:/work:ro" \
    -v "$(readlink -f ~/.local/bin/claude):/usr/local/bin/claude:ro" \
    claude-switch-tests \
    bash -c 'mkdir -p ~/.local/bin && ln -s /usr/local/bin/claude ~/.local/bin/claude && /work/tests/run.sh'
```

Ubuntu 22.04 works the same way, except for the suite of the extension, whose `node` is too old there.

As another user than root, `test-remote-keys.py` runs every check of the service up to the press, which
the kernel refuses. As root with `CAP_SYS_ADMIN` the keys reach the stand-in sessions, and the checks that
need a second user run too:

```bash
docker run --rm --network none --user root --cap-add SYS_ADMIN -v "$PWD:/work:ro" claude-switch-tests \
    python3 -I /work/tests/test-remote-keys.py /work/share/remote-keys.py
```

## Mutants

`tests/mutants.py` breaks one guard at a time, in a copy, and runs the suite that must catch it. Every
mutant undoes something a review or a real incident asked for, so a mutant that stays green is a guard no
test holds. Every one of them must end `killed`.

```bash
tests/mutants.py               # all of them, one at a time
tests/mutants.py desktop       # the mutants of one file: switch, host, install, desktop, worker, completion, usage
                               # or remote-keys
tests/mutants.py key-twin-kept # one mutant, by name
```

Run them one at a time. The suite of the Chrome host depends on timing, and a run under load that fails
for that reason counts a mutant as killed when it is not. `-j` runs several at once, which is fine for
the mutants of `switch`. The mutants of `remote-keys` are all killed only as root with `CAP_SYS_ADMIN`, in
the container above with `bash -c 'cd /work && python3 -I tests/mutants.py remote-keys'`.

A new guard comes with its mutant, and with the test that kills it.

## Windows

`windows/tests/run.sh` runs the suites above against the Windows port, on Linux. On Windows,
`windows\tests\run.ps1` runs the suites of what only Windows does: Desktop's folders and processes, the
settings that go over to the next account, Desktop's Local Storage read and written as LevelDB does, the
named pipe of the Chrome host, and the PowerShell files. It also runs `windows/tests/test-keys.py` and
`windows/tests/test-auto.py`, the checks of `test-keys.sh` and `test-auto.sh` with a stand-in claude and a
stand-in usage endpoint, since those suites can't run on Windows, and what only Windows does to a gateway
key and to automatic switching. They use a scratch profile, stand-in processes and a pipe of their own,
and never touch the real Desktop. `windows\tests\mutants.py` breaks the Windows guards and those of the
gateway keys and of automatic switching one at a time. [windows/README.md](../windows/README.md#tests) has
the details.

```powershell
powershell -ExecutionPolicy Bypass -File windows\tests\run.ps1
py -3 -I windows\tests\mutants.py
```

## Checked on the real thing

A suite can't show what Claude Code, claude.ai and Desktop do on their side, so these were checked on the
real services and apps:

- Claude Code 2.1.314: `claude auth status` refreshes an expired login without a model request, a refused
  refresh empties both tokens, and `/logout` revokes the refresh token.
- Claude Code 2.1.326, against a stand-in gateway: a running session follows a switch between gateway keys
  and from a key to a claude.ai account once the entries are `""`, and a new session then uses the login.
- Claude Code 2.1.329, in a container without network against a stand-in API: a `StopFailure` hook with
  `asyncRewake` that exits 2 wakes the session a usage limit or an empty credit stopped, and when the hook
  replaced `~/.claude/.credentials.json` first, the woken request carries the new login. With
  `claude-switch auto on`, run before the session started or while it was open, a session that met the weekly
  limit of its account carried on by itself on the next account.
- Claude Code 2.1.329 keeps its last reading of the usage of the account it's on in
  `cachedUsageUtilization` of `~/.claude.json`, and lists its running sessions in `~/.claude/sessions`.
- Claude Code 2.1.327 and 2.1.330: `ctrl+x ctrl+q` bound to `command:remote-control` in the `Chat` context
  of `~/.claude/keybindings.json` turns Remote Control on and leaves the text at the prompt as it was, in
  a session that started before the file existed too. Put in its terminal with `TIOCSTI` by root, on Linux
  7.0 with `dev.tty.legacy_tiocsti` at 0, the two keys do the same. The claude-switch-remote service,
  installed under systemd 255 on Ubuntu 24.04 with its sandbox, brought Remote Control back that way in a
  session that ran outside tmux for Claude Code, and refused a shell and PID 1.
- Claude Code 2.1.327 and 2.1.330: `ctrl+x ctrl+r` bound to `command:<name>` runs a skill of
  `~/.claude/skills` with `disable-model-invocation: true`, written after the session started, as a prompt
  typed at the prompt, and leaves the text at the prompt as it was. The record of the session in
  `~/.claude/sessions` shows the turn in `status` and `statusUpdatedAt` within a second.
- claude.ai: a made-up session gets a 403 JSON answer with `account_session_invalid` from `/api/account`,
  saved sessions swap without a sign-in, and a page that sends half a swapped session gets it revoked.
- claude.ai: the first Remote Control session of an account in Chrome or Desktop asks for a fresh sign-in,
  then sets `__Host-ant_trusted_device` and a device key in its IndexedDB, kept per account.
- Claude Desktop 4.4212.0: TERM closes it cleanly in under a second, and it removes its `SingletonLock`.
