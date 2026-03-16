# Social Media Automation Project Guide

## Project Purpose
Social Media Automation is an internal Django-based Meta operations app. It connects Facebook Pages and linked Instagram Business accounts, schedules and publishes posts, stores insights snapshots, and keeps enough historical data for future analytics and AI-driven recommendations.

This file is the high-level source of truth for what the project does, how the main workflows behave, and what operational rules currently apply.

## Core Outcomes
- Connect and refresh Facebook Pages and linked Instagram accounts through Meta OAuth.
- Store connected account metadata and encrypted page access tokens.
- Configure `META_APP_ID`, `META_APP_SECRET`, and `META_REDIRECT_URI` from Dashboard Home and persist them in `.env` without code edits.
- Schedule Facebook, Instagram, or combined FB + IG posts.
- Publish due posts automatically through Celery workers.
- Store cached insights snapshots for operator review and future analytics.
- Run a daily heavy insights collection job so dashboards rely on stored data instead of repeated live API pulls.
- Generate profile-wise AI insights (pros/cons/risks/action plan) from stored snapshots.
- Expose project-aware MCP servers for file, browser, queue, and analytics operations.
- Include concurrency-hardening controls (locks, queue shaping, retry/backoff) for high parallel multi-user usage.

## Main User Areas

### Landing Page
The Landing page is the first screen shown at app start (`/`) for non-authenticated users.

What it shows:
- high-level overview of all core modules (Accounts, Scheduler, Insights, AI Insights)
- visual feature cards with user-friendly illustrations
- clear `Login` and `Signup` actions
- simple step flow explaining setup to analytics journey
- redesigned premium marketing layout with: hero, capability bar, feature matrix, workflow steps, analytics highlight, AI value band, pricing cards, FAQ, and final CTA

What it does:
- helps first-time users understand app value before authentication
- routes authenticated users directly to Dashboard Home to continue operations
- uses project media logos across UI touchpoints: Meta logo in global brand/top identity and Meta/Instagram logos in platform badges and Meta-connect CTA where context is platform-specific

### Home
The Home page is now the workspace setup + navigation surface.

What it shows:
- Meta App Configuration form for `META_APP_ID`, `META_APP_SECRET`, and `META_REDIRECT_URI`
- masked secret state so operators can confirm whether a secret is already configured
- expanded child-friendly setup guide section (collapsible) with copy/paste mapping, checkpoints, troubleshooting, required scopes, and connect/verify steps
- Part 1, 2, 3 are now extra-detailed with click-by-click beginner instructions (login, create app, copy/paste mapping, save + verify flow)
- Part 4 in setup guide now explains where to enable scopes and what each scope does in simple language
- beginner-friendly setup checklist covering: use-case selection, customize use-case, API/Login setup, required scopes, and FB+IG asset linking
- quick actions to Accounts, Scheduler, Insights, and AI Insights

What it does:
- saves Meta app credentials directly into project `.env`
- applies updated values to runtime settings immediately for next Meta OAuth/connect actions
- warns if redirect URI pattern looks unusual (for example missing `/auth/meta/callback`)

### Accounts
The Accounts page is the operator view for connected Meta assets.

What it shows:
- merged FB / IG profile rows where a link exists
- merged profile label shows both names (`FB page + IG profile`) to avoid hidden-page confusion
- account IDs used by scheduler and insights
- Facebook Page ID and Instagram user ID
- connected timestamp
- latest detected posting time (`last_post_at`)
- stale posting indicator when no recent post was detected in the last 24 hours
- stale sync indicator when a stored account row was not refreshed in the latest Meta reconnect
- view meta shows both merged-row count and active raw connected-row count
- per-row quick actions: Schedule, Insights, and AI Insights
- actions UI uses a priority-styled Schedule button with compact Insights/AI Insights secondary buttons
- Connected Accounts table layout is compacted so action buttons stay inside table width without forcing right-side horizontal scrolling in normal desktop view
- one-click `Force Refresh All Profiles` button to queue a full connected-account refresh sweep

