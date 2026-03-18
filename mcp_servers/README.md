# Project MCP Servers

This folder stores Codex MCP servers and launch scripts for the `Postzyo` project.

## Included MCPs

### 1. Filesystem MCP
- launcher: `mcp_servers/launch_filesystem_mcp.ps1`
- purpose: read and update project files plus inspect temporary log files from local runs
- runtime: `@modelcontextprotocol/server-filesystem`

### 2. Browser / Playwright MCP
- launcher: `mcp_servers/launch_playwright_mcp.ps1`
- purpose: open and validate Accounts, Scheduler, and Insights pages in a real browser
- runtime: `@playwright/mcp`
- browser mode: headless Edge with output stored under `mcp_outputs/playwright`

### 3. Redis / Celery MCP
- launcher: `mcp_servers/launch_redis_celery_mcp.ps1`
- module: `mcp_servers.redis_celery_server`
- purpose:
  - inspect Redis queue keys
  - inspect live Celery workers and active queues
  - monitor daily heavy insights progress
  - inspect scheduled publishing health and failed jobs

### 4. Meta Insights MCP
- launcher: `mcp_servers/launch_meta_insights_mcp.ps1`
- module: `mcp_servers.meta_insights_server`
- purpose:
  - summarize latest cached snapshots
  - flag stale profiles
  - detect posting gaps
  - build cached FB vs IG comparison rows

## Local setup

Run:

```powershell
powershell -ExecutionPolicy Bypass -File .\mcp_servers\setup_codex_mcp.ps1
```

That script registers these MCPs in the local Codex config:
- `social-filesystem`
- `social-playwright`
- `social-redis-celery`
- `social-meta-insights`

Smoke-test the launchers with:

```powershell
powershell -ExecutionPolicy Bypass -File .\mcp_servers\smoke_test_mcp.ps1
```

## Notes

- The custom MCP servers rely on the repo virtual environment at `.venv`.
- The Meta Insights MCP intentionally uses cached database snapshots, not live Meta API calls.
- The Redis / Celery MCP expects Redis and Celery workers to be reachable through the current project settings.
