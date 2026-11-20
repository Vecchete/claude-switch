# Tab completion of claude-switch in PowerShell: its commands, then the accounts in ~\.claude-accounts by name or
# email, and the gateway keys where a command takes one. install.ps1 loads it from the PowerShell profiles, Windows
# PowerShell 5.1 and PowerShell 7 alike. It reads the account files and never runs claude-switch, so a Tab changes
# nothing. Where it has nothing to offer, after list or a name for instance, PowerShell completes file names as it does
# for any program.

Register-ArgumentCompleter -Native -CommandName 'claude-switch', 'claude-switch.cmd' -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)

    # The words before the one under the cursor: the command, then its arguments.
    $before = @($commandAst.CommandElements | Where-Object { $_.Extent.EndOffset -lt $cursorPosition })
    # A word typed with an opening quote comes with its closing one too. It is matched without them, and quoted again
    # when it needs it.
    $typed = "$wordToComplete"
    if ($typed -match "^(['`"])") {
        $quote = $Matches[1]
        $typed = $typed.Substring(1)
        if ($typed.EndsWith($quote)) {
            $typed = $typed.Substring(0, $typed.Length - 1)
        }
    }

    # The profile folder as claude-switch finds it: USERPROFILE on Windows, HOME elsewhere.
    $store = Join-Path $(if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }) '.claude-accounts'
    # Each name and email of the accounts: of all of them, of the claude.ai ones (logins), or the names of the gateway
    # keys (keys). A property is looked for before it is read, so a profile with Set-StrictMode completes them too.
    $accounts = {
        param([string]$Kind = 'all')
        Get-ChildItem -LiteralPath $store -Filter '*.json' -File -ErrorAction SilentlyContinue |
            ForEach-Object {
                try {
                    $account = Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
                    $properties = $account.PSObject.Properties
                    $isKey = $properties['kind'] -and $account.kind -eq 'key'
                    if (($Kind -eq 'keys' -and -not $isKey) -or ($Kind -eq 'logins' -and $isKey)) {
                        return
                    }
                    $account.name
                    if (-not $isKey) {
                        $account.oauthAccount.emailAddress
                    }
                } catch {
                }
            } |
            Where-Object { $_ -is [string] -and $_ }
    }

    $candidates = @()
    if ($before.Count -eq 1) {
        if ($typed.StartsWith('-')) {
            $candidates = '-h', '--help'
        } else {
            $candidates = @('list', 'login', 'key', 'rename', 'refresh', 'help') + @(& $accounts)
        }
    } elseif ($before.Count -eq 2) {
        $command = $before[1].Extent.Text
        if ($command -eq 'login') {
            $candidates = @(& $accounts 'logins')
        } elseif ($command -eq 'rename') {
            $candidates = @(& $accounts)
        } elseif ($command -eq 'key') {
            $candidates = 'import', 'add', 'renew', 'remove'
        } elseif ($command -eq 'refresh') {
            $candidates = '--force'
        }
    } elseif ($before[1].Extent.Text -eq 'key') {
        # The key to renew or remove, and the key a new one is like.
        if ($before.Count -eq 3) {
            if ($before[2].Extent.Text -eq 'renew' -or $before[2].Extent.Text -eq 'remove') {
                $candidates = @(& $accounts 'keys')
            }
        } elseif ($before[-1].Extent.Text -eq '--like') {
            $candidates = @(& $accounts 'keys')
        }
    }

    $pattern = [System.Management.Automation.WildcardPattern]::Escape($typed) + '*'
    $candidates | Where-Object { $_ -like $pattern } | Select-Object -Unique | ForEach-Object {
        # As it is when nothing in it means something to PowerShell or cmd, else in single quotes, as claude-switch
        # quotes the names in its hints.
        if ($_ -match '^[\w.:/=+-][\w@.:/=+-]*$') {
            $text = $_
        } else {
            $text = "'" + ($_ -replace "'", "''") + "'"
        }
        [System.Management.Automation.CompletionResult]::new($text, $_, 'ParameterValue', $_)
    }
}
