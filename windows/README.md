# claude-switch on Windows

The Windows 11 port of claude-switch. It follows `bin/claude-switch`, `share/chrome-host.py` and `share/desktop.py` as of
commit bce90fb, and it runs on the Windows PC since 09/11/2026. When the Linux version changes, the port changes with it:
`windows/tests/run.sh` says on Linux whether it still behaves the same, `windows\tests\run.ps1` does it on the PC.

A switch moves the same four things as on Linux: the login of Claude Code, the claude.ai session in Chrome, the sign-in of
Claude Desktop, and with the last two the Remote Control registration of each account. On Windows it also brings along a
Remote Control server started with `claude remote-control`, and the configuration of Claude Desktop, which goes over to
the next account.

## Status

| Part | Files | State |
|---|---|---|
| The command | `claude-switch.py`, `claude-switch.cmd` | Runs on the PC: the store, `list`, `rename`. No real switch yet |
| claude.ai in Chrome | `chrome-host.py`, `chrome-host.cmd`, the extension of `share/chrome-extension` as it is | The named pipe works end to end with a fake extension, a second host and the pending file. The extension is not loaded in the PC's Chrome yet |
| Claude Desktop | `desktop.py`, `localstorage.py` | Finds the folder, the main process and the account of the open Desktop on the PC. Switch, quit and the carrying over pass on a scratch profile, and the carrying over ran both ways on a copy of the PC's real data. Never quit or switched for real |
| Remote Control | `claude-switch.py`, `chrome-host.py`, `desktop.py` | The registration cookie moves with Chrome and Desktop, as on Linux. A `claude remote-control` server is stopped when you answer yes |
| Background refresh | the task `claude-switch-refresh`, registered by `install.ps1` | Ran once from Task Scheduler: result 0, `.task.log` written |
| Notifications | `notify.ps1` | The toast shows, read back from the notification center |
| Tab completion | `completion.ps1` | Windows PowerShell 5.1 on the PC, PowerShell 7.6.9 on Linux |
| Install | `install.ps1` | Ran twice on the PC: the second run found nothing to install, `-Check` nothing that differs |

## Why Python

- The Linux version is bash and jq. Git Bash on the PC has neither jq nor flock, and Claude Code doesn't need
  Git for Windows anymore.
- Two of its three parts are Python already, and Desktop's cookies are a SQLite database: Python reads it with
  nothing to install, PowerShell needs a library for it.
- Python's json keeps `~/.claude.json` as claude wrote it. PowerShell 7's ConvertFrom-Json turns the dates in it
  into DateTime values and writes them back as other text, and Windows PowerShell 5.1 writes UTF-8 with a BOM.
- The PC has Python 3.14 and py.exe already.
- The suites of the Linux version run against the port, so the Windows suites only test what Windows does.

PowerShell does what only PowerShell does: the install, the Tab completion and the toast.

## Where things are on the PC

The places of the Linux version, under the user profile:

