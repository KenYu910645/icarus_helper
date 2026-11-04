param(
    [Parameter(Mandatory=$true)][string]$ExecutablePath,
    [Parameter(Mandatory=$true)][string]$SourcePath,
    [string]$StateDirectory = (Join-Path $env:USERPROFILE '.icarus-helper\world-publisher')
)
$ErrorActionPreference = 'Stop'
$taskExe = [IO.Path]::GetFullPath($ExecutablePath)
$taskSource = [IO.Path]::GetFullPath($SourcePath)
$taskState = [IO.Path]::GetFullPath($StateDirectory)
if (-not (Test-Path -LiteralPath $taskExe -PathType Leaf)) { throw 'The Helper application was not found' }
if (-not (Test-Path -LiteralPath $taskSource -PathType Leaf)) { throw 'The explicitly selected world source was not found' }
if ($taskExe.Contains('"') -or $taskSource.Contains('"') -or $taskState.Contains('"')) { throw 'A path contains unsupported quotation marks' }
$taskName = 'IcarusHelperWorldShare'
$taskIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskArguments = '--world-publisher --save "' + $taskSource + '" --publisher-state "' + $taskState + '" --publisher-keep-versions 3'
$taskAction = New-ScheduledTaskAction -Execute $taskExe -Argument $taskArguments -WorkingDirectory (Split-Path -Parent $taskExe)
$taskTriggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User $taskIdentity),
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1))
)
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $taskIdentity -LogonType Interactive -RunLevel Limited
$taskSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$taskDefinition = New-ScheduledTask -Action $taskAction -Trigger $taskTriggers -Principal $taskPrincipal -Settings $taskSettings -Description 'Publish the explicitly approved complete GuoGuoGaGa world save to GitHub Releases without modifying the game or local backup history.'
Register-ScheduledTask -TaskName $taskName -InputObject $taskDefinition -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output 'World sharing enabled: starts at sign-in with a recovery start every minute; continuously monitors the explicitly selected source.'
