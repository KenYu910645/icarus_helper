$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskExe = Join-Path $PSScriptRoot 'dist\objective-counters-20261105\ICARUS Helper\ICARUS Helper.exe'
if (Test-Path -LiteralPath $taskExe) {
    Start-Process -FilePath $taskExe -WorkingDirectory $PSScriptRoot -WindowStyle Normal
} else {
    $taskPython = (Get-Command pythonw.exe -ErrorAction Stop).Source
    Start-Process -FilePath $taskPython -ArgumentList @('main.py') -WorkingDirectory $PSScriptRoot -WindowStyle Normal
}