| Linux | Windows | What |
|---|---|---|
| `~/.local/bin/claude-switch` | `%USERPROFILE%\.local\bin\claude-switch.cmd` | The command. `claude.exe` can be there (the native installer) or in `%LOCALAPPDATA%\Microsoft\WinGet\Links` (winget), it only has to be in the PATH |
| `~/.local/share/claude-switch/` | `%USERPROFILE%\.local\share\claude-switch\` | `claude-switch.py`, the helpers, `notify.ps1`, `completion.ps1`, the extension, the host manifest |
| `~/.claude/.credentials.json`, `~/.claude.json` | `%USERPROFILE%\.claude\.credentials.json`, `%USERPROFILE%\.claude.json` | The login and the account a switch moves |
| `~/.claude-accounts/` | `%USERPROFILE%\.claude-accounts\` | The accounts, the saved sessions, the lock, and `.task.log` |
| | `%USERPROFILE%\.claude-accounts\.desktop\<accountUuid>.web\` | The storage of Desktop's window as each account left it, from which what the account lost comes back |
| The systemd timer `claude-switch-refresh` | The task `claude-switch-refresh` | 2 and 15 minutes after logon, then every 4 hours |
| `NativeMessagingHosts/` of each Chrome | `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.lucasvecchete.claude_switch` | Where Chrome and Chrome Beta find the host |
| `~/.config/Claude` | `%LOCALAPPDATA%\Claude-Data\claude_<package id>`, else `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude`, else `%APPDATA%\Claude` | Claude Desktop's data. A folder Desktop moved out of keeps only a `.user-data-left.*` file and is skipped |
| The bash completion | A line in each PowerShell profile | Tab |
| flock on `.lock` | A byte range lock on the same file | One switch, sign-in or refresh at a time |
| The socket `.chrome.sock` | The pipe `\\.\pipe\claude-switch-chrome-USERNAME` | claude-switch to the Chrome host |
| notify-send | A toast through Windows PowerShell 5.1 | Notifications |

## Install

1. Python 3 with py.exe and pyw.exe (`winget install Python.Python.3.12`), and Claude Code, with
   `%USERPROFILE%\.local\bin` in the PATH.
2. In the checkout: `powershell -ExecutionPolicy Bypass -File windows\install.ps1`, from a terminal of your own. No
   administrator. Not from a Claude Desktop session: it runs in the app's MSIX container, where the registry key of the
   Chrome host lands in the container's copy, which Chrome never reads. Run it again after any change in the checkout,
   `-Check` says what differs and changes nothing.
3. For claude.ai in Chrome, once: `chrome://extensions`, Developer mode, Load unpacked,
   `%USERPROFILE%\.local\share\claude-switch\chrome-extension`.
4. `claude-switch login` once for each account, then `claude-switch NAME`. `claude-switch help` has the rest.

## How it was tested

On Linux (Ubuntu 24.04, Python 3.12, PowerShell 7.6.9), on 02/11/2026, `windows/tests/run.sh` ran the suites of the Linux
version against the port:

| Suite | Run against | Checks |
|---|---|---|
| `tests/test-switch.sh` | `claude-switch.py` | 75 passed |
| `tests/test-refresh.sh` | `claude-switch.py` | 85 passed |
| `tests/test-switch-desktop.sh` | `claude-switch.py` | 15 passed |
| `tests/test-switch-chrome.py` | `claude-switch.py` and `chrome-host.py` | 11 passed |
| `tests/test-chrome-host.py` | `chrome-host.py` | 55 passed |
| `tests/test-desktop.py` | `desktop.py` | 51 passed |

On the PC (Windows 11 Pro 26300, Python 3.14.3, Windows PowerShell 5.1, Claude Code 2.1.302, Claude Desktop 4.23118.0
from the Store, Chrome 156), on 09/11/2026, `windows\tests\run.ps1`:

| Suite | Run against | Checks |
|---|---|---|
| `windows/tests/test-switch.py` | `claude-switch.py` | 39 passed |
| `windows/tests/test-desktop.py` | `desktop.py` and `localstorage.py` | 86 passed |
| `windows/tests/test-chrome.py` | `claude-switch.py` and `chrome-host.py` | 14 passed |
| `windows/tests/test-powershell.ps1` | `install.ps1`, `completion.ps1`, `notify.ps1`, `tests/run.ps1` | 29 passed |

`windows/tests/mutants.py` breaks the Windows guards one at a time, in a copy: 69 of 69 killed.

`test-desktop.py` writes and reads Desktop's Local Storage with its own code, after LevelDB's formats, so a mistake of
`localstorage.py` can't hide behind the same mistake in the test. On a copy of the PC's real Local Storage,
`localstorage.py` read every record of its log and MANIFEST and every block of its tables with their checksums, and a
write read back with the other keys unchanged. The carrying over then ran on a copy of Desktop's real data, from one
account to the other and back: only the log grew, and no key and no setting was lost.

The suites of `tests/` drive bash, symlinks and `/proc`, so they stay on Linux. On the PC `test-desktop.py` and
`test-chrome.py` take their place, with a scratch profile, stand-in processes that hold files the way Desktop does, and a
pipe named after a user of their own. Every run that could end a process gets a fake taskkill, so they never touch the
real accounts, Chrome or Desktop.

