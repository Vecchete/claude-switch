# Tests

`tests/run.sh` runs every suite against the files of the checkout, never the installed ones, each on a
scratch `HOME`:

| Suite | What it covers |
|---|---|
| `test-switch.sh` | the switch, the prompt, login and rename, with fake logins and the real `claude auth status` |
| `test-refresh.sh` | the background refresh, with a fake claude |
| `test-chrome-host.py` | the Chrome host, with a fake extension, a fake claude.ai and a cookie jar |
| `test-switch-chrome.py` | `claude-switch` driving the Chrome host end to end |
| `test-worker.mjs` | the extension's worker, with a fake `chrome` API (needs `node`) |
| `test-install.sh` | `install.sh`, with a fake `systemctl` |

```bash
tests/run.sh                 # every suite
tests/run.sh switch install  # some of them
```

## Mutants

`tests/mutants.py` breaks one guard at a time, in a copy, and runs the suite that must catch it. Every
mutant undoes something a review or a live incident asked for, so a mutant that stays green is a guard
no test holds. Every one of them must end `killed`.

```bash
tests/mutants.py             # all, one at a time
tests/mutants.py install     # the mutants of one file: switch, host or install
```

Run them one at a time. The Chrome host suite depends on timing, and a run under load that fails for
that reason counts a mutant as killed when it is not.

## Checked live

A suite can't show what Claude Code and claude.ai do, so these were checked on the real
thing:

- Claude Code 2.1.314: `claude auth status` refreshes an expired login without a model request, a
  refused refresh empties both tokens, `/logout` revokes the refresh token.
- claude.ai, 2026-10-30: a made-up session gets 403 JSON `account_session_invalid` from
  `/api/account`, saved sessions swap without a sign-in, a page that sends half a swapped session gets
  it revoked.
