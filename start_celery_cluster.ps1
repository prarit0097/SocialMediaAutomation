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

$workerA = "$pythonCmd -m celery -A social_automation worker -l INFO --pool=threads -c 12 -n worker_a@%h"
$workerB = "$pythonCmd -m celery -A social_automation worker -l INFO --pool=threads -c 8 -n worker_b@%h"
$beat = "$pythonCmd -m celery -A social_automation beat -l INFO"

Start-Process powershell -WorkingDirectory $ProjectPath -ArgumentList @("-NoExit", "-Command", $workerA) | Out-Null
Start-Sleep -Milliseconds 400
Start-Process powershell -WorkingDirectory $ProjectPath -ArgumentList @("-NoExit", "-Command", $workerB) | Out-Null
Start-Sleep -Milliseconds 400
Start-Process powershell -WorkingDirectory $ProjectPath -ArgumentList @("-NoExit", "-Command", $beat) | Out-Null

Write-Host "Celery worker_a, worker_b, and beat started in separate PowerShell windows."
Write-Host "Run this to verify: python -m celery -A social_automation inspect ping"
