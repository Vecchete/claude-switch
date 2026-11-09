<#
.SYNOPSIS
Shows a Windows notification for claude-switch, the notify-send of the Linux version.

.DESCRIPTION
claude-switch runs it with powershell.exe, Windows PowerShell 5.1, which has the WinRT types of the toasts.
PowerShell 7 doesn't have them. It exits 0 once Windows took the notification, 1 otherwise, and claude-switch
counts it as shown only on 0.

The toast shows on the PC (09/11/2026, read back from the notification center under the app id below).
TODO(windows): see one the claude-switch-refresh task shows itself, and whether an app id of its own reads better
than the one of Windows PowerShell used here.
#>
param(
    [Parameter(Mandatory = $true)][string]$Title,
    [Parameter(Mandatory = $true)][string]$Body
)

$ErrorActionPreference = 'Stop'
try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
    $heading = [System.Security.SecurityElement]::Escape($Title)
    $text = [System.Security.SecurityElement]::Escape($Body)
    $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $xml.LoadXml("<toast><visual><binding template=`"ToastGeneric`"><text>$heading</text><text>$text</text></binding></visual></toast>")
    # The app id Windows registers for Windows PowerShell: a toast needs the id of an app the Start menu knows.
    $app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show(
        [Windows.UI.Notifications.ToastNotification]::new($xml))
    exit 0
} catch {
    exit 1
}
