param(
    [string]$ProjectPath = $PSScriptRoot
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $ProjectPath)) {
    throw "Project path not found: $ProjectPath"
}

$venvPython = Join-Path $ProjectPath ".venv\Scripts\python.exe"
$pythonCmd = if (Test-Path $venvPython) { "`"$venvPython`"" } else { "python" }

Write-Host "Starting Celery cluster from: $ProjectPath"
Write-Host "Python command: $pythonCmd"

$publishWorker = "$pythonCmd -m celery -A social_automation worker -l INFO -Q publish --pool=threads -c 4 -n publish@%h"
$insightsWorker = "$pythonCmd -m celery -A social_automation worker -l INFO -Q insights --pool=threads -c 4 -n insights@%h"
$beat = "$pythonCmd -m celery -A social_automation beat -l INFO"

Start-Process powershell -WorkingDirectory $ProjectPath -ArgumentList @("-NoExit", "-Command", $publishWorker) | Out-Null
Start-Sleep -Milliseconds 400
Start-Process powershell -WorkingDirectory $ProjectPath -ArgumentList @("-NoExit", "-Command", $insightsWorker) | Out-Null
Start-Sleep -Milliseconds 400
Start-Process powershell -WorkingDirectory $ProjectPath -ArgumentList @("-NoExit", "-Command", $beat) | Out-Null

Write-Host "Celery publish worker, insights worker, and beat started in separate PowerShell windows."
Write-Host "Run this to verify: python -m celery -A social_automation inspect ping"
