# claude-switch on Windows

The Windows 11 port of claude-switch. It is a starting point: written on Linux, tested there against the suites
of the Linux version, and never run on Windows yet. This file is the handoff for whoever finishes it on the
Windows PC, an agent most likely: what is here, how it was checked, what still has to be tried on the PC, and how.

It follows `bin/claude-switch`, `share/chrome-host.py` and `share/desktop.py` as of commit bce90fb. When the Linux
version changes, the port changes with it, and `windows/tests/run.sh` says whether it still behaves the same.

## Status

| Part | Files | State |
|---|---|---|
| The command | `claude-switch.py`, `claude-switch.cmd` | Full port of `bin/claude-switch`. Passes the switch, refresh and Desktop suites of `tests/` on Linux. What only Windows does in it never ran |
| claude.ai in Chrome | `chrome-host.py`, `chrome-host.cmd`, the extension of `share/chrome-extension` as it is | Passes the Chrome suites on Linux. On Windows it talks over a named pipe, which never ran |
| Claude Desktop | `desktop.py` | Passes the Desktop suite on Linux. Where Desktop keeps its data on Windows, how to tell it runs, how to close and start it: taken from the sources below, to check on the PC |
| Background refresh | the task `claude-switch-refresh`, registered by `install.ps1` | Never ran |
| Notifications | `notify.ps1` | Never ran |
| Tab completion | `completion.ps1` | Tested with PowerShell 7.6.9 on Linux |
| Install | `install.ps1` | Parses. Never ran |

Every place that still has to be tried on the PC carries a `TODO(windows)` comment, listed below.

## Why Python

- The Linux version is bash and jq. Git Bash on the PC has neither jq nor flock, and Claude Code doesn't need
  Git for Windows anymore.
- Two of its three parts are Python already, and Desktop's cookies are a SQLite database: Python reads it with
  nothing to install, PowerShell needs a library for it.
- Python's json keeps `~/.claude.json` as claude wrote it. PowerShell 7's ConvertFrom-Json turns the dates in it
  into DateTime values and writes them back as other text, and Windows PowerShell 5.1 writes UTF-8 with a BOM.
- The PC has Python 3.12 and py.exe already.
- The suites of the Linux version run against the port here, so what is left to try is what only Windows does.

PowerShell does what only PowerShell does: the install, the Tab completion and the toast.

## Where things are on the PC

The places of the Linux version, under the user profile:

| Linux | Windows | What |
|---|---|---|
| `~/.local/bin/claude-switch` | `%USERPROFILE%\.local\bin\claude-switch.cmd` | The command, next to `claude.exe` |
| `~/.local/share/claude-switch/` | `%USERPROFILE%\.local\share\claude-switch\` | `claude-switch.py`, the helpers, `notify.ps1`, `completion.ps1`, the extension, the host manifest |
| `~/.claude/.credentials.json`, `~/.claude.json` | `%USERPROFILE%\.claude\.credentials.json`, `%USERPROFILE%\.claude.json` | The login and the account a switch moves |
| `~/.claude-accounts/` | `%USERPROFILE%\.claude-accounts\` | The accounts, the saved sessions, the lock, and `.task.log` |
| The systemd timer `claude-switch-refresh` | The task `claude-switch-refresh` | 2 and 15 minutes after logon, then every 4 hours |
| `NativeMessagingHosts/` of each Chrome | `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.lucasvecchete.claude_switch` | Where Chrome and Chrome Beta find the host |
| `~/.config/Claude` | `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude`, else `%APPDATA%\Claude` | Claude Desktop's data |
| The bash completion | A line in each PowerShell profile | Tab |
| flock on `.lock` | A byte range lock on the same file | One switch, sign-in or refresh at a time |
| The socket `.chrome.sock` | The pipe `\\.\pipe\claude-switch-chrome-USERNAME` | claude-switch to the Chrome host |
| notify-send | A toast through Windows PowerShell 5.1 | Notifications |

## Install

1. Python 3 with py.exe and pyw.exe (`winget install Python.Python.3.12`), and Claude Code from its native
   installer, so `claude.exe` is in `%USERPROFILE%\.local\bin` and that folder in the PATH.
2. In the checkout: `powershell -ExecutionPolicy Bypass -File windows\install.ps1`. No administrator. Run it again
   after any change in the checkout, `-Check` says what differs and changes nothing.
3. For claude.ai in Chrome, once: `chrome://extensions`, Developer mode, Load unpacked,
   `%USERPROFILE%\.local\share\claude-switch\chrome-extension`.
4. `claude-switch login` once for each account, then `claude-switch NAME`. `claude-switch help` has the rest.

## How it was tested

On Linux (Ubuntu 24.04, Python 3.12, PowerShell 7.6.9), on 02/11/2026. `windows/tests/run.sh` runs all of it:

| Suite | Run against | Checks |
|---|---|---|
| `tests/test-switch.sh` | `claude-switch.py` | 75 passed |
| `tests/test-refresh.sh` | `claude-switch.py` | 85 passed |
| `tests/test-switch-desktop.sh` | `claude-switch.py` | 15 passed |
| `tests/test-switch-chrome.py` | `claude-switch.py` and `chrome-host.py` | 11 passed |
| `tests/test-chrome-host.py` | `chrome-host.py` | 55 passed |
| `tests/test-desktop.py` | `desktop.py` | 51 passed |
| `windows/tests/test-switch.py` | `claude-switch.py` | 34 passed |
| `windows/tests/test-powershell.ps1` | `install.ps1`, `completion.ps1`, `notify.ps1` | 27 passed |

