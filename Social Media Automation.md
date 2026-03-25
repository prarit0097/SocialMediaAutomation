# Postzyo Project Guide

## Project Purpose
Postzyo is a Django-based Meta growth operations app. It connects Facebook Pages and linked Instagram Business accounts, schedules and publishes posts, stores insights snapshots, and keeps enough historical data for future analytics and AI-driven recommendations.

This file is the high-level source of truth for what the project does, how the main workflows behave, and what operational rules currently apply.
Primary product branding is `Postzyo`, and production rollout is planned on `postzyo.com`.

## Core Outcomes
- Connect and refresh Facebook Pages and linked Instagram accounts through Meta OAuth.
- Store connected account metadata and encrypted page access tokens.
- Read `META_APP_ID`, `META_APP_SECRET`, and `META_REDIRECT_URI` from environment/runtime configuration for Meta OAuth and page-connect flows.
- Allow new operator signup via Google OAuth only (Gmail-based signup flow).
- Provide a dedicated Profile page for each logged-in user with Google-synced identity data and editable non-email fields.
- Provide a dedicated Subscription page with Razorpay checkout for monthly/yearly plan purchase flow.
- Give every new user a 1-day trial and lock the app after expiry until payment is completed.
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
- pricing section aligned with app billing: exactly 2 cards (`INR 6,000 / month` and `INR 70,000 / year`)
- public legal pages for Meta compliance: `Privacy Policy`, `Terms of Service`, and `User Data Deletion` available at `/privacy-policy/`, `/terms/`, and `/data-deletion/`
- legal/deletion support mailbox on public compliance pages is `1995postzyo@gmail.com`

What it does:
- helps first-time users understand app value before authentication
- routes new users to Google-based signup flow (local password signup is disabled)
- login page now also includes `Continue with Google` so operators can sign in (or create account if first-time) via same Google OAuth flow
- routes authenticated users directly to Dashboard Home to continue operations
- uses project media logos across UI touchpoints: Postzyo logo in global brand/top identity and Meta/Instagram logos in platform badges and Meta-connect CTA where context is platform-specific
- global topbar now uses horizontal Postzyo lockup logo (contain fit) so branding stays clear and non-cropped across desktop/mobile
- production reverse-proxy chain preserves the original HTTPS protocol header through both host Nginx and container Nginx so Django secure redirects do not loop on live deployment
- production container Nginx now re-resolves the Docker web service through Docker DNS, so web container restarts/redeploys do not leave stale upstream IPs that cause 502 errors

### Home
The Home page is now the workspace overview + navigation surface.

What it shows:
- redesigned control-center style hero + core outcomes cards mapped to actual project workflow
- quick actions to Accounts, Scheduler, Planning, Insights, AI Insights, and Subscription
- operational guarantees section that highlights queue safety, snapshot strategy, and token-safe execution model

### Subscription
The Subscription page is the billing workspace (`/dashboard/subscription/`).

What it shows:
- Monthly plan card: `INR 6,000 / month`
- Yearly plan card: `INR 70,000 / year`
- feature blocks under plans, describing core app capabilities included in subscription packs
- Razorpay-powered payment buttons for each plan
- current entitlement summary: active plan, status, and expiry date
- locked-access warning when a user has expired and needs payment to continue

What it does:
- new Google-signup users start on `Trial` plan with 1-day app access
- starts checkout by creating Razorpay order via backend `POST /dashboard/subscription/create-order/`
- opens Razorpay checkout popup from UI (`checkout.js`)
- verifies payment signature via backend `POST /dashboard/subscription/verify-payment/`
- activates paid entitlement after successful verification
- monthly payment switches the user to `Monthly` and extends access by one calendar month
- yearly payment switches the user to `Yearly` and extends access by one calendar year
- returns clear UI success/error messages for order creation and verification steps
- redirects the user back into the app after successful payment verification

Important runtime meaning:
- checkout requires `.env` keys: `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`
- currency is controlled by `RAZORPAY_CURRENCY` (default `INR`)
- order metadata is cached server-side before checkout so verified payments can be mapped back to the correct logged-in user and selected billing cycle
- expired users are redirected to a dedicated locked page and can only access subscription/payment routes until payment reactivates the app

