<#
.SYNOPSIS
  Register (or remove) the hourly DROMIC update task in Windows Task Scheduler.

.DESCRIPTION
  Runs `python -m pipeline.update_dromic --repo <Repo>` from etl/ every hour.
  Each run polls the DROMIC RSS feed and only parses, maps and loads when new
  report files appeared (see notes.md 8.16). Output goes to logs/update_dromic.log
  and one row per run to logs/update_runs.csv.

  The task runs as the current user, only while logged on (no stored password).
  Missed runs (laptop asleep/off) start as soon as possible afterwards.
  GraphDB must be running for the load stage; if it is not, the run is recorded
  as failed and the next run retries automatically.

.EXAMPLE
  .\scripts\schedule_dromic.ps1                       # register, repo SakunaGraPH-dev
  .\scripts\schedule_dromic.ps1 -Repo SakunaGraPH-dev -IntervalMinutes 60
  .\scripts\schedule_dromic.ps1 -Unregister
  Start-ScheduledTask -TaskName SakunaGraPH-DROMIC-Update   # run once now
  Get-ScheduledTaskInfo -TaskName SakunaGraPH-DROMIC-Update # last result
#>
param(
    [string]$Repo = "SakunaGraPH-dev",
    [int]$IntervalMinutes = 60,
    [string]$Python = (Get-Command python -ErrorAction Stop).Source,
    [string]$TaskName = "SakunaGraPH-DROMIC-Update",
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Removed task $TaskName"
    return
}

$etlDir = Join-Path (Split-Path -Parent $PSScriptRoot) "etl"
if (-not (Test-Path (Join-Path $etlDir "pipeline\update_dromic.py"))) {
    throw "Cannot find etl\pipeline\update_dromic.py under $etlDir"
}

$action = New-ScheduledTaskAction -Execute $Python `
    -Argument "-m pipeline.update_dromic --repo $Repo" `
    -WorkingDirectory $etlDir

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "Polls DROMIC RSS and updates GraphDB repository $Repo (SakunaGraPH)" -Force | Out-Null

Write-Output "Registered $TaskName : every $IntervalMinutes min, repo $Repo"
Write-Output "  python : $Python"
Write-Output "  cwd    : $etlDir"
