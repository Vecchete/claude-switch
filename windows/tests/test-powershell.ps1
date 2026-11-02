# Checks of the PowerShell files of windows/: they parse, they are ASCII, the extension id install.ps1 computes, and
# the Tab completion through TabExpansion2 on a scratch profile. They run in Windows PowerShell 5.1 and PowerShell 7,
# on Windows and on Linux, and never touch the real ~\.claude-accounts.
# Usage: powershell -ExecutionPolicy Bypass -File windows\tests\test-powershell.ps1, or pwsh -File the same.
param([string]$Repo = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)))

$ErrorActionPreference = 'Stop'
$script:passes = 0
$script:failures = @()

function Check([string]$Name, [bool]$Condition) {
    if ($Condition) {
        $script:passes++
    } else {
        $script:failures += $Name
        Write-Output "FAIL $Name"
    }
}

foreach ($file in 'install.ps1', 'completion.ps1', 'notify.ps1') {
    $tokens = $null
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $Repo "windows/$file"), [ref]$tokens,
        [ref]$parseErrors) | Out-Null
    Check "$file parses" ($parseErrors.Count -eq 0)
    foreach ($parseError in $parseErrors) {
        Write-Output "  line $($parseError.Extent.StartLineNumber): $($parseError.Message)"
    }
}

# Windows PowerShell 5.1 reads a script without a BOM in the ANSI code page, and cmd.exe reads a batch file in the
# OEM one: only ASCII reads the same in all of them.
foreach ($file in 'install.ps1', 'completion.ps1', 'notify.ps1', 'claude-switch.cmd', 'chrome-host.cmd') {
    $bytes = [System.IO.File]::ReadAllBytes((Join-Path $Repo "windows/$file"))
    Check "$file is ASCII" (-not ($bytes | Where-Object { $_ -gt 127 }))
}

# The extension id that install.ps1 writes in the host manifest, from its own function.
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $Repo 'windows/install.ps1'), [ref]$null,
    [ref]$null)
$function = $ast.Find({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-ExtensionId'
    }, $true)
$Root = $Repo
. ([scriptblock]::Create($function.Extent.Text))
Check 'extension id: the one Chrome gives the extension' ((Get-ExtensionId) -eq 'klclebpiaondgkoelekdoimhcboaenal')

# The completion, on the accounts of a scratch profile.
$scratch = Join-Path ([System.IO.Path]::GetTempPath()) ("claude-switch-completion." + [System.IO.Path]::GetRandomFileName())
$store = Join-Path $scratch '.claude-accounts'
New-Item -ItemType Directory -Force -Path $store | Out-Null
$userProfile = $env:USERPROFILE
$here = Get-Location
try {
    $env:USERPROFILE = $scratch
    Set-Location -LiteralPath $scratch
    function Add-Account([string]$File, [string]$Name, [string]$Email) {
        $json = @{ name = $Name; login = @{}; oauthAccount = @{ emailAddress = $Email } } | ConvertTo-Json -Depth 5
        [System.IO.File]::WriteAllText((Join-Path $store $File), $json, (New-Object System.Text.UTF8Encoding $false))
    }
    Add-Account 'lucas@example.com_aaaaaaaa.json' '' 'lucas@example.com'
    Add-Account 'lucas@example.org_bbbbbbbb.json' 'work' 'lucas@example.org'
    Add-Account 'other@example.net_cccccccc.json' 'my laptop' 'other@example.net'
    Add-Account 'x@example.com_dddddddd.json' "it's `$(New-Item -ItemType File PWNED)" 'x@example.com'
    Add-Account 'y@example.com_eeeeeeee.json' 'a,b' 'y@example.com'
    Set-Content -LiteralPath (Join-Path $store 'broken.json') -Value '{not json'

    . (Join-Path $Repo 'windows/completion.ps1')

    function Complete([string]$Line) {
        @((TabExpansion2 -inputScript $Line -cursorColumn $Line.Length).CompletionMatches | ForEach-Object { $_.CompletionText })
    }
    function Offers-Ours([string[]]$Completions) {
        [bool]($Completions | Where-Object { $_ -in 'list', 'login', 'work', 'lucas@example.com', '--force' })
    }

    $first = Complete 'claude-switch '
    Check 'first word: the commands' (($first -contains 'list') -and ($first -contains 'login') -and
        ($first -contains 'rename') -and ($first -contains 'refresh') -and ($first -contains 'help'))
    Check 'first word: the accounts by email' ($first -contains 'lucas@example.com')
    Check 'first word: the accounts by name' ($first -contains 'work')
    Check 'first word: no empty name' (-not ($first -contains "''"))
    $lu = Complete 'claude-switch lu'
    Check 'a prefix: only what matches' (($lu -contains 'lucas@example.com') -and ($lu -contains 'lucas@example.org') -and
        $lu.Count -eq 2)
    Check 'a prefix: the commands too' ((Complete 'claude-switch li') -contains 'list')
    Check 'options' (((Complete 'claude-switch -') -join ',') -eq '-h,--help')
    Check 'login: the accounts' (((Complete 'claude-switch login wo') -join ',') -eq 'work')
    Check 'rename: the accounts' ((Complete 'claude-switch rename ') -contains 'other@example.net')
    Check 'refresh: --force' (((Complete 'claude-switch refresh ') -join ',') -eq '--force')
    # Past what it completes, PowerShell offers file names, never a command or an account.
    Check 'nothing of ours after list' (-not (Offers-Ours (Complete 'claude-switch list ')))
    Check 'nothing of ours after a name' (-not (Offers-Ours (Complete 'claude-switch work ')))
    Check 'a name with a space: quoted' (((Complete 'claude-switch my') -join ',') -eq "'my laptop'")
    Check 'a quoted word: matched without its quotes' (((Complete "claude-switch 'my l") -join ',') -eq "'my laptop'")
    Check 'a name with a quote and a $: quoted, never run' (
        ((Complete 'claude-switch it') -join ',') -eq "'it''s `$(New-Item -ItemType File PWNED)'" -and
        -not (Test-Path -LiteralPath (Join-Path $scratch 'PWNED')))
    Check 'a name with a comma: quoted' (((Complete 'claude-switch a') -join ' ') -eq "'a,b'")
    Check 'claude-switch.cmd: the same' ((Complete 'claude-switch.cmd lu').Count -eq 2)
    Check 'the case of the typed word: ignored' ((Complete 'claude-switch LU').Count -eq 2)
} finally {
    $env:USERPROFILE = $userProfile
    Set-Location -LiteralPath $here
    Remove-Item -LiteralPath $scratch -Recurse -Force
}

Write-Output "$($script:passes) passed, $($script:failures.Count) failed"
exit ([int]($script:failures.Count -gt 0))
