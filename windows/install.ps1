<#
.SYNOPSIS
Installs the Windows port of claude-switch from this checkout into the places it runs from.

.DESCRIPTION
Run it again after any change in the checkout: it copies what differs and leaves the rest alone.

    powershell -ExecutionPolicy Bypass -File windows\install.ps1           install
    powershell -ExecutionPolicy Bypass -File windows\install.ps1 -Check    say what differs from this checkout,
                                                                            change nothing, fail when something does

The places are the ones of the Linux version, under the user profile: ~\.local\bin\claude-switch.cmd next to
claude.exe, and ~\.local\share\claude-switch for the rest. It also registers the Chrome host for this user, the
claude-switch-refresh task and the Tab completion in the PowerShell profiles. The accounts, the saved sessions and
every login stay in ~\.claude-accounts, never in this checkout.

It needs Python 3 with its py launcher (winget install Python.Python.3.12) and Claude Code. It works in Windows
PowerShell 5.1 and in PowerShell 7, and needs no administrator.

Ran on the PC on 09/11/2026 from a terminal outside Claude Desktop: the second run found nothing to install, and -Check
nothing that differs. From a Claude Desktop session the registry key would land in the app's container, out of Chrome's
reach.
#>
param([switch]$Check)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$Windows = $PSScriptRoot
$Root = Split-Path -Parent $Windows
$Bin = Join-Path $HOME '.local\bin'
$Share = Join-Path $HOME '.local\share\claude-switch'
$Store = Join-Path $HOME '.claude-accounts'
$HostName = 'com.lucasvecchete.claude_switch'
$HostManifestPath = Join-Path $Share "$HostName.json"
$TaskName = 'claude-switch-refresh'
$Documents = [Environment]::GetFolderPath('MyDocuments')
# Windows PowerShell 5.1 and PowerShell 7 each read a profile of their own.
$Profiles = @((Join-Path $Documents 'WindowsPowerShell\profile.ps1'), (Join-Path $Documents 'PowerShell\profile.ps1'))
$ProfileLine = ". `"$Share\completion.ps1`""

$script:Differs = $false
$script:Changed = New-Object System.Collections.Generic.List[string]

function Write-Difference([string]$What) {
    Write-Output "differs: $What"
    $script:Differs = $true
}

# Puts SOURCE at TARGET, or with -Check says that it differs.
function Install-File([string]$Source, [string]$Target) {
    if ((Test-Path -LiteralPath $Target) -and
        (Get-FileHash -LiteralPath $Source).Hash -eq (Get-FileHash -LiteralPath $Target).Hash) {
        return
    }
    if ($Check) {
        Write-Difference $Target
        return
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Target) | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Target -Force
    $script:Changed.Add($Target)
    Write-Output "installed $Target"
}

# Puts TEXT in TARGET, UTF-8 without a BOM, or with -Check says that it differs.
function Install-Text([string]$Text, [string]$Target) {
    $temporary = [System.IO.Path]::GetTempFileName()
    try {
        [System.IO.File]::WriteAllText($temporary, $Text, (New-Object System.Text.UTF8Encoding $false))
        Install-File $temporary $Target
    } finally {
        Remove-Item -LiteralPath $temporary -Force
    }
}

# The id Chrome gives the extension, from the key in its manifest.
function Get-ExtensionId {
    $manifest = Get-Content -LiteralPath (Join-Path $Root 'share\chrome-extension\manifest.json') -Raw -Encoding UTF8 |
        ConvertFrom-Json
    $digest = [System.Security.Cryptography.SHA256]::Create().ComputeHash([Convert]::FromBase64String($manifest.key))
    $hex = -join ($digest[0..15] | ForEach-Object { $_.ToString('x2') })
    -join ($hex.ToCharArray() | ForEach-Object { [char](97 + [Convert]::ToInt32([string]$_, 16)) })
}

$Python = Get-Command 'py.exe' -ErrorAction SilentlyContinue
if (-not $Python) {
    throw 'claude-switch needs Python 3 and its py launcher: winget install Python.Python.3.12'
}
if (-not (Get-Command 'claude' -ErrorAction SilentlyContinue)) {
    Write-Warning 'claude is not in the PATH. claude-switch needs Claude Code.'
}
if (($env:Path -split ';') -notcontains $Bin) {
    Write-Warning "$Bin is not in the PATH, where Claude Code's installer puts claude.exe and this puts claude-switch."
}

Install-File (Join-Path $Windows 'claude-switch.cmd') (Join-Path $Bin 'claude-switch.cmd')
foreach ($file in 'claude-switch.py', 'chrome-host.py', 'chrome-host.cmd', 'desktop.py', 'notify.ps1', 'completion.ps1') {
    Install-File (Join-Path $Windows $file) (Join-Path $Share $file)
}
foreach ($file in 'manifest.json', 'worker.js') {
    Install-File (Join-Path $Root "share\chrome-extension\$file") (Join-Path $Share "chrome-extension\$file")
}

# The native messaging host of the extension. Chrome and Chrome Beta look for it under the same key on Windows.
# Edge looks under HKCU\Software\Microsoft\Edge\NativeMessagingHosts. The PC uses Chrome for claude.ai, so it is not set.
$HostPath = (Join-Path $Share 'chrome-host.cmd').Replace('\', '\\')
$ExtensionId = Get-ExtensionId
$HostManifest = @"
{
  "name": "$HostName",
  "description": "claude-switch: claude.ai in Chrome follows the Claude account switch",
  "path": "$HostPath",
  "type": "stdio",
  "allowed_origins": ["chrome-extension://$ExtensionId/"]
}

"@
Install-Text $HostManifest $HostManifestPath
$HostKey = "HKCU:\Software\Google\Chrome\NativeMessagingHosts\$HostName"
$registered = $null
if (Test-Path -LiteralPath $HostKey) {
    $registered = (Get-Item -LiteralPath $HostKey).GetValue('')
}
if ($registered -ne $HostManifestPath) {
    if ($Check) {
        Write-Difference $HostKey
    } else {
        # One key at a time: New-Item -Force on a key that exists would wipe what is in it.
        $key = 'HKCU:\'
        foreach ($part in 'Software', 'Google', 'Chrome', 'NativeMessagingHosts', $HostName) {
            $key = Join-Path $key $part
            if (-not (Test-Path -LiteralPath $key)) {
                New-Item -Path $key | Out-Null
            }
        }
        Set-Item -LiteralPath $HostKey -Value $HostManifestPath
        $script:Changed.Add($HostKey)
        Write-Output "registered $HostKey"
    }
}

# The refresh of the accounts that are not active, as the timer of the Linux version: 2 and 15 minutes after logon
# and every 4 hours, and a run missed while the PC was off comes as soon as it can. pyw.exe runs it without a
# console window, so claude-switch writes to ~\.claude-accounts\.task.log.
# On the PC (09/11/2026) the 4 hours repeat with no end, and a run started from Task Scheduler left .task.log.
$Pyw = Get-Command 'pyw.exe' -ErrorAction SilentlyContinue
$Runner = if ($Pyw) { $Pyw.Source } else { $Python.Source }
$Arguments = "-3 -I `"$Share\claude-switch.py`" refresh"
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$current = ''
if ($task) {
    $current = "$($task.Actions[0].Execute) $($task.Actions[0].Arguments)"
}
if ($current -ne "$Runner $Arguments") {
    if ($Check) {
        Write-Difference "the scheduled task $TaskName"
    } else {
        if (-not $Pyw) {
            Write-Warning 'pyw.exe was not found: each run of the task shows a console window for a moment.'
        }
        $user = "$env:USERDOMAIN\$env:USERNAME"
        $afterLogon = New-ScheduledTaskTrigger -AtLogOn -User $user
        $afterLogon.Delay = 'PT2M'
        $laterAfterLogon = New-ScheduledTaskTrigger -AtLogOn -User $user
        $laterAfterLogon.Delay = 'PT15M'
        $everyFourHours = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Hours 4)
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
        $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
        $action = New-ScheduledTaskAction -Execute $Runner -Argument $Arguments -WorkingDirectory $HOME
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $afterLogon, $laterAfterLogon, $everyFourHours `
            -Settings $settings -Principal $principal -Force `
            -Description 'Refresh the logins of the Claude Code accounts that are not active' | Out-Null
        $script:Changed.Add($TaskName)
        Write-Output "registered the scheduled task $TaskName"
    }
}