## Checked on the PC

Read on the PC on 09/11/2026, nothing changed:

- Desktop holds `lockfile` in its folder while it runs, for itself alone, and Windows removes it when Desktop ends. Its
  main process names no folder on its command line, its helpers do in `--user-data-dir`, so the main process is the
  parent of a helper that names the folder.
- An open Desktop holds `Network\Cookies` for itself alone, reading included. `config.json` stays readable, so the
  account of an open Desktop is the one it names there.
- Desktop moved its folder to `%LOCALAPPDATA%\Claude-Data\claude_pzs8sxrjxfjjc` on 22/10/2026, and the old one keeps
  only `.user-data-left.claude_pzs8sxrjxfjjc`.
- Get-StartApps names the Store build `Claude_pzs8sxrjxfjjc!Claude`.
- Closing Desktop's window sends it to the tray (seen on 16/10/2026), so `taskkill` without `/F` doesn't quit it.
- claude 2.1.302 keeps the login in `.credentials.json`: its flag `tengu_windows_credman` is off and the Credential
  Manager holds no Claude entry. It takes the lock folders of the Linux version, `.oauth_refresh.lock` through
  proper-lockfile's `<file>.lock` folders (read in the binary).
- Windows PowerShell 5.1 never runs a native completer for a word that is only a dash, so Tab after `claude-switch -`
  offers nothing there. PowerShell 7 offers `-h` and `--help`.
- The task repeats every 4 hours with no end, and a run writes `.task.log`. The toast shows with the app id of
  Windows PowerShell.
- Desktop keeps the persisted preferences of its window twice: in Local Storage (`LSS-persisted.*`), and in
  `epitaxyPrefs` of `claude_desktop_config.json`, named after the account (`code-projects-order.<accountUuid>`) or kept
  under its scope (`<accountUuid>/<organizationUuid>`).
- The defaults of the Code tab's presets name the account's environments (`bridge:env_<id>`,
  `cloud:environment:env_<id>`).
- Desktop keeps the Code sessions of an account under `claude-code-sessions\<accountUuid>\<organizationUuid>`, and that
  organization is the one the account's saved login names.

## Left to do on the PC

1. Load the extension in Chrome (Install, step 3).
2. `claude-switch login` for each account not saved yet, then switch back and forth with a session open, and check
   with `claude auth status` that the session follows.
3. The first switch with Desktop open, from a terminal outside Desktop: answer y, n, and nothing for 60 seconds. Then
   check that the sidebar, the presets and the pins came along, and after the switch back that the drafts of the first
   account are still there.
4. A switch with Chrome open, then with Chrome closed (the pending file), and `chrome-host.py --status`.
5. A switch while `claude remote-control` runs.

## What differs from Linux, and what can go wrong

- Desktop is closed differently. Its windows are asked to close first, and after 5 seconds its process tree ends, since
  it stays in the tray otherwise. The helpers that outlive it go too, and the switch waits until Desktop's files are
  free.
- `desktop.py` never quits the Desktop that runs it, through one of its Code sessions or its terminal: `switch` exits 6
  there and says how to bring Desktop along.
- Claude Desktop keeps one storage of its window for every account (Local Storage, Session Storage, IndexedDB,
  WebStorage), and settings files beside it. Much of what they hold is named after the account: the sidebar's sections
  and groups, the order of the projects, the presets of the Code tab and their defaults. A switch copies that to the
  next account, so it starts where the last one was. The pins and the starred sessions are kept for every account, so
  they stay as they are.
- Each account keeps its own bypass permissions gate and what auto mode warned it of, the connectors it connected on
  claude.ai and what claude.ai found it may use. A setting carried over keeps the defaults of the next account's own
  environments.
- What an account can't hand over stays its own and is never deleted: its sign-in, its Code sessions and their
  results, its drafts. A switch saves the storage of the window with the account it leaves, and what went missing
  while the account was away comes back when it does. Desktop resets part of that storage when it starts signed out
  (seen on the PC on 22/10/2026). IndexedDB and WebStorage can't be merged, and claude.ai keeps the trusted device
  key of Remote Control there, so they are the account's: each account gets its own back with its sign-in, put in
  place with it and undone with it. A switch made in Desktop's own menu saves nothing, so what comes back is what the
  last switch of claude-switch saved.
