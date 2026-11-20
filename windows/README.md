# claude-switch on Windows

The Windows 11 port of claude-switch. It is experimental: it was tested on one Windows 11 machine, and a
few things were never run there for real yet, see [Status](#status) and
[Not verified yet](#not-verified-yet).

The port follows `bin/claude-switch`, `share/chrome-host.py`, `share/desktop.py` and `share/usage.py` as of
commit 846289c, [gateway keys](../docs/gateway-keys.md) and [automatic switching](../docs/auto.md) included. When the Linux version changes, the port changes with it:
`windows/tests/run.sh` says on Linux whether it still behaves the same, and `windows\tests\run.ps1` checks
on Windows what only Windows does.

A switch moves the same things as on Linux: the login of Claude Code, the claude.ai session in Chrome,
the sign-in of Claude Desktop, and with the last two the Remote Control registration of each account. A
gateway key goes in and out of the `env` of `%USERPROFILE%\.claude\settings.json`, as on Linux. On
Windows a switch also brings along a Remote Control server started with `claude remote-control`, and the
settings Claude Desktop keeps per account, which go over to the next account.

## Status

| Part | Files | State |
|---|---|---|
| The command | `claude-switch.py`, `claude-switch.cmd` | Runs on Windows: the store, `list` and `rename`. No real switch yet |
| Gateway keys | `claude-switch.py` | `key import`, `add`, `renew` and `remove` and the switches to and from a key pass on scratch profiles, on Windows and on Linux. Not tried against a real gateway yet |
| Automatic switching | `claude-switch.py`, `usage.py`, the scheduled task `claude-switch-watch` | `auto on` and `off`, `auto order`, the LEFT column, the watch and the hook pass on a scratch profile against a stand-in usage endpoint and gateway. On the test machine `list` read both subscriptions, `auto on` wrote the hook and turned the task on, and the task ran from Task Scheduler with result 0. No real session has met a limit with it on Windows yet |
| claude.ai in Chrome | `chrome-host.py`, `chrome-host.cmd`, and the extension of `share/chrome-extension` as it is | The named pipe works end to end with a stand-in extension, a second host and the pending file. Not tried with the extension loaded in Chrome yet |
| Claude Desktop | `desktop.py`, `localstorage.py` | Finds the folder, the main process and the account of a running Desktop. Switch, quit and the carrying over pass on a scratch profile, and the carrying over ran both ways on a copy of real Desktop data. Never quit or switched for real |
| Remote Control | `claude-switch.py`, `chrome-host.py`, `desktop.py` | The registration cookie moves with Chrome and Desktop, as on Linux. A `claude remote-control` server is stopped when you answer yes |
| Background refresh | The scheduled task `claude-switch-refresh`, registered by `install.ps1` | Ran from Task Scheduler with result 0, and wrote its log |
| Notifications | `notify.ps1` | The toast shows, and it's in the notification center |
| Tab completion | `completion.ps1` | Windows PowerShell 5.1 and PowerShell 7.6.9 |
| Install | `install.ps1` | A second run found nothing to install, and `-Check` nothing that differs |

## Requirements

- Windows 11, Python 3 with `py.exe` and `pyw.exe` (`winget install Python.Python.3.12`), and Claude
  Code, with `%USERPROFILE%\.local\bin` in the `PATH`.
- Optional: Google Chrome for claude.ai, and Claude Desktop from the Microsoft Store.

## Install

1. From a checkout of the repository, in a terminal of your own and without administrator rights:

   ```powershell
   powershell -ExecutionPolicy Bypass -File windows\install.ps1
   ```

   Don't run it from a Claude Desktop session. Those run in the MSIX container of the app, where the
   registry key of the Chrome host lands in a copy that Chrome never reads. Run it again after a
   `git pull`. `-Check` says what differs and changes nothing.

2. For claude.ai in Chrome, load the extension once: open `chrome://extensions`, turn on Developer mode,
   click Load unpacked and pick `%USERPROFILE%\.local\share\claude-switch\chrome-extension`.

3. Run `claude-switch login` once for each account, then `claude-switch NAME`. A gateway key is kept with
   `claude-switch key import NAME` or `claude-switch key add NAME`, see
   [Gateway keys](../docs/gateway-keys.md). In the prompt, `a` asks for the kind of account first: `s` signs
   in a subscription, and `k` keeps an API key or a gateway key. It asks for a name, the URL of the gateway
   (Enter for the Anthropic API) and then the key. `claude-switch help` has the rest.

## Where things are

The places of the Linux version, under the user profile:

| Linux | Windows | What |
|---|---|---|
| `~/.local/bin/claude-switch` | `%USERPROFILE%\.local\bin\claude-switch.cmd` | The command. `claude.exe` can be in that folder (the native installer) or in `%LOCALAPPDATA%\Microsoft\WinGet\Links` (winget), it only has to be in the `PATH` |
| `~/.local/share/claude-switch/` | `%USERPROFILE%\.local\share\claude-switch\` | `claude-switch.py`, the helpers (`usage.py` among them), `notify.ps1`, `completion.ps1`, the extension and the host manifest |
| `~/.claude/.credentials.json`, `~/.claude.json` | `%USERPROFILE%\.claude\.credentials.json`, `%USERPROFILE%\.claude.json` | The login and the account a switch moves |
| `~/.claude/settings.json` | `%USERPROFILE%\.claude\settings.json` | Where the entries of a gateway key go, in its `env` |
| `~/.claude-accounts/` | `%USERPROFILE%\.claude-accounts\` | The accounts, the gateway keys (`key_<name>.json`), the saved sessions, the lock, `.task.log`, and what automatic switching keeps: `.auto`, `.auto-order`, `.usage\`, `.hold`, `.switched`, `.woken\` and `.auto.log` |
| | `%USERPROFILE%\.claude-accounts\.desktop\<accountUuid>.web\` | The storage of Desktop's window as each account left it, from which what the account lost comes back |
| The systemd timer `claude-switch-refresh` | The scheduled task `claude-switch-refresh` | 2 and 15 minutes after logon, then every 4 hours |
| The systemd timer `claude-switch-watch` | The scheduled task `claude-switch-watch` | Every 5 minutes, turned on only while automatic switching is |
| The `StopFailure` hook `~/.local/bin/claude-switch hook` | `pyw.exe -3 -I %USERPROFILE%\.local\share\claude-switch\claude-switch.py hook`, a program and its arguments | What Claude Code runs when a turn ends on a limit |
| `NativeMessagingHosts/` of each Chrome | `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.lucasvecchete.claude_switch` | Where Chrome and Chrome Beta find the host |
| `~/.config/Claude` | `%LOCALAPPDATA%\Claude-Data\claude_<package id>`, else `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude`, else `%APPDATA%\Claude` | The data of Claude Desktop. A folder Desktop moved out of keeps only a `.user-data-left.*` file and is skipped |
| The bash completion | A line in each PowerShell profile | Tab completion |
| `flock` on `.lock` | A byte range lock on the same file | One switch, sign-in or refresh at a time |
| The socket `.chrome.sock` | The pipe `\\.\pipe\claude-switch-chrome-USERNAME` | claude-switch to the Chrome host |
| `notify-send` | A toast through Windows PowerShell 5.1 | Notifications |

## Why Python

- The Linux version is bash and jq. Git Bash has neither jq nor `flock`, and Claude Code doesn't need Git
  for Windows anymore.
- Two of its three parts are Python already, and the cookies of Desktop are a SQLite database. Python
  reads it with nothing to install, while PowerShell needs a library for it.
- The json module of Python keeps `~/.claude.json` as claude wrote it. `ConvertFrom-Json` of PowerShell 7
  turns the dates in it into DateTime values and writes them back as other text, and Windows PowerShell
  5.1 writes UTF-8 with a BOM.
- The suites of the Linux version run against the port, so the Windows suites only test what Windows
  does.

PowerShell does what only PowerShell does: the install, the Tab completion and the toast.

## What differs from Linux

- Desktop is closed differently. Its windows are asked to close first, and after 5 seconds its process
  tree ends, since it stays in the tray otherwise. The helpers that outlive it go too, and the switch
  waits until the files of Desktop are free.
- `desktop.py` never quits the Desktop that runs it, through one of its Code sessions or its terminal, as on
  Linux: `switch` exits 6 there and says how to bring Desktop along. On Windows any Desktop among the parents
  of the process counts, where Linux looks for the Desktop that holds the folder.
- Claude Desktop keeps one storage of its window for every account (Local Storage, Session Storage,
  IndexedDB, WebStorage), and settings files beside it. Much of what they hold is named after the account:
  the sections and groups of the sidebar, the order of the projects, the presets of the Code tab and their
  defaults. A switch copies that to the next account, so it starts where the last one was. The pins and
  the starred sessions are kept for every account, so they stay as they are.
- Each account keeps its own bypass permissions gate and what auto mode and the permission modes warned
  it of, the connectors it connected on claude.ai and what claude.ai found it may use. The permission mode
  picked for each folder goes over, and the gate still decides whether bypass is offered. A setting carried
  over keeps the defaults of the next account's own environments.
- What an account can't hand over stays its own and is never deleted: its sign-in, its Code sessions and
  their results, its drafts. A switch saves the storage of the window with the account it leaves, and what
  went missing while the account was away comes back when it does. Desktop resets part of that storage
  when it starts signed out. IndexedDB and WebStorage can't be merged, and claude.ai keeps the trusted
  device key of Remote Control there, so they belong to the account: each account gets its own back with
  its sign-in, put in place with it and undone with it. A switch made in the menu of Desktop saves
  nothing, so what comes back is what the last switch of claude-switch saved.
- Local Storage is a LevelDB database. `localstorage.py` reads it as LevelDB does, and adds the changes as
  one record at the end of its log, which LevelDB replays at its next start. It never rewrites a file of
  the database. A database with a checksum that fails is left as it is, and the switch goes on and says
  what it left.
- A Remote Control server started with `claude remote-control` outside Desktop stays registered with the
  account it started on. In a terminal the switch asks whether to stop it, and what keeps it running
  starts it again on the new account. A server started by hand needs to be started again.
- Claude Code keeps the login in `.credentials.json` on Windows, as on Linux (see its authentication
  docs). Its flag `tengu_windows_credman` moves it to the Credential Manager instead. While that flag is on
  in `~/.claude.json`, every command but `list` stops and says why, since the files a switch moves would
  not be the login anymore.
- Desktop encrypts on Windows with AES-256-GCM, under a key that DPAPI protects in its `Local State`, with
  a new nonce at each encryption. The saved sign-ins still go back as they are, but the same cookie no
  longer gives the same bytes, so a session Desktop wrote again is named by `lastKnownAccountUuid` rather
  than by its cookies. A saved sign-in only opens for the same Windows account on the same machine.
- Windows can't replace a file another program holds open. The switch tries again for 5 seconds, then
  stops, and the next command completes a switch cut between its two renames, as on Linux.
- A switch to or from a gateway key writes `settings.json` aside and renames it into place, as on Linux.
  Windows has no mode, owner and group to keep: the new file takes the ACL of its folder, as
  `.claude.json` does at a switch. Windows can't rename a file over one marked read-only, so a
  `settings.json` with that attribute stops such a switch before anything changes, with the `attrib`
  command that clears it. A folder an ACL keeps you from writing in stops it too, but the read-only
  attribute of a folder means nothing on Windows and such an ACL only shows when a file is made, so the
  switch makes and removes one there first. It doesn't use Python's `tempfile` for that, which takes the
  access denied of Windows for a name already in use and tries other names without end.
- A gateway token piped into `key add` or `key renew` from Windows PowerShell can come with a BOM in
  front, which is dropped, and a Windows line end goes with the other whitespace. Typed at the prompt, the
  token goes through the console without echo.
- The file of a gateway key replaces what a file name can't hold with `_`, as on Linux. On NTFS that
  matters more: a colon would name a stream of another file. NTFS also takes `key_GW.json` and
  `key_gw.json` for the same file, so a key named like another but for the case is refused.
- The pipe: the first host keeps it, and a second one waits until the first is gone, where on Linux the
  newest host takes the socket over. It has the default security of a named pipe, which lets the other
  users of the machine open it for reading.
- A Claude Desktop session runs in the MSIX container of the app, where a new file in `%LOCALAPPDATA%` and
  a write in HKCU land in the container's own copy. claude-switch keeps its files under the profile,
  outside AppData, so it works from such a session too, and Desktop then stays on its account. But
  `install.ps1` has to run outside.
- Ctrl+C in `claude-switch.cmd` gets the "Terminate batch job (Y/N)?" of cmd once the script stopped.
- A claude installed with npm is `claude.cmd`, so its arguments go through cmd.exe. The native
  `claude.exe` doesn't have that problem.
- Nothing gets a chmod on Windows. The store is private through the ACL of the profile, as the
  `.credentials.json` of Claude Code is.
- PowerShell completes file names where claude-switch has nothing to offer, after `list` or a name for
  instance.
- Under the execution policy Restricted, PowerShell runs no profile, so there is no Tab completion.
  `install.ps1` says so and changes nothing.
- The `StopFailure` hook of automatic switching is a program and its arguments in `settings.json`, with no
  shell between: the hooks reference says a `.cmd` can't be started that way on Windows, and a command line
  would go to Git Bash or to PowerShell, whichever is installed. The program is the `pyw.exe` of the py
  launcher, so no console window opens, else the `pythonw.exe` of the Python that runs claude-switch.
  `auto on` writes it again when it finds the hook of another path or another form.
- The watch is the scheduled task `claude-switch-watch`, every 5 minutes, and a run lasts 5 minutes at
  most. `install.ps1` registers it turned off unless automatic switching is on, and `claude-switch auto on`
  and `off` turn it on and off with `schtasks`.
- A session counts as open while the process its file in `~/.claude/sessions` names runs and was created at
  the moment its `procStart` says. Claude Code writes the creation time of the process there on Windows, in
  units of 100 ns since 1601, where Linux has the start time `/proc` gives.
- A hook of a Code session of Claude Desktop runs inside Desktop, so an automatic switch there leaves Desktop
  on its account, as a switch typed in such a session does, and the notification says how to bring it along.
  That session isn't woken: it goes on with the login of Desktop, which only a restart of Desktop changes.
  The watch runs outside Desktop and restarts it.
- In a hook, what the helpers of claude-switch say goes to `.auto.log` too. Windows hands a program its
  standard handles rather than the files Python writes to, so claude-switch gives them the log.
- `usage.py` reads JSON as UTF-8 whatever the code page of Windows, and writes what it says in UTF-8, so a
  profile path or a name that isn't ASCII comes through to claude-switch whole. A reading another run has
  open is replaced once that run lets go of it.

## Tests

On Linux (Ubuntu 24.04, Python 3.12, PowerShell 7.6.9), on 2026-11-02, `windows/tests/run.sh` ran the
suites of the Linux version against the port:

| Suite | Run against | Checks |
|---|---|---|
| `tests/test-switch.sh` | `claude-switch.py` | 75 passed |
| `tests/test-refresh.sh` | `claude-switch.py` | 85 passed |
| `tests/test-switch-desktop.sh` | `claude-switch.py` | 15 passed |
| `tests/test-switch-chrome.py` | `claude-switch.py` and `chrome-host.py` | 11 passed |
| `tests/test-chrome-host.py` | `chrome-host.py` | 55 passed |
| `tests/test-desktop.py` | `desktop.py` | 51 passed |

Those runs came before the gateway keys and automatic switching. `windows/tests/run.sh` now runs
`tests/test-keys.sh`, `tests/test-usage.py` and `tests/test-auto.sh` against the port too, which haven't run
yet: the shell suites need `jq` and the Linux `claude`. On 2026-11-20, on Linux in WSL
(Ubuntu 26.04, Python 3.14.4), `windows/tests/test-switch.py` passed 38 checks, `windows/tests/test-keys.py`
145 and `windows/tests/test-auto.py` 81, with what only Linux does in place of what only Windows does.
`tests/test-usage.py` passed 68 against `usage.py` with `TZ=UTC`. In the zone of the PC, CET, 5 of its
checks fail against `share/usage.py` as well: the test builds the times it expects in its own zone, and runs
`usage.py` in UTC.

On Windows 11 Pro 26300, with Python 3.14.3 and Windows PowerShell 5.1, on 2026-11-20,
`windows\tests\run.ps1` ran:

| Suite | Run against | Checks |
|---|---|---|
| `windows/tests/test-switch.py` | `claude-switch.py` | 43 passed |
| `windows/tests/test-keys.py` | `claude-switch.py` | 146 passed |
| `windows/tests/test-auto.py` | `claude-switch.py` and `usage.py` | 82 passed |
| `windows/tests/test-desktop.py` | `desktop.py` and `localstorage.py` | 86 passed |
| `windows/tests/test-chrome.py` | `claude-switch.py` and `chrome-host.py` | 14 passed |
| `windows/tests/test-powershell.ps1` | `install.ps1`, `completion.ps1`, `notify.ps1`, `tests/run.ps1` | 46 passed |

`windows/tests/mutants.py` breaks the Windows guards, those of the gateway keys, those of automatic switching
and those of their Tab completion one at a time, in a copy: 154 of 154 killed. Eight runs of `test-keys.py` at once, the load of
`mutants.py -j 8`, all passed, so no mutant was killed by a check that fails under load.

`test-desktop.py` writes and reads the Local Storage of Desktop with its own code, after the formats of
LevelDB, so a mistake of `localstorage.py` can't hide behind the same mistake in the test. On a copy of a
real Local Storage, `localstorage.py` read every record of its log and MANIFEST and every block of its
tables with their checksums, and a write read back with the other keys unchanged. The carrying over then
ran on a copy of real Desktop data, from one account to the other and back: only the log grew, and no key
and no setting was lost.

The suites of `tests/` drive bash, symbolic links and `/proc`, so they stay on Linux. On Windows,
`test-keys.py`, `test-auto.py`, `test-desktop.py` and `test-chrome.py` take their place, with a scratch
profile, a stand-in usage endpoint and gateway on the machine itself, a
stand-in claude, stand-in processes that hold files the way Desktop does, and a pipe named after a user of
their own. Every run that could end a process gets a stand-in taskkill, so they never touch the real
accounts, Chrome or Desktop. `test-keys.py` and `test-auto.py` give a folder of their scratch profile an
ACL that denies making files, and remove it again before they delete the folder. `test-auto.py` keeps its
profile under a path that isn't ASCII.

## Behavior seen on Windows

These were read on the test machine, without changing anything:

- Desktop holds `lockfile` in its folder while it runs, for itself alone, and Windows removes it when
  Desktop ends. Its main process names no folder on its command line, its helpers do in
  `--user-data-dir`, so the main process is the parent of a helper that names the folder.
- A running Desktop holds `Network\Cookies` for itself alone, reading included. `config.json` stays
  readable, so the account of a running Desktop is the one it names there.
- Desktop from the Store moved its folder to `%LOCALAPPDATA%\Claude-Data\claude_pzs8sxrjxfjjc`, and the
  old folder keeps only `.user-data-left.claude_pzs8sxrjxfjjc`. Get-StartApps names that build
  `Claude_pzs8sxrjxfjjc!Claude`.
- Closing the window of Desktop sends it to the tray, so `taskkill` without `/F` doesn't quit it.
- claude 2.1.302 keeps the login in `.credentials.json`: its flag `tengu_windows_credman` is off and the
  Credential Manager holds no Claude entry. It takes the lock folders of the Linux version,
  `.oauth_refresh.lock` through the `<file>.lock` folders of proper-lockfile.
- Windows PowerShell 5.1 never runs a native completer for a word that is only a dash, so Tab after
  `claude-switch -` offers nothing there. PowerShell 7 offers `-h` and `--help`.
- The task repeats every 4 hours with no end, and each run writes `.task.log`. The toast shows with the
  app id of Windows PowerShell.
- Desktop keeps the persisted preferences of its window twice: in Local Storage (`LSS-persisted.*`), and
  in `epitaxyPrefs` of `claude_desktop_config.json`, named after the account
  (`code-projects-order.<accountUuid>`) or kept under its scope (`<accountUuid>/<organizationUuid>`).
- The defaults of the presets of the Code tab name the environments of the account (`bridge:env_<id>`,
  `cloud:environment:env_<id>`).
- Desktop keeps the Code sessions of an account under
  `claude-code-sessions\<accountUuid>\<organizationUuid>`, and that organization is the one the saved
  login of the account names.
- Windows refuses to rename a file over one marked read-only ("Access is denied"), and `os.access` of
  Python says such a file can't be written.
- In a folder whose ACL keeps you from making files, `tempfile.mkstemp` of Python 3.14 takes the access
  denied for a name already in use and tries other names without end. A switch to a key hung there in
  `windows/tests/test-keys.py` until claude-switch made its own test file instead.
- Anthropic renamed Max 5x to Max and Max 20x to Ultra on 2026-10-30. Claude Code 2.1.302 still says
  `max` for both, in the login and in the profile, and tells Ultra by the rate limit tier
  `default_claude_max_20x`. A token refresh keeps the tier the login was given, while the profile in
  `oauthAccount` is fetched again at each start of Claude Code, so the two can differ.

## Not verified yet

What a tester on Windows can still check:

1. Load the extension in Chrome, step 2 of [Install](#install).
2. Run `claude-switch login` for each account, then switch back and forth with a session open, and check
   with `claude auth status` that the session follows.
3. Switch with Desktop open, from a terminal outside Desktop: answer y, n, and nothing for 60 seconds.
   Then check that the sidebar, the presets and the pins came along, and after the switch back that the
   drafts of the first account are still there.
4. Switch with Chrome open, then with Chrome closed (the pending file), and run `chrome-host.py --status`.
5. Switch while `claude remote-control` runs.
6. Keep a gateway key with `claude-switch key add`, typing the token at the prompt in Windows Terminal,
   with PowerShell and with cmd. Then switch to it and back with a session open, and check with
   `claude auth status` that the session follows.
7. With automatic switching on, let a session in a terminal meet the weekly limit of its account: the
   machine should move to the next account and the session carry on by itself. Then let the watch task
   bring the machine back once that limit resets, with Desktop open.

## Sources

- Claude Code keeps the login in `%USERPROFILE%\.claude\.credentials.json` on Windows:
  https://code.claude.com/docs/en/authentication#credential-management
- `claude.exe` is in `%USERPROFILE%\.local\bin`, and Git for Windows is optional:
  https://code.claude.com/docs/en/setup, https://code.claude.com/docs/en/troubleshoot-install
- The Credential Manager flag, first read by a third party and then in the 2.1.302 binary:
  https://github.com/Vernum-Projecten/cc-shift/issues/103
- Native messaging on Windows (the registry key, a relative path, O_BINARY):
  https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging
- A batch file as the host, with `@echo off`:
  https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Native_messaging
- Python has no AF_UNIX socket on Windows: https://github.com/python/cpython/issues/77589
- The data of Desktop in the MSIX package: https://github.com/anthropics/claude-code/issues/25579
- The safeStorage of Electron on Windows (a DPAPI key in `Local State`, `v10`, AES-256-GCM):
  https://github.com/tddworks/ClaudeBar/pull/656, https://github.com/Lectern-HQ/lectern-cookie-reader/pull/62
- Register-ArgumentCompleter:
  https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/register-argumentcompleter
- Max 20x is called Ultra since 2026-10-30, and existing subscribers moved to it:
  https://support.claude.com/en/articles/17728952-what-is-the-ultra-plan
- WinRT toasts work in Windows PowerShell 5.1 and not in PowerShell 7:
  https://smsagent.blog/2024/10/29/popping-toast-notifications-in-powershell-core/
- A hook with `args` is a program started with no shell, which on Windows has to be a real executable, and
  `asyncRewake` wakes the session on exit code 2: https://code.claude.com/docs/en/hooks