### Profile
The Profile page is the logged-in user identity/settings workspace (`/dashboard/profile/`).

What it shows:
- Google-linked email (read-only)
- editable first name and last name
- read-only profile picture URL
- live profile preview card (avatar, name, email, plan badge)
- real subscription fields in read-only mode: plan name, status (`active` / `expired`), and expiry date

What it does:
- loads profile data from `GET /dashboard/profile-data/`
- saves profile updates through `POST /dashboard/profile-data/`
- allows updates only for first/last name; ignores any non-name fields in API payload
- keeps email immutable from UI/API updates (email stays login identity)
- auto-creates a per-user `UserProfile` row when missing
- pre-fills profile data from Google signup callback (given name, family name, profile picture)

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
- force-refresh status endpoint is lock-safe for SQLite contention (`database is locked`): temporary DB locks no longer break UI polling with 500 responses
- Accounts UI shows a one-time toast when a previously stuck force-refresh run is auto-recovered/finalized
- accounts list refresh now preserves the last good table if an upstream request is interrupted or returns an unreadable HTML error page, so operators see a retry notice instead of a blank/broken table
- scheduler upload failures now surface HTTP-specific guidance in the UI; proxy upload-limit failures (for example outer nginx `413`) show a direct message instead of a generic unreadable HTML error
- insight endpoints are now cache-first: if a profile has no stored snapshot yet, the UI gets an immediate placeholder response while a background Celery refresh is queued, avoiding first-load nginx timeouts
- production Docker Compose file now omits the obsolete top-level `version` key, so `docker compose` commands no longer emit the legacy schema warning during routine ops
- publish retries for transient Meta throttles now use DB-scheduled backoff instead of Celery self-retry, which avoids duplicate publish-task collisions on Instagram and keeps throttled posts in a paced auto-retry state until Meta recovers
- scheduler queue now labels throttled pending rows as `retrying`, and due-dispatch only advances one Instagram post per batch so Meta-facing publish pressure is reduced during busy windows
- force refresh-all now refuses to start while Instagram due posts are already close to publish/processing, so operators do not create avoidable Meta contention right before scheduled delivery
- publish health status is exposed to the dashboard, showing retrying/processing/due-pending pressure and the next known retry time for throttled posts
- scheduler queue now shortens transient Meta throttle text into a compact retry note in the table while preserving the full raw detail in the cell tooltip
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
- scheduler form now auto-resolves and shows `Page Name` from entered `Account ID`; for linked profiles it uses merged format (`FB page + IG profile`) same as Accounts page
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
- transient publish errors no longer stay stuck as long `processing`; row is moved back to `pending` with visible auto-retry message/countdown context
- transient publish retries now also move `scheduled_for` forward by the retry cooldown, preventing immediate re-pick loops each minute
- Instagram publish now uses a global lane lock to reduce parallel IG bursts; if lane is busy, post is re-queued with short delay instead of hammering Meta and hitting app-level throttles
- when Instagram gets app-level throttle (`code=4`), scheduler now sets a temporary global IG cooldown window so due IG jobs are held briefly instead of being picked repeatedly into processing loops
- Instagram media-ready polling now uses configurable slower defaults (`META_IG_READY_TIMEOUT`, `META_IG_READY_POLL_INTERVAL`) to reduce Graph status-check pressure
- Instagram media-ready polling now tolerates transient API failures/rate limits with backoff and continues polling instead of immediate hard-fail
- media preflight stream read timeouts (while validating public URL) are now classified as transient, so scheduler auto-retry path handles temporary ngrok/network instability instead of immediate hard-fail
- publishing retries now use longer cooldown for Graph rate-limit errors to reduce repeated burst failures during multi-profile scheduling
- failed Instagram retries also re-apply IG media optimization before requeueing
- after a successful schedule action, UI shows an immediate toast notification with the scheduled local date-time
- after successful scheduling, Create Scheduled Post form auto-clears so operators can start the next post without old field confusion
- Scheduled Queue table trims long `message` text to 4 lines for clean layout while keeping full content accessible via hover tooltip
- force-refresh post-stat calls retry transient Meta/network timeouts and backfill missing values from latest cached snapshot when possible
- Scheduler now includes **Smart Posting Assist** (profile-wise) driven by stored insights:
  - best posting windows (top weekday/time slots)
  - current 7-day cadence vs recommended cadence (separate per FB/IG where available)
  - caption A/B recommendation (short/medium/long) with reasoning from recent performance history

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
- AI planner form for niche, goal, platform, duration (`7` or `30` day), and optional connected account context
- AI-generated content calendar cards with topic, hook, CTA, format, and best-time guidance per day

