# Postzyo Project Guide

## Project Purpose
Postzyo is a Django-based Meta growth operations app. It connects Facebook Pages and linked Instagram Business accounts, schedules and publishes posts, stores insights snapshots, and keeps enough historical data for future analytics and AI-driven recommendations.

This file is the high-level source of truth for what the project does, how the main workflows behave, and what operational rules currently apply.
Primary product branding is `Postzyo`. Its public site is `postzyo.com`.
Primary Git repository: `https://github.com/prarit0097/SocialMediaAutomation`

## Core Outcomes
- Connect and refresh Facebook Pages and linked Instagram accounts through Meta OAuth.
- Store connected account metadata and encrypted page access tokens.
- Read `META_APP_ID`, `META_APP_SECRET`, and `META_REDIRECT_URI` from environment/runtime configuration for Meta OAuth and page-connect flows.
- Allow new operator signup via Google OAuth only (Gmail-based signup flow).
- Provide a dedicated Profile page for each logged-in user with Google-synced identity data and editable non-email fields.
- Provide a dedicated Subscription page with Razorpay checkout for monthly/yearly plan purchase flow.
- Provide a public Help page with FAQ and a support request form that emails submitted requests to the configured support mailbox.
- Give every new user a 3-day trial and lock the app after expiry until payment is completed.
- Isolate connected accounts, tokens, schedules, planning context, and analytics to the logged-in user who owns them.
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
- real Postzyo Scheduler and Planning screenshots populated with sample data. Phones use dedicated WebP captures of the post composer and the calendar item form. Larger screens show the desktop composer and calendar grid. The Planning preview lazy-loads, and the homepage uses a Scheduler preview when shared.
- clear `Login` and `Signup` actions
- simple step flow explaining setup to analytics journey
- the homepage leads with the trial and a link to the workflow, then explains planning, per-platform publishing status, and Insights. Section links jump to features, workflow, pricing, and FAQ. The footer links to pricing, help, and legal pages.
- pricing section aligned with app billing: exactly 2 cards (`INR 6,000 / month` and `INR 70,000 / year`)
- public legal pages for Meta compliance: `Privacy Policy`, `Terms of Service`, and `User Data Deletion` available at `/privacy-policy/`, `/terms/`, and `/data-deletion/`
- legal/deletion support mailbox on public compliance pages is driven by `HELP_SUPPORT_EMAIL` (currently `1995praritsidana@gmail.com`)
- public Help page at `/help/` includes FAQ plus a structured support form without exposing the private support mailbox in the UI; submitted requests are emailed to `HELP_SUPPORT_EMAIL` (currently `1995praritsidana@gmail.com`) and users see a professional 24-hour response confirmation after submission
- shared site template loads Meta Pixel `1703024424409702` and Google Analytics 4 measurement ID `G-SCZT2H8750`, tracking page views across public and logged-in Django pages
- Meta Pixel and GA4 conversion events are now wired across major funnels: public signup/pricing CTAs (`Lead`, `ViewContent`), Google signup start (`Lead` + `GoogleSignupStart`), server-confirmed Google registration (`CompleteRegistration`), server-confirmed login (`Login`), successful Meta account connection (`MetaAccountConnected`), subscription page view (`ViewContent`), Razorpay checkout start (`InitiateCheckout`), Razorpay order creation (`PaymentOrderCreated`), verified payment (`Purchase` with INR value), scheduled post creation (`SchedulePost`), insights refresh (`InsightsRefresh`), AI profile report generation (`AIInsightsGenerated`), and AI planner generation (`AIPlannerGenerated`)

What it does:
- helps first-time users understand app value before authentication
- routes new users to Google-based signup flow (local password signup is disabled)
- login page now also includes `Continue with Google` so operators can sign in (or create account if first-time) via same Google OAuth flow
- routes authenticated users directly to Dashboard Home to continue operations
- keeps Help available from both public and authenticated top navigation, including expired users who may need billing/access support before reactivation
- uses project media logos across UI touchpoints: Postzyo logo in global brand/top identity and Meta/Instagram logos in platform badges and Meta-connect CTA where context is platform-specific
- global topbar now uses horizontal Postzyo lockup logo (contain fit) so branding stays clear and non-cropped across desktop/mobile
- production reverse-proxy chain preserves the original HTTPS protocol header through both host Nginx and container Nginx so Django secure redirects do not loop on live deployment
- production container Nginx now re-resolves the Docker web service through Docker DNS, so web container restarts/redeploys do not leave stale upstream IPs that cause 502 errors

### Homepage assistant and follow-up requests

- **Ask Postzyo** opens a responsive assistant for visitors. It matches more than 10,000 question phrasings to maintained Postzyo answers, including common Hinglish wording and spelling mistakes. Topics include pricing, the trial, account setup, Meta permissions, publishing, Planning, Insights, billing and privacy.
- Answers use current trial and plan values from the app. When the assistant cannot find a clear match, it links to Help. The conversation stays in the current page and the server does not store it as chat history.
- Visitors can optionally request an email follow-up by entering a name and email and selecting the consent checkbox. Postzyo records the contact details and submission time once per normalized email. Asking a question requires no signup, purchase or contact details.
- Authorized staff can review requests in Django Admin under **Accounts → Homepage follow-up requests**. The consent form links to the Privacy Policy, and visitors can use the Data Deletion page to request removal. The form saves a request for staff to review.
- Questions and follow-up requests have separate rate limits. With the two-proxy deployment, Postzyo uses the client address appended by the outermost trusted proxy.

### Public search and pricing

- The public blog at `/blog/` lists illustrated guides for Facebook and Instagram scheduling, launch rehearsals, Meta connections, campaign planning and closeout, accessibility, website-link audits and reporting. Guides also cover multilingual campaigns, after-hours posts, replacing scheduled media and weekly calendar handoffs. Each guide has its own canonical URL, share image, BlogPosting metadata and sitemap entry, plus links to two related guides. Add new articles and their related guide slugs in `accounts/articles.py`.

