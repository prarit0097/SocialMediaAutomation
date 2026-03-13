$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$outputDir = Join-Path $repoRoot "mcp_outputs\\playwright"
$npx = (Get-Command npx.cmd -ErrorAction Stop).Source

New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
Set-Location $repoRoot
& $npx -y @playwright/mcp@0.0.68 --headless --isolated --browser msedge --output-dir $outputDir