# Tab completion: one line in each profile loads completion.ps1.
foreach ($profilePath in $Profiles) {
    if ((Test-Path -LiteralPath $profilePath) -and
        ((Get-Content -LiteralPath $profilePath -Encoding UTF8) -contains $ProfileLine)) {
        continue
    }
    if ($Check) {
        Write-Difference $profilePath
        continue
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $profilePath) | Out-Null
    Add-Content -LiteralPath $profilePath -Value $ProfileLine -Encoding UTF8
    $script:Changed.Add($profilePath)
    Write-Output "added the Tab completion of claude-switch to $profilePath"
}
# The policy a new window of this PowerShell gets: the first one set apart from this process, which runs with Bypass,
# else the default of the edition. The other edition keeps a policy of its own.
$policy = 'RemoteSigned'
$policies = @(Get-ExecutionPolicy -List |
    Where-Object { $_.Scope -ne 'Process' -and "$($_.ExecutionPolicy)" -ne 'Undefined' })
if ($policies.Count -gt 0) {
    $policy = "$($policies[0].ExecutionPolicy)"
} elseif ($PSVersionTable.PSEdition -eq 'Desktop') {
    $policy = 'Restricted'
}
if ($policy -in 'Restricted', 'AllSigned') {
    Write-Warning ("PowerShell runs no profile under the execution policy $policy, so Tab doesn't complete " +
        'claude-switch. Set-ExecutionPolicy -Scope CurrentUser RemoteSigned allows it.')
}

