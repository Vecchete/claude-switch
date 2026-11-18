# Contributing to claude-switch

Bug reports, fixes and ideas are welcome. This page says how the project is laid out, how to test a
change, and what a pull request needs.

## Reporting a bug

Open an issue with:

- what you ran and what happened, with the messages claude-switch printed
- the output of `claude-switch list`, which prints no token
- the output of `claude --version`, your distribution, and the versions of Chrome and Claude Desktop when
  they are involved

Never paste the content of `~/.claude-accounts`, `~/.claude/.credentials.json` or
`~/.claude/settings.json`: they hold logins and keys. A security problem goes through
[SECURITY.md](SECURITY.md), not through an issue.

## How the project is laid out

| Path | What it is |
|---|---|
| `bin/claude-switch` | The command, in bash and jq |
| `share/chrome-host.py` | The native messaging host that swaps the claude.ai cookies in Chrome |
| `share/chrome-extension/` | The Chrome extension the host works through |
| `share/desktop.py` | Puts Claude Desktop on the account of a switch |
| `share/completion.bash` | The bash completion |
| `systemd/` | The timer and the service of the background refresh |
| `install.sh` | Copies everything to where it runs from |
| `tests/` | The suites and the mutants, see [docs/testing.md](docs/testing.md) |
| `docs/` | How each part works |
| `windows/` | The Windows port, see [windows/README.md](windows/README.md) |

## Making a change

1. Fork the repository and create a branch.
2. Change the code, and the tests with it.
3. Run the suites. A container keeps them off your machine, see
   [docs/testing.md](docs/testing.md#running-them-in-a-container).

   ```bash
   tests/run.sh
   ```

4. Run the mutants of the files you changed. A new guard comes with a mutant in `tests/mutants.py`, and
   with the test that kills it.

   ```bash
   tests/mutants.py switch
   ```

5. Update the docs when the behavior changes: `README.md`, the page of `docs/` and the help that
   `claude-switch help` prints.

Don't try a change on your real accounts before the suites pass. claude-switch moves logins, and a bug
can leave an account signed out.

## How the code is written

- bash with `set -euo pipefail`, and jq for every JSON file. Python only for the helpers, with the
  standard library alone.
- A file that Claude Code, Chrome or Desktop reads is written aside and renamed over the old one, never
  written in place, because they read it at any moment.
- A token or a cookie never goes on a command line or in a message. Tokens are compared inside jq.
- A command that stops leaves the previous state whole, and says `nothing changed`.
- Comments say why, not what the next line does.
- A message says what happened and what to do next, with the command to run.

## Commit messages

One change per commit. The subject names the part it changes, then says what that part does now:

```text
switch: gateway keys are accounts too
desktop: Claude Desktop follows the switch
docs: a Desktop update can change how it encrypts
```

The body says why, when the subject doesn't.

## The Windows port

`windows/` follows `bin/claude-switch`, `share/chrome-host.py` and `share/desktop.py`. A change to one of
those should reach the port too, or the pull request should say that it doesn't. `windows/tests/run.sh`
runs the Linux suites against the port, on Linux.

## Pull requests

- One change per pull request, with what it fixes or adds and why.
- `tests/run.sh` passes, and the mutants of the files you changed are all killed.
- The docs say what changed.

By contributing, you agree that your contribution is released under the [MIT License](LICENSE).