What it does:
- starts the Meta connect flow
- refreshes the connected account list
- `Refresh List` now forces fresh reads for both connected accounts list and Meta catalog (`?refresh=1`), so operators see latest reconnect state immediately
- shows current sync status and Meta page catalog data
- can queue force-refresh jobs for all active connected profiles to pull latest Meta insights into snapshots
- force-refresh-all now uses persistent per-user run tracking with live progress (%) and completion state, so the button stays disabled until that user's run finishes (even after page reload or re-login)
- force-refresh-all now asks for operator confirmation before starting, because full refresh can take significant time based on connected FB/IG profile count
- force-refresh run status is now auto-reconciled: if snapshot storage succeeds but callback bookkeeping misses, counters self-heal and stale `running` states are auto-finalized
- Accounts UI shows a one-time toast when a previously stuck force-refresh run is auto-recovered/finalized
- uses user-token fallback for catalog detail checks (session token first, then current user cache, then latest global reconnect token)
- keeps only latest reconnect profiles active in scheduling/health
- blocks scheduling from stale or inactive account rows until the profile is refreshed in a new reconnect
- shows Meta catalog in merged FB_IG rows when linked, and separate rows when unlinked

Important runtime meaning:
- a green Health indicator does not mean every historical stored account row is usable
- scheduling is only allowed for account rows refreshed in the latest reconnect window
- if older connected rows still exist outside the latest reconnect window, Health turns red and asks for reconnect
- profiles not returned in the latest reconnect are marked inactive and excluded from active account lists

### Scheduler
The Scheduler page creates publishing jobs and monitors scheduled, published, and failed rows.

Supported publishing modes:
- Facebook only
- Instagram only
- Both Facebook + Instagram together

What happens:
- user enters account, platform, content, media, and schedule time
- app validates account freshness and rejects stale account rows
- local Instagram image uploads are auto-optimized to a lighter JPG variant for more reliable Meta download
- app preflights public media URLs before Instagram publish attempts
- Instagram/FB+IG scheduling stores optimized IG-safe media URLs at schedule time (not only at publish time)
- post is stored in UTC internally
- Celery beat checks every minute for due posts
- Celery worker publishes due jobs to Meta Graph
- scheduler list API now includes a self-healing dispatcher fallback: if due pending jobs exist (beat miss case), it auto-triggers due processing so queue does not stay stuck
- stale `processing` rows are auto-recovered back to `pending` after safety window and re-queued, reducing long-lived stuck jobs
- Celery uses fair scheduling (`prefetch=1`) and task priority routing so due publishing jobs are not starved by heavy analytics queues
- due publish jobs are enqueued explicitly with higher priority while daily-heavy analytics refresh is queued with lower priority
- publish task is idempotent against delayed duplicate deliveries (already-published rows are skipped safely)
- publish task now uses a per-post execution lock (`publish_task_lock:<post_id>`) so duplicate deliveries do not run in parallel
- failed jobs can be retried if the account row is current
- invalid Meta token failures are stored with reconnect guidance so the operator knows to reconnect before retrying
- Instagram video / reel publishing waits for container processing to finish before final publish
- Meta media download timeouts are treated as transient and automatically retried
- Instagram "media not ready to publish" responses (`code=9007`, `subcode=2207027`) are treated as transient and retried automatically
- Meta Graph application/page rate-limit responses (`code=4`, plus common transient throttle codes) are treated as transient instead of permanent failures
- Instagram media-ready polling now tolerates transient API failures/rate limits with backoff and continues polling instead of immediate hard-fail
- publishing retries now use longer cooldown for Graph rate-limit errors to reduce repeated burst failures during multi-profile scheduling
- failed Instagram retries also re-apply IG media optimization before requeueing
- after a successful schedule action, UI shows an immediate toast notification with the scheduled local date-time
- force-refresh post-stat calls retry transient Meta/network timeouts and backfill missing values from latest cached snapshot when possible