if ($Check) {
    if (-not $script:Differs) {
        Write-Output "Everything installed matches $Root."
    }
    exit ([int]$script:Differs)
}

# Chrome keeps running the host it started. A host stopped between two switches is started again by the extension
# within 10 seconds, with the new code. It stops under the store lock, so no switch is cut in the middle.
if ($script:Changed.Contains((Join-Path $Share 'chrome-host.py'))) {
    $hostScript = (Join-Path $Share 'chrome-host.py').ToLowerInvariant()
    $running = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine.ToLowerInvariant().Contains($hostScript) })
    if ($running.Count -gt 0) {
        New-Item -ItemType Directory -Force -Path $Store | Out-Null
        # The byte range lock claude-switch takes with msvcrt.locking.
        $lock = [System.IO.File]::Open((Join-Path $Store '.lock'), 'OpenOrCreate', 'ReadWrite', 'ReadWrite')
        try {
            $deadline = (Get-Date).AddMinutes(5)
            while ($true) {
                try {
                    $lock.Lock(0, 1)
                    break
                } catch [System.IO.IOException] {
                    if ((Get-Date) -gt $deadline) {
                        throw 'The accounts stayed locked for 5 minutes, the Chrome host keeps its old code.'
                    }
                    Start-Sleep -Milliseconds 200
                }
            }
            $running | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        } finally {
            $lock.Close()
        }
        Write-Output 'restarted the Chrome host, the extension starts the new one'
    }
}
if (@($script:Changed | Where-Object { $_ -like "$Share\chrome-extension\*" }).Count -gt 0) {
    Write-Output ('Chrome loads the new extension when it starts again. To load it now: chrome://extensions, ' +
        'Reload on Claude account switch.')
}
if ($script:Changed.Contains((Join-Path $Share 'completion.ps1'))) {
    Write-Output "Tab completes claude-switch in new PowerShell windows. In an open one: . `"$Share\completion.ps1`""
}
if ($script:Changed.Count -eq 0) {
    Write-Output "Nothing to install, everything matches $Root."
}