- Local Storage is a LevelDB database. `localstorage.py` reads it as LevelDB does, and adds the changes as one record
  at the end of its log, which LevelDB replays at its next start. It never rewrites a file of the database. A database
  with a checksum that fails is left as it is, and the switch goes on and says what it left.
- A Remote Control server started with `claude remote-control` outside Desktop stays registered with the account it
  started on. In a terminal the switch asks whether to stop it (`[y/N]`), and what keeps it running starts it again
  on the new account. Started by hand, run it again.
- Claude Code keeps the login in `.credentials.json` on Windows, as on Linux (the authentication docs). Its flag
  `tengu_windows_credman` moves it to the Credential Manager instead. While that flag is on in `~/.claude.json`, every
  command but `list` stops and says why, since the files a switch moves would not be the login anymore.
- Desktop encrypts on Windows with AES-256-GCM under a key that DPAPI protects in its `Local State`, with a new nonce
  at each encryption. The saved sign-ins still go back as they are, but the same cookie no longer gives the same
  bytes, so a session Desktop wrote again is named by `lastKnownAccountUuid` rather than by its cookies. A saved
  sign-in only opens for this Windows account on this PC.
- Windows can't replace a file another program holds open. The switch tries again for 5 seconds, then stops, and the
  next command completes a switch cut between its two renames, as on Linux.
- The pipe: the first host keeps it and a second one waits until the first is gone, where on Linux the newest takes
  the socket over. It has the default security of a named pipe, which lets other users of the PC open it for reading.
- A Claude Desktop session runs in the app's MSIX container, where a new file in `%LOCALAPPDATA%` and a write in HKCU
  land in the container's own copy. claude-switch keeps its files under the profile, outside AppData, so it works from
  a session too (Desktop then stays on its account), but `install.ps1` has to run outside.
- Ctrl+C in `claude-switch.cmd` gets cmd's "Terminate batch job (Y/N)?" once the script stopped.
- A claude installed with npm is `claude.cmd`, so its arguments go through cmd.exe. The native `claude.exe` doesn't
  have that.
- Nothing gets a chmod on Windows: the store is private through the ACL of the profile, as Claude Code's own
  `.credentials.json` is.
- PowerShell completes file names where claude-switch has nothing to offer, after `list` or a name for instance.
- Under the execution policy Restricted, PowerShell runs no profile, so no Tab completion. `install.ps1` says so and
  changes nothing.

## Sources

- Claude Code keeps the login in `%USERPROFILE%\.claude\.credentials.json` on Windows:
  https://code.claude.com/docs/en/authentication#credential-management
- `claude.exe` is in `%USERPROFILE%\.local\bin`, Git for Windows is optional:
  https://code.claude.com/docs/en/setup, https://code.claude.com/docs/en/troubleshoot-install
- The Credential Manager flag, first read by a third party and then in the 2.1.302 binary on the PC:
  https://github.com/Vernum-Projecten/cc-shift/issues/103
- Native messaging on Windows (the registry key, a relative path, O_BINARY):
  https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging
- A batch file as the host, with `@echo off`:
  https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Native_messaging
- Python has no AF_UNIX socket on Windows: https://github.com/python/cpython/issues/77589
- Desktop's data in the MSIX package: https://github.com/anthropics/claude-code/issues/25579
- Electron's safeStorage on Windows (a DPAPI key in `Local State`, `v10`, AES-256-GCM):
  https://github.com/tddworks/ClaudeBar/pull/656, https://github.com/Lectern-HQ/lectern-cookie-reader/pull/62
- Register-ArgumentCompleter:
  https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/register-argumentcompleter
- WinRT toasts work in Windows PowerShell 5.1 and not in PowerShell 7:
  https://smsagent.blog/2024/10/29/popping-toast-notifications-in-powershell-core/