Common failure pattern:
- if a page was not refreshed in the latest reconnect, old stored tokens can still exist in the database
- the app now blocks scheduling and retry on those stale rows instead of allowing a later `190/460` publish failure

### Planning
The Planning page is the first step toward full social media manager operations.

What it shows:
- monthly content calendar view (month switcher + day grid)
- drag/drop movement of planned items between dates
- content status coloring (`draft`, `review`, `approved`, `scheduled`, `published`)
- pillars/tags creation panel
- quick create form for planned content items

What it supports:
- operator-scoped planning data per logged-in user
- API-backed monthly read/write calendar
- API-backed pillar/tag management
- item updates (including drag/drop date movement)

Important runtime meaning:
- planning items are editorial plans, separate from `ScheduledPost` publish queue
- this allows teams to plan first, then schedule approved items into the publishing engine

### Insights
The Insights page is the reporting view for account-level Meta data and recent post performance.

What it shows:
- total followers
- total following
- total post share
- published posts table
- published posts message text trimmed to first 3 lines in-table for readable layout (full text available on hover)
- FB vs IG comparison table
- top-nav Meta token health indicator with green/red blinking status and reconnect guidance
- warnings for partial upstream failures
- post-stats health counters (`Live`, `Cached fallback`, `Missing`) in insight meta line for force-refresh transparency

What it supports:
- fetch cached/latest snapshot
- force refresh from Meta
- combined FB + IG view for linked assets
- newest-first published post sorting by `published_at`
- full-width published posts section with comparison table below it

Important runtime meaning:
- UI primarily uses stored snapshots
- force refresh is optimized to avoid excessively slow page loads
- force refresh now uses a per-account short live lock to avoid simultaneous duplicate Meta pulls for the same profile
- non-force insights responses are short-cached by snapshot identity to reduce repeated payload rebuild cost without serving stale snapshots
- some FB comparison rows use best-available Meta equivalents because exact IG-style metrics do not always exist on Facebook

### AI Insights
The AI Insights page is the profile-wise recommendation layer.

What it shows:
- AI executive summary for selected profile
- strengths (pros) and weaknesses (cons)
- risks and improvement opportunities
- recommended posting strategy (current vs suggested cadence) with mandatory separate FB/IG current cadence using last-7-days posts and avg/day
- 7-day action plan and KPI growth targets
- content ideas aligned to current profile data
- final section: **Best recommendation for grow your profile** (bullet points), tied to profile data and trend-aware execution with serious source references (for example Meta for Creators / Instagram Creators)
- this recommendation section is profile-wise for the selected `account_id` and tuned for realistic, humanized advisor tone instead of generic robotic output
- each recommendation bullet in this section explicitly includes the selected profile name for stronger user-context attachment

What it supports:
- account-id based analysis for any connected profile
- optional force-refresh before analysis
- optional operator goal/focus prompt
- OpenAI-backed JSON report generation from latest stored insight snapshot + recent published posts
- stronger prompt framework with strict metric-grounded reasoning, platform-specific diagnosis, and 7-day execution planning
- fallback normalization for posting strategy so output always contains platform-separated FB/IG current posting, recommended cadence, and reasoning

Important runtime meaning:
- AI advice is generated from available snapshot/post data; missing metrics are marked as unavailable
- OpenAI key must be configured in `.env` (`OPENAI_API_KEY`) for AI insights generation

## Background Automation

### Scheduled Publishing Automation
- task: `publishing.tasks.process_due_posts`
- frequency: every minute
- purpose: move due scheduled posts into publish tasks
- priority behavior: publishing tasks run at higher queue priority than heavy analytics refresh tasks