- Public landing, pricing, help and legal pages render server-side with page-specific titles, descriptions, absolute canonical URLs, social metadata and structured data. `SEO_CANONICAL_ORIGIN` defaults to `https://postzyo.com` and is independent of the media URL configuration.
- `/pricing/` shows the same INR plan prices and term lengths that checkout uses. `/robots.txt` points crawlers to `/sitemap.xml`, which includes only the registered public pages. Public sign-in utilities and authenticated workspace responses carry `noindex` directives.
- The host proxy can direct HTTP and `www` traffic to the HTTPS apex in one path- and query-preserving redirect. See [VPS rollout](#vps-rollout-for-canonical-host-and-publishing-workers) for certificate, provider callback and Search Console checks.

### Home
The Home page is now the workspace overview + navigation surface.

What it shows:
- hero section rebuilt on the same shared dashboard layout system used by other app pages (`hero-card`, `hero-split`, shared buttons, shared stat rail), so live sizing and spacing stay consistent with already-stable production CSS
- quick action row for the 3 most common jumps: `Accounts`, `Scheduler`, and `Insights`
- trust/capability chips that summarize the operator promise: connected FB + IG workflow, queue-safe publishing, snapshot-backed reporting, and AI next-step guidance
- hero-side visual preview with a snapshot-run and shared-account summary
- `Start Here` workflow section shown as a 4-step operator path: connect assets -> plan/schedule -> review results -> improve with AI
- compact 6-card workspace grid for `Accounts`, `Scheduler`, `Planning`, `Insights`, `AI Insights`, and `Subscription`, each with a direct button and simpler plain-English purpose copy
- final 2-panel guidance area: recommended daily rhythm on the left and operational reliability explanation on the right
- Home no longer shows `Meta App Configuration` or `Setup Guide`; those setup-heavy sections were removed so the landing workspace stays focused on navigation and daily execution

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
- new Google-signup users start on `Trial` plan with 3-day app access
- starts checkout by creating Razorpay order via backend `POST /dashboard/subscription/create-order/`
- opens Razorpay checkout popup from UI (`checkout.js`)
- verifies payment signature via backend `POST /dashboard/subscription/verify-payment/`
- activates paid entitlement after successful verification
- keeps a DB-backed `SubscriptionOrder` record for each checkout so verification is replay-safe and does not rely only on cache
- monthly payment switches the user to `Monthly` and extends access by one calendar month
- yearly payment switches the user to `Yearly` and extends access by one calendar year
- prepaid upgrades and renewals preserve remaining time on an active paid term when the plan is activated
- returns clear UI success/error messages for order creation and verification steps
- redirects the user back into the app after successful payment verification
- when Monthly plan is active, only the monthly button is disabled (yearly upgrade allowed)
- when Yearly plan is active, both monthly + yearly buttons are disabled until expiry
- Pay Yearly button now blinks by default to attract attention and stops blinking only when Yearly plan is active
- subscription button disabled/active labels are rendered server-side from current plan/status, then reinforced by JS after payment
- Yearly plan card is visually prioritized with a recommended badge, animated glow, and first position in the plan grid to draw attention

Important runtime meaning:
- checkout requires `.env` keys: `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`
- currency is controlled by `RAZORPAY_CURRENCY` (default `INR`)
- order metadata is persisted in both cache and the `dashboard.SubscriptionOrder` table, so verified payments can still be mapped after cache loss and duplicate verification posts do not stack extra time
- expired users are redirected directly to the Subscription payment page and can only access subscription/payment routes until payment reactivates the app
- expired users are routed to the Subscription payment page after login or when opening the site root, so they can pay immediately without landing back on the public homepage or an extra locked interstitial

### Profile
The Profile page is the logged-in user identity/settings workspace (`/dashboard/profile/`).

What it shows:
- Google-linked email (read-only)
- editable first name and last name
- live profile avatar/preview sourced from Google-backed profile data, without exposing the raw profile picture URL field to end users
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
- Connected Accounts rows are ordered by posting recency: profiles that posted within the last 24 hours come first (newest `last_post_at` at the very top), profiles stale for more than 24 hours sink below them (also newest-first), and profiles with no detected post at all sit last, so the most recently active profiles are always visible without scrolling
- stale sync indicator when a stored account row was not refreshed in the latest Meta reconnect
- view meta shows both merged-row count and active raw connected-row count
- per-row quick actions: Schedule, Insights, and AI Insights
- actions UI uses a priority-styled Schedule button with compact Insights/AI Insights secondary buttons
- Connected Accounts table layout is compacted so action buttons stay inside table width without forcing right-side horizontal scrolling in normal desktop view
- one-click `Force Refresh All Profiles` button to queue a full connected-account refresh sweep

What it does:
- starts the Meta connect flow
- on reconnect, re-mints fresh per-page access tokens for ALL granted Business-Manager pages (token `target_ids`), not only the `/me/accounts` pages, so catalog-discovered profiles do not later fail with `code=190`; this runs in the background via `integrations.tasks.resync_page_tokens_task` (see "Business-Manager Page-Token Resync")
- only allows the logged-in operator to complete the Meta callback; OAuth state is tied to both cache and the current session/user
- refreshes the connected account list
- `Refresh List` now forces fresh reads for both connected accounts list and Meta catalog (`?refresh=1`), so operators see latest reconnect state immediately
- shows current sync status and Meta page catalog data
- Accounts list endpoint is per-user throttled (`30/m`) and Meta catalog endpoint is per-user throttled (`10/m`) so one operator cannot flood the DB/cache or Meta lookups during heavy usage
- can queue force-refresh jobs for all active connected profiles to pull latest Meta insights into snapshots
- force-refresh-all now uses persistent per-user run tracking with live progress (%) and completion state, so the button stays disabled until that user's run finishes (even after page reload or re-login)
- force-refresh run creation is now serialized per user with DB row locking, so concurrent double-click/API bursts cannot create duplicate active runs for the same operator
- force-refresh-all now asks for operator confirmation before starting, because full refresh can take significant time based on connected FB/IG profile count
- if Instagram due/processing pressure is high, force-refresh-all shows a busy warning and now allows explicit operator override from the UI confirmation path
- force-refresh run status is now auto-reconciled: if snapshot storage succeeds but callback bookkeeping misses, counters self-heal and stale `running` states are auto-finalized
- force-refresh auto-reconcile now counts only that operator's snapshots, preventing cross-user snapshot activity from falsely completing someone else's run
- a reconciled/completed force-refresh run no longer blocks the next refresh request for that same user
- force-refresh run counters (`queued_count`, `skipped_no_token`) are persisted before dispatch so an early result, such as an account already leased by another refresh, cannot finalize the run too early. Post-dispatch bookkeeping adds to the persisted counters under a row lock.
- inline queue-down fallback results are folded into `queued_count` as already-reported outcomes, so the completion threshold stays correct when Celery dispatch fails; the API response still reports Celery-dispatched work separately as `queued`
- the per-user force-refresh cooldown is armed only when Meta work actually started (`queued > 0` or an inline refresh succeeded); a run where every profile was skipped for a missing token no longer locks the operator out for the cooldown window
- the force-refresh status endpoint is per-user throttled (`60/m`) and now clears the cached accounts list only once per finished run, instead of on every 7-second poll and every Accounts page load
- run reconcile now judges completion on REPORTED task outcomes only (`completed + failed`), matching the task-side bookkeeping; `skipped_no_token` / `enqueue_failed` rows never dispatch a task, and counting them toward the `queued_count` threshold used to let the first status poll finalize a run as completed while real refresh tasks were still running (their late outcomes were then dropped)
- force-refresh status endpoint is lock-safe for SQLite contention (`database is locked`): temporary DB locks no longer break UI polling with 500 responses
- Accounts UI shows a one-time toast when a previously stuck force-refresh run is auto-recovered/finalized
- force-refresh status polling now avoids overlapping requests, applies retry backoff on temporary failures, and shows retry status text instead of silently swallowing poll errors
- force-refresh API now returns a terminal status (`completed`/`completed_with_errors`) when no profile task is queued, instead of always reporting `queued`
- if Celery queue dispatch fails during force-refresh-all, backend now attempts inline Meta refresh fallback for the affected profiles so button action still refreshes data instead of silently doing nothing
- Accounts `last_post_at` now resolves against the freshest available snapshots across all active profiles instead of being biased toward lower account IDs when many snapshots exist, so a completed force refresh shows recent posting times more reliably
- when force-refresh-all finishes, Accounts list cache is cleared and the Accounts UI re-requests fresh rows so newly collected `last_post_at` values appear without waiting for stale cached data to age out
- accounts list refresh now preserves the last good table if an upstream request is interrupted or returns an unreadable HTML error page, so operators see a retry notice instead of a blank/broken table
- scheduler upload failures now surface HTTP-specific guidance in the UI; proxy upload-limit failures (for example outer nginx `413`) show a direct message instead of a generic unreadable HTML error
- insight endpoints are now cache-first: if a profile has no stored snapshot yet, the UI gets an immediate placeholder response while a background Celery refresh is queued, avoiding first-load nginx timeouts
- production Docker Compose file now omits the obsolete top-level `version` key, so `docker compose` commands no longer emit the legacy schema warning during routine ops
- publish retries for transient Meta throttles now use DB-scheduled backoff instead of Celery self-retry, which avoids duplicate publish-task collisions on Instagram and keeps throttled posts in a paced auto-retry state until Meta recovers
- scheduler shows a distinct `Retrying` state for deferred delivery and preserves the requested schedule alongside the next attempt
- force refresh-all now refuses to start while Instagram due posts are already close to publish/processing, so operators do not create avoidable Meta contention right before scheduled delivery
- publish health status is exposed to the dashboard, showing retrying/processing/due-pending pressure and the next known retry time for throttled posts
- scheduler queue shows the current retry, account-block or delivery-verification action alongside each destination
- uses user-token fallback for catalog detail checks in this order only: session token -> per-user cache -> encrypted per-user DB token
- does not reuse another user's cached Meta user-access token for catalog lookups
- scopes account listing, sync status, catalog rows, force-refresh runs, and downstream scheduler/insights actions to the current user only
- keeps only latest reconnect profiles active in scheduling/health
- blocks scheduling from stale or inactive account rows until the profile is refreshed in a new reconnect
- shows Meta catalog in merged FB_IG rows when linked, and separate rows when unlinked

Important runtime meaning:
- a green Health indicator does not mean every historical stored account row is usable
- scheduling is only allowed for account rows refreshed in the latest reconnect window
- if older connected rows still exist outside the latest reconnect window, Health turns red and asks for reconnect
- profiles not returned in the latest reconnect are marked inactive and excluded from active account lists

### All Accounts Overview
The Overview page (`/dashboard/overview/`, top-nav "Overview") is the portfolio view across every connected account.

What it shows (one sortable/searchable table, snapshot-backed so it loads fast):
- account (page name + thumbnail + FB/IG badge), followers, 7-day follower change (green up / red down), reach (7d), engagement (7d), last post (relative), queued posts (with a ⚠ flag + count when any are failed), and a health badge (`OK` / `Stale` if the latest snapshot is older than ~36h / `No data`)
- sortable by any column; searchable by page name; filterable by platform
- clicking any row opens that account's Insights (`/dashboard/insights/?account_id=...`)

What it does:
- served by `GET /api/insights/overview/` (per-user throttled `30/m`), which reads each account's latest stored `InsightSnapshot` (no live Meta calls) and aggregates follower/reach/engagement plus pending/failed publish counts and last-published time
- metrics are extracted from stored snapshots one account at a time, so only ~2 snapshot payloads are resident at once (memory-safe for large fleets); the 7-day follower change compares the latest snapshot to the newest snapshot at least 7 days old

### Scheduler

Scheduler supports Facebook Pages, connected Instagram professional accounts, combined FB + IG posts, and one composed post sent to multiple selected accounts. The queue shows an independent delivery row for each destination. Users choose the account with a searchable picker, confirm the browser-local time, and can view profile-specific posting windows and caption guidance from stored insights.

The account picker closes after selection. Bulk mode shows the multi-account controls and hides the single-account fields.

- The scheduling time is stored in UTC with a display timezone. `next_attempt_at` governs delivery and backoff. Schedule requests with the same user-scoped idempotency key and content return the original result. The composer reuses the key when retrying an unchanged submission after a network error.
- Operators can edit, reschedule or cancel eligible queued destinations and retry known failures. The queue shows Scheduled, Processing, Retrying, Blocked, Verify outcome, Published, Failed and Cancelled. Opening it only reads the signed-in operator's rows.
- The dispatcher selects due Facebook and Instagram work separately. PostgreSQL claims and attempt generations authorize the worker. A database constraint limits each Instagram account to one active publishing claim. The worker checks ownership, subscription, account and token eligibility at claim and again before sending to Meta.
- Safe preparation or explicit provider-rejection retries retain the requested time. Exhausted Instagram publishing quota defers container creation. Worker loss before the final write can requeue. Loss during the final write enters `outcome_unknown` and preserves available provider evidence for read-only reconciliation. When confirmation is missing, the row remains visible for operator review.
- Facebook uses direct multipart upload for eligible stored files and retains original media in combined schedules. Instagram uses an optimized image where applicable and supports resumable upload for stored Reel video. Validate public media reachability, file limits and platform requirements before sending. Persist the Instagram media container ID to support safe retry and recovery.
- Facebook video upload can continue processing after acceptance. Video processing and Page publication checks are read-only. The queue marks Published after provider evidence confirms delivery. Instagram container status alone may lack the media ID required to confirm the corresponding post.

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
- planning tag pills and AI planner result cards now escape dynamic text before rendering, so user/API/AI content cannot inject raw HTML into the page

What it supports:
- operator-scoped planning data per logged-in user
- API-backed monthly read/write calendar
- API-backed pillar/tag management
- item updates (including drag/drop date movement)
- OpenAI-backed content planner endpoint at `POST /api/planning/ai-calendar/`
- optional profile-aware planning when account ID is provided and latest snapshot/post history exists
- connected-account context is accepted only when that account belongs to the logged-in user

Important runtime meaning:
- planning items are editorial plans, separate from `ScheduledPost` publish queue
- this allows teams to plan first, then schedule approved items into the publishing engine
- AI planner can generate ready-to-schedule calendars without writing directly into the publish queue, so operators can review first and then schedule selectively
- clicking a planned calendar item opens the Scheduler pre-filled with that item's account, platform, caption, and planned time (no manual re-entry); after the post is scheduled, the calendar item is automatically moved to `scheduled` status, linking the plan to the publish queue (calendar status colors already distinguish draft / review / approved / scheduled / published)

### Insights
The Insights page is the reporting view for account-level Meta data and recent post performance.

What it shows:
- total followers
- total following
- total post share
- published posts table
- Published Posts table hides internal post IDs from operators and shows only user-facing content/metrics columns
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
- if no stored snapshot exists yet, the API returns a placeholder payload immediately and queues a background refresh instead of crashing or waiting on a slow live pull
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
- operator focus text is treated as untrusted preference input and sanitized/truncated before prompt assembly
- AI generation (AI Insights report + AI content planner) is provider-configurable via `.env`:
  - `AI_PROVIDER=openai` (default) uses `OPENAI_API_KEY` + `OPENAI_MODEL`
  - `AI_PROVIDER=openrouter` routes the same OpenAI-compatible call through OpenRouter using `OPENROUTER_API_KEY` + `OPENROUTER_MODEL` (e.g. `deepseek/deepseek-chat` and other DeepSeek slugs); base URLs are overridable via `OPENAI_BASE_URL` / `OPENROUTER_BASE_URL`
  - the AI Insights response reports the actual active model

## Background Automation

### Scheduled Publishing Automation
- task: `publishing.tasks.process_due_posts`
- frequency: every minute
- purpose: move due scheduled posts into publish tasks
- publishing and reconciliation run on the `publish` queue. Analytics refresh and token resync run on `insights` with a separate worker.
- dispatcher errors are logged per post. If a broker acknowledgement is lost, the claim stays fenced until the lease expires. A delayed duplicate delivery cannot take over a newer claim.

### Daily Heavy Insights Automation
- task: `analytics.tasks.queue_daily_heavy_insight_refresh`
- default schedule: every day at `05:00 AM`
- timezone source: `CELERY_TIMEZONE`
- intended timezone: `Asia/Kolkata`
- purpose: fetch the heaviest practical insights snapshot for every connected profile and store it for UI and future analytics
- manual and background refresh share an expiring lease on the account row. Lease ownership prevents an older refresh from releasing a newer claim.
- nightly fan-out selects active accounts. A hard Meta `code=190` deactivates only the account token generation that produced the error. A fresh reconnect or successful resync can reactivate the account.

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
- owning Django user
- platform
- page ID
- page name
- Instagram linked user ID if available
- encrypted page access token
- token expiry if available
- created and updated timestamps

Operational meaning:
- every connected account row belongs to exactly one logged-in user
- uniqueness is enforced per `(user, platform, page_id)`
- `updated_at` reflects when the stored connected account row was last refreshed
- this is used to determine whether a row belongs to the latest reconnect window
- `is_active` tracks whether a stored row is part of the latest usable reconnect set
- encrypted token text itself is not used for DB filtering decisions; active/inactive state is managed with `is_active`
- encrypted token values now survive Django model/form/admin round-trips without accidental double-encryption
- active-account lookups are indexed on `(user, is_active)` so reconnect/account screens stay fast under larger user counts

### Subscription Orders
Model: `dashboard.SubscriptionOrder`

Stores:
- owning Django user
- Razorpay order ID
- plan key and billing cycle
- payment ID once verified
- verification status and consume timestamp
- created and updated timestamps

Operational meaning:
- checkout verification is durable even if cache is cleared or restarted
- a verified payment POST body can be processed idempotently without extending the same subscription twice

### Meta User OAuth Token
Model: `integrations.MetaUserToken`

Stores:
- per-user Meta OAuth user access token in encrypted form
- created and updated timestamps

Operational meaning:
- catalog detail lookups can recover after cache/server restarts
- token resolution order is session token -> per-user cache -> encrypted DB token
- when catalog API sees a valid session/cache token for the current user, it auto-persists that token into DB for durability

### User Account Signup Mode
- `/signup/` is Google-only onboarding UI.
- Google OAuth callback creates user with unusable password and logs in via Django session.
- Google OAuth callback also upserts `UserProfile` seed data (first name, last name, profile picture URL) and initializes a 3-day `Trial` subscription.
- Google OAuth state is bound to the browser session, so a cached state token alone is not enough to complete login
- Existing users can continue to use login flow; new account creation happens only through Google OAuth.
- `/login/` shows both classic login form and Google button (`Continue with Google`) when OAuth env is configured.
- login screen places Google CTA above username/password fields with a `Recommended` badge and helper hint, so operators naturally use OAuth first.
- login screen still supports classic username/password form below a visual divider for fallback access.
- both Google login callback and classic username/password login now force a persistent Django session expiry based on `SESSION_COOKIE_AGE`, so closing and reopening the browser should not require immediate re-login.
- default session persistence is now env-configurable with:
  - `SESSION_COOKIE_AGE=2592000` (`30` days)
  - `SESSION_EXPIRE_AT_BROWSER_CLOSE=False`
  - `SESSION_SAVE_EVERY_REQUEST=False` so production does not write session state on every request
- when `CACHE_BACKEND=redis`, Django sessions are stored in Redis instead of the database to reduce write load during high concurrent usage
- subscription-access middleware now caches each user's subscription profile for 5 minutes and clears that cache after successful payment verification so access updates immediately without repeated DB reads
- logout is POST-only and rendered as a CSRF-protected form instead of a GET link
- runtime Meta app config endpoint still exists for controlled recovery/admin use, but it is blocked for normal logged-in users and only available in debug or for staff/admin accounts

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
- middleware resolves subscription state once per request path where access decisions matter and avoids mutating the profile through a property read
- legacy `Starter` profile values are normalized to `Trial` during migration/runtime refresh so old placeholder plan names do not remain visible in Profile
- expired users are blocked from dashboard pages and app APIs except subscription/payment routes
- locked users are redirected to `/dashboard/subscription/` for immediate payment; `/dashboard/subscription/expired/` remains as a lightweight informational page but is no longer the primary redirect target

### Token Health UX
- topbar Health indicator is red until at least one Meta account is connected
- "no connected accounts" is treated as action required, not healthy
- token-health cache is scoped per user request context so one user does not see another user's cached health state
- once active Meta accounts are connected in the workspace, topbar Health shows green by default unless tokens are explicitly marked invalid/expired by Meta

### Scheduled Posts
Model: `publishing.ScheduledPost`

Stores:
- target account
- schedule owner, original schedule and display timezone, next attempt time, and reschedule history
- optional request key record for grouping FB + IG or bulk destinations
- platform
- message
- media URL
- scheduled time
- status
- external Meta post ID
- attempt generation and lease, delivery stage, Instagram container ID, Facebook video upload ID, and outcome-verification context
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
- when Redis cache is enabled, sessions also live in Redis so authenticated traffic does not add per-request session writes to PostgreSQL
- Use separate Celery workers for `publish` and `insights`. Verify queue isolation, claim recovery, latency and resource use with representative staging load before claiming a production capacity.
- hot tables now ship with DB indexes for active-account lookups, due-post scans, account/status scans, snapshot recency, and subscription expiry checks so high-traffic dashboard queries avoid full-table scans
- critical write paths now use DB row locks where needed (`select_for_update()` in bulk-refresh bookkeeping and subscription verification) so concurrent requests do not double-finalize the same workflow
- Meta Graph calls reuse a shared `requests.Session` with HTTP connection pooling (`pool_connections=20`, `pool_maxsize=50`) so heavy publish/insight traffic avoids excessive TCP connection churn
- heavy dashboard APIs now use per-user cache-backed throttles (`schedule_post 60/m`, `list_accounts 30/m`, `meta_pages_catalog 10/m`) to protect the app from operator bursts and abusive clients
- Tune runtime env knobs:
  - `DB_CONN_MAX_AGE`, `DB_CONN_HEALTH_CHECKS`
  - `CELERY_PUBLISH_RATE_LIMIT`, `CELERY_INSIGHTS_REFRESH_RATE_LIMIT`
  - `CELERY_WORKER_MAX_TASKS_PER_CHILD`
  - `CELERY_TASK_SOFT_TIME_LIMIT`, `CELERY_TASK_TIME_LIMIT`
  - `BULK_REFRESH_STALE_MINUTES`
  - `FORCE_REFRESH_IG_GUARD_MINUTES`, `FORCE_REFRESH_IG_GUARD_DUE_LIMIT`
- application defaults for Celery soft/hard time limits are `540` / `600` seconds. Deployments can set both through the environment. The production publishing worker uses prefork so Celery can enforce task time limits.
- Restart web + worker processes after config changes so new concurrency controls apply.

### One-command Celery Startup (Windows)
- Script: `start_celery_cluster.ps1` (project root)
- Purpose: start these 3 processes automatically in separate PowerShell windows:
  - `publish` (`-Q publish --pool=threads -c 4`)
  - `insights` (`-Q insights --pool=threads -c 4`)
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
5. Worker claims the destination and checks ownership, entitlement and current account eligibility before the final Meta request.
6. A provider confirmation marks that destination `published`. Recoverable delays go to `retry_wait` or `blocked`, known failures to `failed`, and uncertain writes to `outcome_unknown` for read-only verification.

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
- Current Meta scope set used by this app:
  - `pages_show_list`
  - `pages_read_engagement`
  - `pages_manage_posts`
  - `read_insights`
  - `instagram_basic`
  - `instagram_content_publish`
  - `instagram_manage_insights`
- `META_APP_ID`, `META_APP_SECRET`, and `META_REDIRECT_URI` must be set in `.env` or the deployed environment for Meta connect flows to work.
- production must set an explicit `SECRET_KEY`; placeholder secrets are rejected outside debug mode
- production must set `FERNET_KEY` or `FERNET_KEYS` so encrypted Meta tokens remain decryptable across restarts and rotations
- Instagram publishing requires public HTTPS media URLs.
- `PUBLIC_BASE_URL` must point to a reachable public HTTPS base.
- `TRUST_REVERSE_PROXY=True` should only be enabled when the deployment is actually behind a trusted proxy that sets `X-Forwarded-Proto`
- `MAX_UPLOAD_FILE_BYTES` controls the maximum accepted media upload size in Django
- force-refresh Instagram contention guard knobs are env-configurable:
  - `FORCE_REFRESH_IG_GUARD_MINUTES` (default `20`)
  - `FORCE_REFRESH_IG_GUARD_DUE_LIMIT` (default `1`)
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
- Help form email delivery requires SMTP/email environment values in production:
  - `HELP_SUPPORT_EMAIL` (default `1995praritsidana@gmail.com`)
  - `DEFAULT_FROM_EMAIL`
  - `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`
  - `EMAIL_USE_TLS` / `EMAIL_USE_SSL` based on the mail provider
- local Docker Compose now expects env-driven Postgres and Redis credentials; Redis uses password auth and host port bindings stay on loopback
- reconnecting a subset of pages does not automatically refresh every older stored account row.
- SQLite can still hit transient write locks under high parallel activity; PostgreSQL is strongly recommended for production workloads.

## Test Reliability Notes
- The publishing and technical SEO release was verified with 238 Django tests on PostgreSQL 16, including PostgreSQL-only locking and upgrade coverage. Local browser checks exercised schedule replay, status filters and queue edits. Nginx checks covered redirects, callbacks, media and staging access.
- static storage coverage (`core/tests_storage.py`) locks in hashed URLs when a manifest entry exists, unhashed fallback when it is missing or absent, and that the stock Django storage would raise in that case.
- force-refresh-all regression coverage now explicitly checks counter seeding before dispatch, preservation of task-written counters against the post-dispatch save, cooldown suppression on no-op runs, once-per-run accounts-cache invalidation from the status endpoint, and that skipped/no-token accounts cannot prematurely finalize a run that still has live refresh tasks.
- MCP helper tests are optional and auto-skip when the external `mcp` Python package is not installed.
- Instagram local image optimization tests are auto-skip when Pillow (`PIL`) is not installed.
- publishing task tests clear cache in setup to avoid stale lock-key side effects between tests.
- scheduler regressions cover read-only queue requests, independent dispatch by platform, safe retry and reconciliation, schedule replay and conflicts, and user-scoped queue actions.
- PostgreSQL upgrade coverage includes pre-existing scheduled rows. The owner backfill validates deferred foreign keys before creating the partial Instagram claim index, inside the same atomic migration.
- planning coverage now also checks invalid month validation, connected-account ownership boundaries, cross-user item isolation, and AI planner account-context isolation.

## Static Asset Cache-Busting
Production serves `/static/` through Nginx with `expires 30d`. Assets were referenced by plain
filename (`/static/dashboard/app.js`), so a returning visitor's browser kept the cached old
`app.js` / `styles.css` for up to 30 days after a deploy and never received frontend fixes.

- production static storage is now `core.storage.ResilientManifestStaticFilesStorage`, so
  `collectstatic` writes content-hashed filenames (`app.2b2c7201104f.js`) and `{% static %}`
  renders those hashed URLs; the filename changes whenever the file content changes, which
  makes the long `expires 30d` cache both safe and self-invalidating
- Django bypasses hashing while `DEBUG=True`, so `runserver` needs no manifest
- the storage falls back to the unhashed filename instead of raising when a name is missing
  from `staticfiles.json`. The stock Django storage raises `ValueError` there, which would
  turn an asset problem into a site-wide 500 (and would break every template-rendering test
  on a fresh checkout, since `staticfiles/` is gitignored). `collectstatic` writes both the
  hashed and the plain filename, so the fallback URL is always serveable — it costs only
  cache-busting for that one file, never availability
- `manifest_strict` is deliberately left at `True`: with it disabled Django hashes a
  missing-from-manifest file on the fly and returns a URL that 404s, because only
  `collectstatic` writes hashed copies to disk
- `deploy/prod/update_on_vps.sh` now builds, then runs `collectstatic` into the shared static
  volume, and only then starts the new containers. The previous order (`up -d` before
  `collectstatic`) left a window where the running app had no matching manifest
- after any deploy that changes JS/CSS, returning visitors get the new files automatically;
  no hard refresh is required any more

## Recent UI Performance Update
- Top navigation logo now uses a lightweight optimized icon asset (`postzyo-icon-optimized.png`) instead of the previous heavy lockup image.
- Logo loading priority is explicitly raised in base template (`preload` + eager image hints) so branding appears faster on first paint.
- Favicon was regenerated from the optimized icon to keep header assets consistent and lighter.

## Production Deployment Pack (KVM4 / multi-app friendly)
- Added `deploy/prod/docker-compose.prod.yml` for VPS production runs:
  - pins Compose project name to `postzyo` so direct Compose commands target the same live container set on VPS
  - resolves build context, `.env`, and Nginx config paths back to repo root even though the compose file lives under `deploy/prod/`
  - keeps Postgres/Redis private inside Docker network
  - serves Django via Gunicorn + app Nginx
  - runs a prefork publish worker with concurrency 4 and a gevent insights worker with concurrency 4
  - binds app Nginx on loopback (`127.0.0.1:${APP_INTERNAL_PORT}`) so host reverse proxy can route multiple apps safely on one VPS
  - includes persistent Docker volumes for DB, Redis AOF, static, and media
- local/dev `docker-compose.yml` also mounts shared media storage into web, worker, and nginx so uploaded media remains reachable across services
- Added `deploy/prod/install_on_vps.sh`:
  - installs Docker, Nginx, Certbot, and Git on Ubuntu VPS
  - clones/updates repo under `/opt/apps/postzyo`
  - creates `.env` from `.env.example` when missing, so real production secrets should be filled in immediately on first setup
  - writes host Nginx site for domain proxying
  - runs Docker production stack, migrations, collectstatic
  - attempts SSL certificate issuance with Certbot
- `deploy/prod/update_on_vps.sh` pulls and builds, then quiesces web, beat and workers, including an active legacy drain worker. It runs migrations and `collectstatic` using one-shot containers before restarting app services. The change introduces a brief maintenance window. Set `POSTZYO_STOP_TIMEOUT_SECONDS` at or above the maximum in-flight publish time.
- Docker image build leaves static collection to the installer and updater, using deployed environment settings and the shared static volume.
- Fresh app images prepare `/app/staticfiles` and `/app/media` with the Django service user's ownership before Docker mounts new volumes. The Celery broker transport is pinned to Kombu `5.6.2` for compatibility with `redis-py==5.2.1`. Check `publish` and `insights` deliveries through Redis after building a new release image.
- Upgraded app Nginx config (`deploy/nginx/nginx.conf`) to:
  - support larger upload limits
  - directly serve `/static/` and `/media/`
  - include safer proxy timeout settings for longer publish/insight requests

### VPS rollout for canonical host and publishing workers

1. Rehearse the PostgreSQL migration and queue handoff on isolated staging. Capture queue depth, in-flight publishing and a database backup. Use the updater to stop old web and worker writers before the migration. Keep beat stopped if migration or static collection fails.
2. Start with `PUBLISH_CLAIMS_ENABLED=False` to pause new sends while keeping reconciliation available. To canary, enable it and set `PUBLISH_CANARY_USER_IDS` to the comma-separated IDs of approved operators before restarting the affected services. An empty allowlist enables all otherwise eligible users when claims are on.
3. The publish worker consumes `publish` and the Insights worker consumes `insights`. For existing `celery` messages, inspect the old backlog and drain from the new code after migration with `docker compose -f deploy/prod/docker-compose.prod.yml --profile legacy-drain up -d worker-legacy`. Stop the drain worker when the legacy queue is empty. The migration preserves confirmed IDs and places legacy ambiguous outcomes into review. Preserve those records when rolling back.
4. For the host redirect, render a candidate using `bash deploy/nginx/render_host_proxy.sh production postzyo.com 18010`. Verify the current configuration with `nginx -T`, confirm the certificate covers apex and `www`, and review its renewal method. Verify apex is allowed by Django and registered for Meta and Google OAuth callbacks. Check checkout returns, existing media URLs and proxy HTTPS forwarding. The application updater leaves host proxy configuration untouched.
5. Apply a reviewed host config and verify it with `nginx -t` and one-hop redirect checks on representative paths with query strings. Check anonymous public HTML, the dashboard login redirect, robots and sitemap. In Search Console inspect priority pages, confirm the preferred host and submit the sitemap. Render staging host config with `staging` mode and an approved `POSTZYO_STAGING_HTPASSWD` file on a separate upstream port.
6. Test real Facebook and Instagram publishing on approved test assets before expanding a canary. Use per-platform confirmation, latency and duplicate counts from [the rollout plan](plan.md#p04-qa-canary-and-rollback).

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
- `postzyo-worker-insights-1`
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
  - `docker exec postzyo-db-1 pg_isready -U social_admin`
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

Important runtime meaning:
- because the compose file now declares `name: postzyo`, these direct commands target the live `postzyo-*` containers instead of accidentally creating a separate `prod-*` project

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
- production startup now requires an explicit `SECRET_KEY`; debug-only local runs can generate an ephemeral key, but placeholder production secrets are rejected
- production startup now also requires `FERNET_KEY` or `FERNET_KEYS`, and encrypted fields support key rotation through multi-key decryption
- Added explicit production security env toggles in settings:
  - `SECURE_SSL_REDIRECT`
  - `SESSION_COOKIE_SECURE`
  - `CSRF_COOKIE_SECURE`
  - `SESSION_COOKIE_AGE`
  - `SESSION_EXPIRE_AT_BROWSER_CLOSE`
  - `SESSION_SAVE_EVERY_REQUEST`
  - `SESSION_COOKIE_HTTPONLY`
  - `SESSION_COOKIE_SAMESITE`
  - `CSRF_COOKIE_SAMESITE`
  - `SECURE_HSTS_SECONDS`
  - `SECURE_HSTS_INCLUDE_SUBDOMAINS`
  - `SECURE_HSTS_PRELOAD`
  - `SECURE_REFERRER_POLICY`
  - `SECURE_CROSS_ORIGIN_OPENER_POLICY`
  - `SECURE_CROSS_ORIGIN_RESOURCE_POLICY`
- Added proxy-aware defaults for VPS reverse-proxy deployment:
  - `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")` only when `TRUST_REVERSE_PROXY=True`
  - `USE_X_FORWARDED_HOST = True`
- Enabled baseline hardening headers:
  - `SECURE_CONTENT_TYPE_NOSNIFF = True`
  - `X_FRAME_OPTIONS = "DENY"`
- production container Nginx now also adds:
  - `Referrer-Policy: same-origin`
  - `Cross-Origin-Opener-Policy: same-origin`
  - `Permissions-Policy: camera=(), microphone=(), geolocation=()`
  - `server_tokens off`
- `/static/` and `/media/` locations now keep the same security headers as the parent server block instead of dropping them
- app containers run as a non-root user inside Docker
- Instagram/public-media fetch preflight now validates that media URLs use public http/https hosts only and rejects localhost/private-IP targets before any server-side request is made
- dashboard JS now escapes dynamic table, AI-insight, scheduler-assist, and planning content before using `innerHTML`, reducing XSS exposure from API/user/AI text
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
  - workspace/team RBAC above the current per-user ownership isolation
  - backup/restore + retention lifecycle for snapshots/media

Step 9:
- Business Layer
  - usage quotas / fair-use controls
  - AI credit accounting model
  - in-app onboarding checklist
  - health dashboard with one-click remediation actions

## Rate-Limit & Memory Hardening (Meta App-Usage + OOM)
This layer was added after a production incident where Meta `X-App-Usage` reached 142-144% (error `(#4) Application request limit reached` across all profiles) and gunicorn workers were recurringly OOM-killed (`Worker was sent SIGKILL`). All controls are additive and env-tunable; defaults preserve prior behavior.

Meta app-usage protection:
- `MetaClient._check_usage_headers` now parses `X-Business-Use-Case-Usage` by key (`call_count`/`total_cputime`/`total_time`) instead of flattening every numeric value, so `estimated_time_to_regain_access` (minutes) is no longer mistaken for a usage percentage; the regain minutes are cached separately (`meta_buc_regain_minutes`).
- usage peak cache TTL is now `META_USAGE_CACHE_TTL` (default `300s`) instead of a hardcoded `60s`, so a brief lull no longer makes the throttle forget a near-limit state on the rolling 1-hour window.
- `meta_app_usage_peak()` / `meta_app_over_budget()` helpers expose the shared cached app-usage signal.
- publishing self-throttle now hard-stops (defers via transient retry) when usage `>= META_APP_USAGE_STOP_PCT` (default `100`), instead of sleeping briefly and calling anyway.
- the heavy insights refresh (`analytics.tasks.refresh_account_insights_snapshot`) now defers when over budget and applies a long, quota-aware backoff (`META_RATE_LIMIT_RETRY_COUNTDOWN`, default `900s`) to rate-limit transients (`#4/#17/#32/#613`) instead of retrying the full fan-out at a flat 300s into a still-saturated window.
- `fetch_and_store_insights` paces (sleep-only, never raises) when usage is elevated, gated by `META_INSIGHTS_USAGE_THROTTLE` (default `True`).
- manual force-refresh-all (`POST /api/insights/force-refresh-all/`) now has a non-overridable app-usage gate (`429` when cached usage `>= FORCE_REFRESH_APP_USAGE_BLOCK_PCT`, default `90`) and a per-user cooldown (`FORCE_REFRESH_ALL_COOLDOWN_SECONDS`, default `1800s`) so repeated full-fleet runs cannot stack on the daily job and exhaust the shared app quota.
- the force-refresh-all inline (queue-down) fallback is bounded to `FORCE_REFRESH_INLINE_FALLBACK_MAX` accounts (default `5`) so a degraded broker cannot push the whole fleet through synchronous Meta pulls inside one web worker.
- per-account Meta call volume is the dominant quota driver; lower `DAILY_INSIGHTS_POST_STATS_LIMIT` / `DAILY_INSIGHTS_POST_LIMIT` in production to cut it.
- `DAILY_INSIGHTS_POST_STATS_LIMIT` default is now `15` (was `40`). Per-post stats are one Meta Graph call per post per account per refresh, so this is the single largest lever on app-usage: for an 81-profile fleet a full "Force Refresh All" drops from roughly 3,400 calls to roughly 1,400. Only the newest 15 posts per profile get live per-post metrics; older posts still appear in Published Posts and reuse their last cached stats. Deployments that pin this value in their own `.env` must update it there — the settings default does not override an explicit env value.

Memory / OOM protection:
- `analytics.tasks.prune_insight_snapshots` keeps the latest `INSIGHT_SNAPSHOT_RETENTION_PER_ACCOUNT` (default `30`) per account and deletes older rows in batches daily at `04:30` by default. The Overview also reads an older snapshot for the seven-day follower comparison when available.
- production gunicorn now recycles workers (`--max-requests 800 --max-requests-jitter 200`) to release accumulated RSS, and `--timeout` was raised to `310s` (just above the container Nginx `300s` proxy_read_timeout) so a slow-but-legitimate insight refresh completes instead of being SIGKILLed mid-request at `120s`.
- production keeps insight workload on a dedicated gevent worker with concurrency 4, independently of publishing.
- note: `CELERY_WORKER_MAX_TASKS_PER_CHILD` only recycles memory under the prefork pool; under gevent it is a no-op. If worker RSS still climbs after these changes, the recommended follow-up is a dedicated insights worker on `--pool=prefork --concurrency=4 --max-tasks-per-child=100 --max-memory-per-child`, and per-container `mem_limit` values tuned against live `docker stats`.

## Security Hardening (auth, payment, abuse)
Added after a focused security audit. All controls are additive and behavior-preserving by default.

- Login brute-force lockout: the password login view blocks an IP after `LOGIN_FAILURE_LIMIT` (default `10`) failed attempts within `LOGIN_FAILURE_WINDOW_SECONDS` (default `900`). The limiter is IP-based (the per-user throttle is a no-op for anonymous requests) and fails open on cache errors so real users are never wrongly locked out.
- Google OAuth account-takeover guard: Google email-matching login now refuses to sign into an existing **staff/superuser** account (those would unlock admin + runtime Meta-credential management). Regular operator accounts logging in via Google remains a supported flow; only privileged accounts must use their password.
- Celery subscription enforcement (`ENFORCE_SUBSCRIPTION_IN_TASKS`, default on) pauses publishing and skips daily insight refresh for lapsed operators. Publishing rechecks after `SUBSCRIPTION_HOLD_SECONDS` (default 6h). Ownerless deliveries remain blocked.
- Public Help form abuse controls: a hidden honeypot field silently drops bots, and submissions are per-IP rate limited (`HELP_FORM_RATE`, default `5/h`); over-limit/bot submissions return the same generic success page but send no email.
- AI endpoints (`ai_profile_insights`, `generate_ai_calendar_plan`) are now per-user throttled (`5/m`) and every OpenAI call is capped by `OPENAI_MAX_TOKENS` (default `1500`) to prevent shared-key cost drain.
- Razorpay: create-order and verify-payment are per-user throttled. An optional capture-status check (`RAZORPAY_VERIFY_CAPTURE`, default off) fetches the payment and requires `status=captured` plus matching order/amount/currency before activating a plan — closing the authorized-but-not-captured gap; it fails open on a Razorpay API hiccup so genuine payments are never blocked. Enable only after confirming the merchant is on auto-capture.
- Meta user OAuth token is now Fernet-encrypted in the cache (it was previously stored in plaintext in Redis for 30 days); decryption tolerates any pre-existing plaintext entries during the transition.
- Production startup now also requires `CACHE_BACKEND=redis` (a shared cache is mandatory for rate limiting, login lockout, OAuth state, and subscription-order recovery; LocMem would silently multiply limits by worker count).
- Django admin no longer renders the decrypted Meta page access token in the change form; the debug-token health cache key uses a full SHA-256 (no truncation) so distinct tokens can never collide.

## Business-Manager Page-Token Resync (reconnect OAuth-190 fix)
Added after a production incident where ~71 of an operator's connected profiles failed every insights/force-refresh pull with Meta `code=190 subcode=460` ("access token invalidated"), so `last_post_at` and snapshots went stale and "Force Refresh All" returned mostly failures — even after the operator reconnected Meta.

Root cause:
- `/me/accounts` only returns a user's DIRECTLY-managed pages (~8 for a Business-Manager operator). The rest of the granted pages are exposed only through the token's `granular_scopes.target_ids`.
- On reconnect the app previously re-minted per-page `access_token`s ONLY for the `/me/accounts` pages; the Business-Manager (catalog-discovered) pages kept their pre-reconnect tokens, which a fresh user-token re-auth invalidates → `code=190` on every later insight/publish call.
- The Accounts catalog marks a row "connected" when a `ConnectedAccount` row simply exists (it does not validate the stored token), so the UI looked healthy while the stored per-page tokens were dead.

Fix (additive; no schema change; reuses `is_active`):
- New `integrations.services.resync_all_page_tokens(user, user_access_token)` walks the FULL granted asset set (token `target_ids`) and re-mints every `ConnectedAccount.access_token` from the current user token. FB page tokens are fetched in quota-friendly batches (50 ids per Graph call, with per-id fallback on batch error) and each linked IG row is updated with its FB page token. Guarded by the shared app-usage budget (`meta_app_over_budget`) so it cannot re-trigger app-level rate-limit exhaustion, and it never deactivates rows (safe to re-run any time).
- The reconnect callback queues `integrations.tasks.resync_page_tokens_task` on transaction commit with the current OAuth generation. Confirmed revocations are scoped to affected profiles. An incomplete inventory preserves unverified grants. Budget or temporary API deferrals retry with backoff and appear as retrying or partial in sync status. Superseded tasks cannot rewrite newer tokens.
- New management command for immediate recovery without another reconnect (uses the stored `MetaUserToken`):
  - `python manage.py resync_page_tokens --user-id <id>` (or `--email <addr>`)
- Daily heavy insights self-heal (see "Daily Heavy Insights Automation"): a hard `code=190` failure now marks that account `is_active=False` so the nightly fan-out stops burning shared Meta app quota on dead tokens.

Operational note (large multi-page fleets):
- A single "Force Refresh All" already sweeps the whole fleet's Meta quota; do NOT run it repeatedly, and never run `check_meta_tokens` across a large fleet (it makes one Meta call per account and can push `X-App-Usage` past 100%, which fails ALL app calls with `code=4`). Prefer the nightly daily-heavy job; use `resync_page_tokens` for targeted token recovery.

## Maintenance Rule
This file must be updated whenever project behavior, workflow, automation, stored data, or important UI meaning changes.

If someone asks, "What does this project do?", this file should be the first source of truth.