31 mutants of the port, each undoing one guard in a copy, were run against these suites: 30 were caught. The one
left changes how a helper runs on Windows, a branch Linux never takes. `windows/tests/test-switch.py` covers that
branch when it runs on Windows.

That proves what the port shares with Linux. None of it proves the Windows branches.

## On the PC

1. `py -3 -I windows\tests\test-switch.py`. It runs the port with a fake claude on a scratch profile, through the
   Windows branches: the `.cmd` fake, the helpers through Python, the toast through a fake `notify.ps1`, the byte
   range lock. A failure there is a bug of the port: fix the port, not the test.
2. `powershell -ExecutionPolicy Bypass -File windows\tests\test-powershell.ps1`, and the same with `pwsh` when
   PowerShell 7 is installed.
3. `install.ps1`, then once more: the second run must find nothing to install, and `-Check` nothing that differs.
4. Two real accounts: `claude-switch login` for each, `claude-switch list`, then switch back and forth with a
   session open, and check with `claude auth status` that the session follows.
5. Each `TODO(windows)` below.

## TODO(windows)

| Where | What to check | How |
|---|---|---|
| `claude-switch.py` `take_lock` | claude takes the same lock folders: `.claude\.oauth_refresh.lock`, `.claude.lock`, `.claude.json.lock` | Look for them in the profile while a session refreshes its login. A switch at that moment must wait |
| `claude-switch.py` `cmd_login` | The sign-in lands in `.credentials.json` of the sign-in folder | `claude-switch login`. "It left no Claude login" means Claude Code keeps it in the Credential Manager now, see below |
| `claude-switch.py` `ask` | The question to restart an open Desktop | Answer y, n, and nothing for 60 seconds, in Windows Terminal with PowerShell and with cmd |
| `claude-switch.py` `show_notification`, `notify.ps1` | The toast shows, also from the task, with no window | `Start-ScheduledTask claude-switch-refresh` with an account whose sign-in ends within 3 days |
| `chrome-host.py` `serve_pipe` | A switch with Chrome open, with Chrome closed (the pending file), and with two profiles | `claude-switch NAME`, `chrome-host.py --status`, Chrome's log (`chrome.exe --enable-logging --log-level=1`, then `chrome_debug.log`) |
| `desktop.py` `desktop_folder` | Which folder Desktop uses, and whether `Cookies` is in `Network` | `dir /s Cookies` under `%LOCALAPPDATA%\Packages\Claude_*` and `%APPDATA%\Claude` |
| `desktop.py` `running_on_windows` | Desktop holds `lockfile` in that folder while it runs, and its main process is found | Open Desktop, `py -3 -I %USERPROFILE%\.local\share\claude-switch\desktop.py status` |
| `desktop.py` `quit_desktop` | taskkill without /F makes Desktop quit, not hide in the tray, and it writes `config.json` on the way out | `desktop.py quit`, then `status` |
| `desktop.py` `start_on_windows` | The Store build starts from its app id, `Claude_*!*` in Get-StartApps | `desktop.py start` |
| `install.ps1` | The task repeats every 4 hours with no end, and a run leaves `.task.log` | Task Scheduler, then `%USERPROFILE%\.claude-accounts\.task.log` |
| `install.ps1` | Edge has a key of its own | Only if the PC uses Edge: `HKCU\Software\Microsoft\Edge\NativeMessagingHosts` |
| `notify.ps1` | The app id the toast uses | Whether a shortcut with an app id of its own reads better than Windows PowerShell's |

## What differs from Linux, and what can go wrong

- Claude Code keeps the login in `.credentials.json` on Windows, as on Linux (the authentication docs). A third
  party read in the 2.1.300 binary that a flag, `tengu_windows_credman`, moves it to the Credential Manager. If it
  ever does, the files a switch moves no longer hold the login, `claude-switch login` stops with "it left no Claude
  login", and the switch has to read and write the Credential Manager instead.
- The Store build of Desktop is an MSIX package, and Windows redirects its `%APPDATA%` into the package
  (anthropics/claude-code#25579). Reports say its "Edit Config" opened the other folder after an update, so the
  folder `desktop.py` picks needs a look on the PC.
- Desktop encrypts on Windows with AES-256-GCM under a key that DPAPI protects in its `Local State`, with a new
  nonce at each encryption. The saved sign-ins still go back as they are, but the same cookie no longer gives the
  same bytes, so a session Desktop wrote again is named by `lastKnownAccountUuid` rather than by its cookies. A
  saved sign-in only opens for this Windows account on this PC.
- Windows can't replace a file another program holds open. The switch tries again for 5 seconds, then stops, and
  the next command completes a switch cut between its two renames, as on Linux.
- The pipe: the first host keeps it and a second one waits, where on Linux the newest takes the socket over. It has
  the default security of a named pipe, which lets other users of the PC open it for reading.
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
- The Credential Manager flag, a third party's reading of the binary, not checked here:
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