### Daily Heavy Insights Automation
- task: `analytics.tasks.queue_daily_heavy_insight_refresh`
- default schedule: every day at `03:00 AM`
- timezone source: `CELERY_TIMEZONE`
- intended timezone: `Asia/Kolkata`
- purpose: fetch the heaviest practical insights snapshot for every connected profile and store it for UI and future analytics
- each account refresh task uses a per-account lock (`insight_refresh_lock:<account_id>`) so duplicate queued jobs are safely skipped

Heavy insights collection currently stores:
- account-level insights returned by Meta
- published posts list
- post-level stats for a configured subset of recent posts
- snapshot metadata describing collection mode and collection date

This matters because future AI analysis can use stored snapshots instead of depending only on live API calls.

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

Operational meaning:
- `updated_at` reflects when the stored connected account row was last refreshed
- this is used to determine whether a row belongs to the latest reconnect window
- `is_active` tracks whether a stored row is part of the latest usable reconnect set
- encrypted token text itself is not used for DB filtering decisions; active/inactive state is managed with `is_active`

### Meta User OAuth Token
Model: `integrations.MetaUserToken`

Stores:
- per-user Meta OAuth user access token in encrypted form
- created and updated timestamps

Operational meaning:
- catalog detail lookups can recover after cache/server restarts
- token resolution order is session token -> per-user cache -> encrypted DB token -> global cache token
- when catalog API sees a valid session/cache/global token, it auto-persists that token into DB for durability

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

## Workflow Summary

### Connect Flow
1. User starts Meta OAuth.
2. Meta returns a code.
3. App exchanges the code for a token.
4. App fetches managed pages.

## Scale Readiness (1000+ Concurrent Users)
- Use PostgreSQL via `DATABASE_URL` (SQLite is not suitable for high concurrent writes).
- Use Redis for cache (`CACHE_BACKEND=redis`) and Celery broker/result backend.
- Keep multiple web workers + multiple Celery workers (separate worker pool for publishing vs analytics is recommended).
- Tune runtime env knobs:
  - `DB_CONN_MAX_AGE`, `DB_CONN_HEALTH_CHECKS`
  - `CELERY_PUBLISH_RATE_LIMIT`, `CELERY_INSIGHTS_REFRESH_RATE_LIMIT`
  - `CELERY_WORKER_MAX_TASKS_PER_CHILD`
  - `CELERY_TASK_SOFT_TIME_LIMIT`, `CELERY_TASK_TIME_LIMIT`
  - `BULK_REFRESH_STALE_MINUTES`
- Restart web + worker processes after config changes so new concurrency controls apply.

### One-command Celery Startup (Windows)
- Script: `start_celery_cluster.ps1` (project root)
- Purpose: start these 3 processes automatically in separate PowerShell windows:
  - `worker_a` (`--pool=threads -c 12`)
  - `worker_b` (`--pool=threads -c 8`)
  - `beat`
- Usage:
  - from project root: `.\start_celery_cluster.ps1`
  - optional custom path: `.\start_celery_cluster.ps1 -ProjectPath "E:\Social Media Automation"`
5. App creates or updates connected FB and IG account rows.
6. App records the latest reconnect time for stale-account detection.

### Publish Flow
1. User creates a scheduled post.
2. App validates account freshness and platform rules.
3. Post is saved with `pending` status.
4. Beat queues due posts.
5. Worker publishes to Meta.
6. Post becomes `published` or `failed`.

### Insights Flow
1. User opens insights for an account.
2. App returns the latest stored snapshot if available.
3. On force refresh, app fetches fresh Meta data.
4. App stores a new snapshot.
5. UI renders summary cards, published posts, and comparison data.

## Current Technical Stack
- Django
- Django templates with JavaScript frontend
- PostgreSQL or SQLite for storage
- Redis
- Celery worker
- Celery beat
- Meta Graph API
- Docker Compose for deployment setup
- Codex MCP servers for local operations and future agent tooling

