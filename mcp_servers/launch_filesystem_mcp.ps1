$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$tempRoot = [System.IO.Path]::GetTempPath()
$npx = (Get-Command npx.cmd -ErrorAction Stop).Source

Set-Location $repoRoot
& $npx -y @modelcontextprotocol/server-filesystem@2026.1.14 $repoRoot $tempRoot
