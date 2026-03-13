# Social Media Automation Project Guide

## Project Purpose
This project is an internal Django-based web app for managing Meta assets from one workspace. It connects Facebook Pages and linked Instagram Business accounts, schedules posts, publishes them automatically, and stores insights snapshots for reporting and future analytics.

Anyone reading this file should be able to understand what the app does without reading the full codebase.

## Core Use Cases
- Connect Facebook Pages and linked Instagram accounts through Meta OAuth.
- Store connected account details and page access tokens securely.
- Schedule Facebook posts, Instagram posts, or combined FB + IG publishing.
- Publish due posts automatically through Celery workers.
- View account-level insights and recent published post performance.
- Refresh insights on demand from the UI.
- Run a daily heavy insights collection job so cached analytics stay updated.

## Main User Areas

### 1. Accounts
The Accounts page is used to inspect connected assets and confirm whether pages and IG profiles are available inside the app.

What it shows:
- merged FB / IG profile view where possible
- account IDs used by scheduling and insights
- Facebook Page ID and Instagram user ID
- connected timestamp
- latest detected posting time (`last_post_at`)
- stale posting indicator if no recent post was detected in the last 24 hours

What it does:
- starts Meta connect flow
- refreshes connected account list
- shows sync status
- shows Meta page catalog / connectability state

### 2. Scheduler
The Scheduler page creates scheduled publishing jobs.

Supported publishing modes:
- Facebook only
- Instagram only
- Both Facebook + Instagram together

What happens:
- user enters account, platform, content, media, and schedule time
- post is stored in UTC internally
- Celery beat checks every minute for due posts
- Celery worker publishes due jobs to Meta Graph
- failed jobs can be retried

### 3. Insights
The Insights page is used to inspect performance for a connected account.

What it shows:
- total followers
- total following
- total post share
- published posts table
- FB vs IG comparison table
- warnings/errors for partial or upstream failures

It supports:
- fetch cached/latest snapshot
- force refresh from Meta
- combined FB + IG view for linked assets
- recent post sorting by latest publish time

## Background Automation

### Scheduled Publishing Automation
- task: `publishing.tasks.process_due_posts`
- frequency: every minute
- purpose: move due scheduled posts into publishing tasks

### Daily Heavy Insights Automation
- task: `analytics.tasks.queue_daily_heavy_insight_refresh`
- default schedule: every day at `05:00 AM`
- timezone source: `CELERY_TIMEZONE` (currently intended for `Asia/Kolkata`)
- purpose: fetch the heaviest practical insights snapshot for every connected profile and store it for UI + future analytics use

Heavy insights collection currently stores:
- account-level insights returned by Meta
- published posts list
- post-level stats for a configured subset of recent posts
- snapshot metadata describing collection mode and collection date

This is important because future AI analysis can use these stored snapshots instead of depending only on live API calls.

## Main Data Stored

### Connected Accounts
Model: `integrations.ConnectedAccount`

Stores:
- platform
- page ID
- page name
- Instagram linked user ID if available
- encrypted page access token
- token expiry if available
- created and updated timestamps

### Scheduled Posts
Model: `publishing.ScheduledPost`

Stores:
- target account
- platform
- message
- media URL
- scheduled time
- status
- external Meta post ID
- publish time
- error details

### Insight Snapshots
Model: `analytics.InsightSnapshot`

Stores:
- target account
- platform
- raw insight payload
- published posts embedded in payload
- optional metadata for collection mode
- snapshot fetch time

## How Data Flows

### Connect Flow
1. User starts Meta OAuth.
2. Meta returns a code.
3. App exchanges code for token.
4. App fetches managed pages.
5. App creates or updates connected FB and IG accounts.

### Publish Flow
1. User creates scheduled post.
2. Post is saved with `pending` status.
3. Beat queues due posts.
4. Worker publishes to Meta.
5. Post becomes `published` or `failed`.

### Insights Flow
1. User opens insights for an account.
2. App returns latest stored snapshot if available.
3. On force refresh, app fetches fresh Meta data.
4. App stores a new snapshot.
5. UI renders summary cards, published posts, and comparison table.

## Current Technical Stack
- Django
- Django templates + JavaScript frontend
- PostgreSQL or SQLite for storage
- Redis
- Celery worker
- Celery beat
- Meta Graph API
- Docker Compose for deployment setup
- Codex MCP servers for local operations and future agent tooling

## Codex MCP Tooling
This project now includes four local MCP servers under `mcp_servers/` so Codex or future agents can inspect and operate the workspace more directly.

### Filesystem MCP
- reads and updates project files
- can inspect temporary local log files used during local runs

### Browser / Playwright MCP
- opens the dashboard in a real browser
- validates Accounts, Scheduler, and Insights flows

### Redis / Celery MCP
- checks Redis queue keys and queue sizes
- checks Celery workers and active queues
- reports daily heavy insights automation progress
- reports scheduled publishing pipeline health and failed jobs

### Meta Insights MCP
- summarizes latest cached snapshots
- flags stale profiles
- detects posting gaps
- builds cached FB vs IG comparison data from stored snapshots

## Current Operational Assumptions
- Meta app permissions must be valid.
- Instagram publishing requires public media URLs.
- Public HTTPS base URL is required for Meta-facing media/callback workflows.
- Celery worker and beat must be running for scheduled publishing and daily heavy insights automation.

## What This Project Is Preparing For
This project is not only a scheduler and dashboard. It is also becoming a data collection layer for future analytics tooling.

Planned/expected future direction:
- AI agent reads stored insight snapshots
- AI agent analyzes trends across FB and IG
- AI agent suggests content and posting improvements
- UI can rely more on stored snapshots and less on expensive live pulls
- MCP-based tools can give agents direct structured access to cached analytics, queue health, and browser validation workflows

## Rule For Maintenance
This file must be updated whenever project behavior, workflow, automation, stored data, or important UI meaning changes.

If someone asks, “What does this project do?”, this file should be the first source of truth.