## Codex MCP Tooling
This project includes local MCP servers under `mcp_servers/` so Codex or future agents can inspect and operate the workspace more directly.

### Filesystem MCP
- reads and updates project files
- inspects local logs and generated artifacts

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

## Operational Requirements
- Meta app permissions must remain valid.
- Instagram publishing requires public HTTPS media URLs.
- `PUBLIC_BASE_URL` must point to a reachable public HTTPS base.
- Celery worker and Celery beat must be running for scheduled publishing and daily heavy insights automation.
- Celery workers must be restarted after Celery config changes so new prefetch/priority behavior is applied.
- OpenAI credentials (`OPENAI_API_KEY`) must be set for AI Insights report generation.
- reconnecting a subset of pages does not automatically refresh every older stored account row.

## Test Reliability Notes
- full Django test suite currently runs with 91 tests.
- MCP helper tests are optional and auto-skip when the external `mcp` Python package is not installed.
- Instagram local image optimization tests are auto-skip when Pillow (`PIL`) is not installed.
- publishing task tests clear cache in setup to avoid stale lock-key side effects between tests.

## Future Direction
This project is not only a scheduler and dashboard. It is becoming a stored-data layer for future analytics tooling.

Planned direction:
- AI agent reads stored insight snapshots
- AI agent analyzes trends across FB and IG
- AI agent suggests content and posting improvements
- UI relies more on stored snapshots and less on expensive live pulls
- MCP-based tools give agents structured access to cached analytics, queue health, and browser validation workflows

## Step-wise Product Roadmap (In Progress)
This roadmap tracks the exact manager-grade scope requested for turning the app into a complete social media operations suite.

Step 1 (Implemented):
- Content Planning Suite
  - monthly content calendar with drag/drop movement
  - content pillars/tags support

Step 2 (Next):
- Creative Asset Management
  - central media library (folders/search/reuse)
  - brand kits (colors, fonts, logo, disclaimers)
  - caption templates, CTA snippets, hashtag banks
  - duplicate content warning (caption/media similarity)

Step 3:
- Advanced Publishing Engine
  - bulk schedule via CSV/Excel
  - best-time recommendation from historical snapshots
  - queue balancing for same-time overload / API throttling
  - reusable publish presets per profile/platform

Step 4:
- Engagement + Community Ops
  - unified inbox (where Meta APIs permit)
  - moderation keyword rules and spam filters
  - SLA tracker (pending replies, average response time)
  - AI-assisted suggested replies with human approval

Step 5:
- Performance Intelligence
  - weekly/monthly report generation (PDF, email, WhatsApp integration layer)
  - campaign grouping and campaign KPI rollups
  - manager scorecards (reach, ER, saves, shares, follower growth)
  - proactive anomaly alerts (drop/spike, posting gaps, token risk, throttling)

Step 6:
- AI Copilot Upgrade
  - AI caption assistant by tone/language/platform
  - next-day content recommendations from profile history
  - low-performing content audit with corrective actions
  - competitor topic-gap recommendations (manual competitor input)

Step 7:
- High-scale Queue + Observability
  - Celery queue separation (`publish`, `insights`, `ai`, `maintenance`)
  - idempotency keys for schedule/publish APIs
  - IG-heavy scheduler windowing to reduce rate-limit bursts
  - Sentry + Prometheus/Grafana + structured logs

Step 8:
- Compliance + Security
  - production secret manager support (instead of raw `.env`)
  - audit logs for configuration and critical operations
  - stronger encryption/rotation policy for sensitive tokens
  - backup/restore + retention lifecycle for snapshots/media

Step 9:
- Business Layer
  - subscription plans and usage quotas
  - AI credit accounting model
  - in-app onboarding checklist
  - health dashboard with one-click remediation actions

## Maintenance Rule
This file must be updated whenever project behavior, workflow, automation, stored data, or important UI meaning changes.

If someone asks, "What does this project do?", this file should be the first source of truth.
