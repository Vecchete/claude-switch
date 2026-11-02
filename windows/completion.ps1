# Tab completion of claude-switch in PowerShell: its commands, then the accounts in ~\.claude-accounts by name or
# email. install.ps1 loads it from the PowerShell profiles, Windows PowerShell 5.1 and PowerShell 7 alike. It reads
# the account files and never runs claude-switch, so a Tab changes nothing. Where it has nothing to offer, after list
# or a name for instance, PowerShell completes file names as it does for any program.

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
    $accounts = {
        Get-ChildItem -LiteralPath $store -Filter '*.json' -File -ErrorAction SilentlyContinue |
            ForEach-Object {
                try {
                    $account = Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
                    $account.name
                    $account.oauthAccount.emailAddress
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
            $candidates = @('list', 'login', 'rename', 'refresh', 'help') + @(& $accounts)
        }
    } elseif ($before.Count -eq 2) {
        $command = $before[1].Extent.Text
        if ($command -eq 'login' -or $command -eq 'rename') {
            $candidates = @(& $accounts)
        } elseif ($command -eq 'refresh') {
            $candidates = '--force'
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
