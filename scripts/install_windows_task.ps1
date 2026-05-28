param(
    [string]$TaskName = "MysteryWeeklyReport",
    [ValidateSet("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")]
    [string]$DayOfWeek = "Friday",
    [string]$At = "18:00",
    [ValidateSet("run", "publish", "run-and-publish")]
    [string]$Mode = "run-and-publish",
    [switch]$GitPush,
    [string]$PythonPath = "python"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RunnerPath = Join-Path $ProjectRoot "scheduled_runner.py"
if (-not (Test-Path -LiteralPath $RunnerPath)) {
    throw "scheduled_runner.py not found: $RunnerPath"
}

$Arguments = @(
    "`"$RunnerPath`"",
    "--mode",
    $Mode
)
if ($GitPush) {
    $Arguments += "--git-push"
}

$Action = New-ScheduledTaskAction `
    -Execute $PythonPath `
    -Argument ($Arguments -join " ") `
    -WorkingDirectory $ProjectRoot

$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $DayOfWeek -At $At
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel LeastPrivilege
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "Run local weekly mystery report task from scheduled_runner.py" `
    -Force | Out-Null

Write-Host "Registered scheduled task: $TaskName"
Write-Host "Project root: $ProjectRoot"
Write-Host "Mode: $Mode"
Write-Host "Schedule: $DayOfWeek $At"
Write-Host "Git push: $GitPush"
