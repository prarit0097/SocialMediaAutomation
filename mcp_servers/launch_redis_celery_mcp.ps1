$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\\Scripts\\python.exe"

if (-not (Test-Path $python)) {
  throw "Python virtual environment not found at $python"
}

Set-Location $repoRoot
& $python -m mcp_servers.redis_celery_server