What it supports:
- operator-scoped planning data per logged-in user
- API-backed monthly read/write calendar
- API-backed pillar/tag management
- item updates (including drag/drop date movement)
- OpenAI-backed content planner endpoint at `POST /api/planning/ai-calendar/`
- optional profile-aware planning when account ID is provided and latest snapshot/post history exists

Important runtime meaning:
- planning items are editorial plans, separate from `ScheduledPost` publish queue
- this allows teams to plan first, then schedule approved items into the publishing engine
- AI planner can generate ready-to-schedule calendars without writing directly into the publish queue, so operators can review first and then schedule selectively

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
- Early Engagement Monitor panel (first 6h window) with post-level status (`weak`/`average`/`strong`)
- Low Distribution Alerts panel that flags underperforming recent posts against profile baseline and suggests corrective repost/hook actions

What it supports:
- fetch cached/latest snapshot
- force refresh from Meta
- combined FB + IG view for linked assets
- newest-first published post sorting by `published_at`
- full-width published posts section with comparison table below it
- resilient Facebook published-post fetch: if rich fields are rejected for a page, API auto-falls back to minimal field set so FB rows still appear in Published Posts
- API assist endpoint for scheduler guidance: `GET /api/insights/scheduler-assist/<account_id>/`
- scheduler assist now combines best posting window, strongest recent format, and next likely topic direction per platform

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
- plain-English `What Worked` summary
- plain-English `What Flopped` summary
- recommended posting strategy (current vs suggested cadence) with mandatory separate FB/IG current cadence using last-7-days posts and avg/day
- `Next Best Post` recommendation with best time window, format, topic, and why-now explanation
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
- hybrid recommendation context that blends historical best-time / best-format / topic signals from stored post performance into the AI report

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

### User Account Signup Mode
- `/signup/` is Google-only onboarding UI.
- Google OAuth callback creates user with unusable password and logs in via Django session.
- Google OAuth callback also upserts `UserProfile` seed data (first name, last name, profile picture URL) and initializes a 1-day `Trial` subscription.
- Existing users can continue to use login flow; new account creation happens only through Google OAuth.
- `/login/` shows both classic login form and Google button (`Continue with Google`) when OAuth env is configured.
- login screen places Google CTA above username/password fields with a `Recommended` badge and helper hint, so operators naturally use OAuth first.
- login screen still supports classic username/password form below a visual divider for fallback access.

### User Profiles
Model: `accounts.UserProfile`

Stores:
- one-to-one link with Django user
- editable first name and last name (display values)
- read-only profile picture URL (from Google or backend state)
- current subscription plan (`Trial`, `Monthly`, `Yearly`)
- current subscription status (`active` / `expired`)
- current subscription expiry date (`subscription_expires_on`)
- created and updated timestamps

Behavior:
- every authenticated request refreshes subscription state from expiry date
- legacy `Starter` profile values are normalized to `Trial` during migration/runtime refresh so old placeholder plan names do not remain visible in Profile
- expired users are blocked from dashboard pages and app APIs except subscription/payment routes
- locked users are redirected to `/dashboard/subscription/expired/`, which only shows the expiry message and continue button

### Token Health UX
- topbar Health indicator is red until at least one Meta account is connected
- "no connected accounts" is treated as action required, not healthy
- token-health cache is scoped per user request context so one user does not see another user's cached health state
- once active Meta accounts are connected in the workspace, topbar Health shows green by default unless tokens are explicitly marked invalid/expired by Meta

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
- `META_APP_ID`, `META_APP_SECRET`, and `META_REDIRECT_URI` must be set in `.env` or the deployed environment for Meta connect flows to work.
- Instagram publishing requires public HTTPS media URLs.
- `PUBLIC_BASE_URL` must point to a reachable public HTTPS base.
- Celery worker and Celery beat must be running for scheduled publishing and daily heavy insights automation.
- Celery workers must be restarted after Celery config changes so new prefetch/priority behavior is applied.
- OpenAI credentials (`OPENAI_API_KEY`) must be set for AI Insights report generation.
- Google OAuth credentials must be configured for signup:
  - `GOOGLE_OAUTH_CLIENT_ID`
  - `GOOGLE_OAUTH_CLIENT_SECRET`
  - `GOOGLE_OAUTH_REDIRECT_URI` (e.g. `https://your-domain/signup/google/callback/`)
