$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$pwsh = (Get-Command powershell -ErrorAction Stop).Source
$servers = @(
  @{ Name = "filesystem"; Path = (Join-Path $repoRoot "mcp_servers\\launch_filesystem_mcp.ps1"); WaitSeconds = 8 },
  @{ Name = "playwright"; Path = (Join-Path $repoRoot "mcp_servers\\launch_playwright_mcp.ps1"); WaitSeconds = 12 },
  @{ Name = "redis_celery"; Path = (Join-Path $repoRoot "mcp_servers\\launch_redis_celery_mcp.ps1"); WaitSeconds = 8 },
  @{ Name = "meta_insights"; Path = (Join-Path $repoRoot "mcp_servers\\launch_meta_insights_mcp.ps1"); WaitSeconds = 8 }
)

$results = @()
foreach ($server in $servers) {
  $startInfo = New-Object System.Diagnostics.ProcessStartInfo
  $startInfo.FileName = $pwsh
  $quotedPath = '"' + $server.Path + '"'
  $startInfo.Arguments = "-NoProfile -ExecutionPolicy Bypass -File $quotedPath"
  $startInfo.UseShellExecute = $false
  $startInfo.RedirectStandardInput = $true
  $startInfo.RedirectStandardOutput = $true
  $startInfo.RedirectStandardError = $true

  $process = New-Object System.Diagnostics.Process
  $process.StartInfo = $startInfo
  $null = $process.Start()
  Start-Sleep -Seconds $server.WaitSeconds

  if ($process.HasExited) {
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $results += [pscustomobject]@{
      name = $server.Name
      ok = $false
      exit_code = $process.ExitCode
      stdout = $stdout
      stderr = $stderr
    }
    continue
  }

  $process.Kill()
  $process.WaitForExit()
  $results += [pscustomobject]@{
    name = $server.Name
    ok = $true
    exit_code = $null
    stdout = ""
    stderr = ""
  }
}

$results | ConvertTo-Json -Depth 4
if ($results.Where({ -not $_.ok }).Count -gt 0) {
  exit 1
}
