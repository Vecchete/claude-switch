# Tests

`tests/run.sh` runs every suite against the files of the checkout, never the installed ones, each on a
scratch `HOME`:

| Suite | What it covers |
|---|---|
| `test-switch.sh` | the switch, the prompt, login and rename, with fake logins and the real `claude auth status` |
| `test-refresh.sh` | the background refresh, with a fake claude |
| `test-keys.sh` | gateway key accounts: import, add, switches between keys and claude.ai accounts, `settings.json` kept whole |
| `test-chrome-host.py` | the Chrome host, with a fake extension, a fake claude.ai and a cookie jar |
| `test-switch-chrome.py` | `claude-switch` driving the Chrome host end to end |
| `test-worker.mjs` | the extension's worker, with a fake `chrome` API (needs `node`) |
| `test-desktop.py` | `desktop.py`, with a Desktop folder, a cookie database and fake Desktop processes |
| `test-switch-desktop.sh` | `claude-switch` driving `desktop.py` |
| `test-completion.sh` | the bash completion, called directly and through readline in a real interactive bash |
| `test-install.sh` | `install.sh`, with a fake `systemctl` |

```bash
tests/run.sh                 # every suite
tests/run.sh switch desktop  # some of them
```

## Mutants

`tests/mutants.py` breaks one guard at a time, in a copy, and runs the suite that must catch it. Every
mutant undoes something a review or a live incident asked for, so a mutant that stays green is a guard
no test holds. Every one of them must end `killed`.

```bash
tests/mutants.py             # all, one at a time
tests/mutants.py desktop     # the mutants of one file: switch, host, install, desktop, worker or completion
```

Run them one at a time. The Chrome host suite depends on timing, and a run under load that fails for
that reason counts a mutant as killed when it is not.

## Windows

`windows/tests/run.sh` runs the suites above against the Windows port, on Linux. On the PC,
`windows\tests\run.ps1` runs the suites of what only Windows does: the smoke tests, Desktop's folder, an
open Desktop, the quit, the settings that go over to the next account and what comes back to it, Desktop's
Local Storage read and written as LevelDB does, the named pipe of the Chrome host, and the PowerShell
files.
They use a scratch profile, stand-in processes and a pipe of their own, and never touch the real Desktop.
`windows\tests\mutants.py` breaks the Windows guards one at a time. [windows/README.md](../windows/README.md)
has the numbers.

```powershell
powershell -ExecutionPolicy Bypass -File windows\tests\run.ps1
py -3 -I windows\tests\mutants.py
```

## Checked live

A suite can't show what Claude Code, claude.ai and Desktop do, so these were checked on the real
thing:

- Claude Code 2.1.314: `claude auth status` refreshes an expired login without a model request, a
  refused refresh empties both tokens, `/logout` revokes the refresh token.
- claude.ai, 2026-10-30: a made-up session gets 403 JSON `account_session_invalid` from
  `/api/account`, saved sessions swap without a sign-in, a page that sends half a swapped session gets
  it revoked.
- Claude Desktop 4.4212.0, 2026-10-31: TERM closes it cleanly in under a second and it removes its
  `SingletonLock`.
- claude.ai, 2026-11-02: the first Remote Control session of an account in Chrome or Desktop asks for a
  fresh sign-in, then sets `__Host-ant_trusted_device` and a device key in its IndexedDB, kept per
  account.