- Razorpay credentials must be configured for live subscription checkout:
  - `RAZORPAY_KEY_ID`
  - `RAZORPAY_KEY_SECRET`
  - optional `RAZORPAY_CURRENCY` (default `INR`)
- reconnecting a subset of pages does not automatically refresh every older stored account row.
- SQLite can still hit transient write locks under high parallel activity; PostgreSQL is strongly recommended for production workloads.

## Test Reliability Notes
- full Django test suite currently runs with 100 tests (plus optional skips depending on environment).
- MCP helper tests are optional and auto-skip when the external `mcp` Python package is not installed.
- Instagram local image optimization tests are auto-skip when Pillow (`PIL`) is not installed.
- publishing task tests clear cache in setup to avoid stale lock-key side effects between tests.

## Recent UI Performance Update
- Top navigation logo now uses a lightweight optimized icon asset (`postzyo-icon-optimized.png`) instead of the previous heavy lockup image.
- Logo loading priority is explicitly raised in base template (`preload` + eager image hints) so branding appears faster on first paint.
- Favicon was regenerated from the optimized icon to keep header assets consistent and lighter.

## Production Deployment Pack (KVM4 / multi-app friendly)
- Added `deploy/prod/docker-compose.prod.yml` for VPS production runs:
  - keeps Postgres/Redis private inside Docker network
  - serves Django via Gunicorn + app Nginx
  - binds app Nginx on loopback (`127.0.0.1:${APP_INTERNAL_PORT}`) so host reverse proxy can route multiple apps safely on one VPS
  - includes persistent Docker volumes for DB, Redis AOF, static, and media
- Added `deploy/prod/install_on_vps.sh`:
  - installs Docker, Nginx, Certbot, and Git on Ubuntu VPS
  - clones/updates repo under `/opt/postzyo`
  - writes host Nginx site for domain proxying
  - runs Docker production stack, migrations, collectstatic
  - attempts SSL certificate issuance with Certbot
- Added `deploy/prod/update_on_vps.sh`:
  - pulls latest code and performs rolling update (`up -d --build`, migrate, collectstatic)
- Upgraded app Nginx config (`deploy/nginx/nginx.conf`) to:
  - support larger upload limits
  - directly serve `/static/` and `/media/`
  - include safer proxy timeout settings for longer publish/insight requests

## VPS Access + Health Check Guide
This section is for anyone new to the live environment who needs to access, inspect, or verify the production deployment quickly.

### Live VPS App Location
- production app path on VPS: `/opt/apps/postzyo`
- expected working directory before most ops commands:
  - `cd /opt/apps/postzyo`

### Live Production Stack
The live app runs in Docker containers under the Compose project name `postzyo`.

Current production services/container names:
- `postzyo-web-1`
- `postzyo-worker-1`
- `postzyo-beat-1`
- `postzyo-nginx-1`
- `postzyo-db-1`
- `postzyo-redis-1`

Important runtime meaning:
- host machine Nginx is also active and proxies traffic into the app container Nginx
- production compose metadata on running containers points to:
  - `/opt/apps/postzyo/deploy/prod/docker-compose.prod.yml`
- if a pasted command ever shows blank `docker compose ... ps` output, verify with direct Docker container commands before assuming the app is down

### Basic VPS Access Commands
- move into project:
  - `cd /opt/apps/postzyo`
- confirm repo state:
  - `git status`
  - `git log -1 --oneline`
- list live Postzyo containers:
  - `docker ps --filter "name=postzyo"`

### Core Health Check Commands
Run these when you want a fast production sanity check.

- Docker containers:
  - `docker ps --filter "name=postzyo"`
