# Runs the Windows suites of the port on the PC: the smoke tests, the gateway keys, automatic switching, the Desktop and
# Chrome branches, and the PowerShell checks, in Windows PowerShell and in PowerShell 7 when it is installed. They use
# scratch profiles, stand-in processes and a pipe of their own, and never touch the real accounts, Chrome or Claude
# Desktop.
# windows\tests\mutants.py then says whether they still catch every guard.
# Usage: powershell -ExecutionPolicy Bypass -File windows\tests\run.ps1
$ErrorActionPreference = 'Continue'
$env:PYTHONDONTWRITEBYTECODE = '1'
$script:passed = @()
$script:failed = @()

function Invoke-Suite([string]$Name, [scriptblock]$Command) {
    Write-Output "== $Name"
    & $Command
    if ($LASTEXITCODE -eq 0) {
        $script:passed += $Name
    } else {
        $script:failed += $Name
    }
}

Invoke-Suite 'switch' { py -3 -I (Join-Path $PSScriptRoot 'test-switch.py') }
Invoke-Suite 'keys' { py -3 -I (Join-Path $PSScriptRoot 'test-keys.py') }
Invoke-Suite 'auto' { py -3 -I (Join-Path $PSScriptRoot 'test-auto.py') }
Invoke-Suite 'desktop' { py -3 -I (Join-Path $PSScriptRoot 'test-desktop.py') }
Invoke-Suite 'chrome' { py -3 -I (Join-Path $PSScriptRoot 'test-chrome.py') }
Invoke-Suite 'powershell' {
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'test-powershell.ps1')
}
if (Get-Command 'pwsh' -ErrorAction SilentlyContinue) {
    Invoke-Suite 'powershell 7' { pwsh -NoProfile -File (Join-Path $PSScriptRoot 'test-powershell.ps1') }
}

Write-Output ''
Write-Output ('Passed: ' + $(if ($script:passed.Count -gt 0) { $script:passed -join ', ' } else { 'none' }))
if ($script:failed.Count -gt 0) {
    Write-Output ('Failed: ' + ($script:failed -join ', '))
    exit 1
}
exit 0
