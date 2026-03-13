$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$filesystemLauncher = Join-Path $repoRoot "mcp_servers\\launch_filesystem_mcp.ps1"
$playwrightLauncher = Join-Path $repoRoot "mcp_servers\\launch_playwright_mcp.ps1"
$redisCeleryLauncher = Join-Path $repoRoot "mcp_servers\\launch_redis_celery_mcp.ps1"
$metaInsightsLauncher = Join-Path $repoRoot "mcp_servers\\launch_meta_insights_mcp.ps1"

$codex = (Get-Command codex -ErrorAction Stop).Source
$pwsh = (Get-Command powershell -ErrorAction Stop).Source

$servers = @(
  @{ Name = "social-filesystem"; Launcher = $filesystemLauncher },
  @{ Name = "social-playwright"; Launcher = $playwrightLauncher },
  @{ Name = "social-redis-celery"; Launcher = $redisCeleryLauncher },
  @{ Name = "social-meta-insights"; Launcher = $metaInsightsLauncher }
)

foreach ($server in $servers) {
  & $codex mcp remove $server.Name 2>$null | Out-Null
}

foreach ($server in $servers) {
  & $codex mcp add $server.Name -- $pwsh -NoProfile -ExecutionPolicy Bypass -File $server.Launcher
}

& $codex mcp list