- Django app health:
  - `docker exec postzyo-web-1 python manage.py check`
- migration status:
  - `docker exec postzyo-web-1 python manage.py showmigrations`
- connected token/account inventory:
  - `docker exec postzyo-web-1 python manage.py check_meta_tokens`
- Redis health:
  - `docker exec postzyo-redis-1 redis-cli ping`
- PostgreSQL health:
  - `docker exec postzyo-db-1 pg_isready -U postgres`
- Celery worker health:
  - `docker exec postzyo-worker-1 celery -A social_automation inspect ping`
- container Nginx config health:
  - `docker exec postzyo-nginx-1 nginx -t`

### Useful Log Commands
- web logs:
  - `docker logs --tail=100 postzyo-web-1`
- worker logs:
  - `docker logs --tail=100 postzyo-worker-1`
- beat logs:
  - `docker logs --tail=100 postzyo-beat-1`
- app nginx logs:
  - `docker logs --tail=100 postzyo-nginx-1`

What healthy logs usually mean:
- `postzyo-web-1` should show Gunicorn booted and listening on `0.0.0.0:8000`
- `postzyo-worker-1` should show Celery receiving and completing `publishing.tasks.process_due_posts`
- `postzyo-beat-1` should show `Scheduler: Sending due task process-due-posts-every-minute`
- `postzyo-nginx-1` will also show normal traffic plus random bot scans; repeated `404` requests for WordPress paths do not mean this app is broken

### Public Endpoint Checks
- home page:
  - `curl -I https://postzyo.com`
- login page:
  - `curl -I https://postzyo.com/login/`
- dashboard auth redirect behavior:
  - `curl -I https://postzyo.com/dashboard/`

Expected quick outcomes:
- home page should return `200 OK`
- login page should return `200 OK`
- dashboard should return `302` to `/login/?next=/dashboard/` when not authenticated

### Host-Level Checks
- disk:
  - `df -h`
- memory:
  - `free -h`
- uptime/load:
  - `uptime`
- process view:
  - `top`
- host Nginx status:
  - `systemctl status nginx`

### Compose-Based Commands
When using Compose directly for the production stack, use:
- `docker compose -f deploy/prod/docker-compose.prod.yml ps`
- `docker compose -f deploy/prod/docker-compose.prod.yml logs web --tail=100`
- `docker compose -f deploy/prod/docker-compose.prod.yml logs worker --tail=100`
- `docker compose -f deploy/prod/docker-compose.prod.yml logs beat --tail=100`
- `docker compose -f deploy/prod/docker-compose.prod.yml logs nginx --tail=100`

If Compose output ever looks inconsistent, confirm the running containers with:
- `docker inspect postzyo-web-1 --format '{{json .Config.Labels}}'`

This should show labels including:
- Compose project: `postzyo`
- config file: `/opt/apps/postzyo/deploy/prod/docker-compose.prod.yml`

### Common Operator Interpretation
- if `docker exec postzyo-web-1 python manage.py check` passes, Django config is structurally healthy
- if `pg_isready` passes, database is reachable
- if Redis responds `PONG`, cache/broker is reachable
- if Celery inspect returns `pong`, worker is online
- if Beat logs keep sending minute tasks and Worker logs keep receiving them, publishing automation loop is healthy
- if website returns `200 OK` but Compose commands appear empty, check pasted command integrity and direct container status before troubleshooting deployment

## Live-Readiness Hardening (Django settings)
- Added explicit production security env toggles in settings:
  - `SECURE_SSL_REDIRECT`
  - `SESSION_COOKIE_SECURE`
  - `CSRF_COOKIE_SECURE`
  - `SECURE_HSTS_SECONDS`
  - `SECURE_HSTS_INCLUDE_SUBDOMAINS`
  - `SECURE_HSTS_PRELOAD`
- Added proxy-aware defaults for VPS reverse-proxy deployment:
  - `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`
  - `USE_X_FORWARDED_HOST = True`
- Enabled baseline hardening headers:
  - `SECURE_CONTENT_TYPE_NOSNIFF = True`
  - `X_FRAME_OPTIONS = "DENY"`
- `.env.example` now includes these security flags so production env setup is predictable for Postgres + Nginx deployments.

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




