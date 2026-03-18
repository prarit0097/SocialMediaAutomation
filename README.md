# Postzyo (Django + Celery)

Django-based internal admin app to connect Facebook + Instagram accounts, schedule posts, and view insights from one dashboard.

## What this app includes
- Meta OAuth connect flow for Facebook Pages + linked Instagram Business accounts
- Connected accounts listing API and dashboard table
- Post scheduling API and dashboard form
- Celery worker + beat for automatic scheduled publishing
- Daily automated heavy insights snapshot refresh for all connected profiles
- Profile-wise AI insights generation from stored snapshots (OpenAI)
- Insights API with snapshot caching and refresh throttling
- Single-admin auth using Django sessions
- Encrypted token storage at rest using Fernet-backed model field

## Stack
- Django 5.x
- PostgreSQL
- Celery + Redis + Celery Beat
- Django templates + lightweight JavaScript
- Gunicorn + Nginx deployment via Docker Compose

## Project layout
- `social_automation/` project settings + celery app
- `accounts/` login/logout
- `integrations/` Meta OAuth + connected accounts
- `publishing/` scheduled posts + Celery publishing tasks
- `analytics/` insights snapshot + rate-limited refresh
- `dashboard/` template pages
- `core/` constants, exceptions, encrypted field, Meta client

## 1) Prerequisites
- Python 3.10+
- PostgreSQL
- Redis
- Meta developer app with required permissions

## 2) Setup
```bash
python -m venv .venv
. .venv/Scripts/activate   # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Update `.env` values:
- `SECRET_KEY`
- `DATABASE_URL`
- `REDIS_URL`
- `CELERY_TIMEZONE` (default: `Asia/Kolkata` for 5:00 AM daily insights automation)
- `META_APP_ID`
- `META_APP_SECRET`
- `META_REDIRECT_URI` (default: `http://localhost:8000/auth/meta/callback`)
- optional `FERNET_KEY`
- optional automation tuning:
  - `DAILY_INSIGHTS_ENABLED=True`
  - `DAILY_INSIGHTS_SCHEDULE_HOUR=5`
  - `DAILY_INSIGHTS_SCHEDULE_MINUTE=0`
- `DAILY_INSIGHTS_POST_LIMIT=100`
- `DAILY_INSIGHTS_POST_STATS_LIMIT=40`
- `OPENAI_API_KEY` (required for AI Insights page)
- `OPENAI_MODEL` (default: `gpt-4o-mini`)
- `OPENAI_TIMEOUT_SECONDS` (default: `45`)

## 3) Database and admin user
```bash
python manage.py migrate
python manage.py createsuperuser
```

## 4) Run (local)
Terminal 1:
```bash
python manage.py runserver
```

Terminal 2:
```bash
celery -A social_automation worker -l info
```

Terminal 3:
```bash
celery -A social_automation beat -l info
```

Open:
- Login: `http://127.0.0.1:8000/login/`
- Dashboard: `http://127.0.0.1:8000/dashboard/`

## 5) Meta app configuration
Required scopes:
- `pages_show_list`
- `pages_read_engagement`
- `pages_manage_posts`
- `pages_read_user_content`
- `instagram_basic`
- `instagram_content_publish`
- `instagram_manage_insights`

Important:
- Instagram publish requires `media_url` (publicly accessible image URL)
- Your IG account must be Professional and linked to FB Page

## 6) Core endpoints
Auth / Integration:
- `GET /auth/meta/start` -> returns `{ auth_url }`
- `GET /auth/meta/callback` -> exchanges code, syncs accounts, redirects to dashboard

APIs (authenticated):
- `GET /api/accounts/`
- `POST /api/posts/schedule/`
- `GET /api/posts/scheduled/`
- `GET /api/insights/<account_id>/`
- `GET /api/insights/<account_id>/?refresh=1` (rate-limited)
- `POST /api/ai-insights/<account_id>/` (profile-wise AI report)

Sample schedule payload:
```json
{
  "account_id": 1,
  "platform": "facebook",
  "message": "Hello from Django scheduler",
  "scheduled_for": "2026-03-10T15:30:00Z"
}
```

Instagram example needs `media_url`.

## 7) Celery behavior
- Beat triggers `publishing.tasks.process_due_posts` every minute
- Beat triggers `analytics.tasks.queue_daily_heavy_insight_refresh` every day at `05:00` in `CELERY_TIMEZONE`
- Due posts move from `pending` to `processing`
- Per-post task publishes to Meta Graph
- Daily heavy insights refresh queues one task per connected account, stores fresh `InsightSnapshot` rows, and lets the dashboard use the latest cached snapshot by default
- Status transitions:
  - `pending -> processing -> published`
  - `processing -> failed` on final error
- Retry policy for transient errors:
  - exponential backoff
  - max 3 retries

## 8) Token health check command
```bash
python manage.py check_meta_tokens
```

## 9) Docker deployment
```bash
docker compose up --build
```

Services:
- web (Gunicorn)
- worker (Celery)
- beat (Celery Beat)
- db (PostgreSQL)
- redis
- nginx

## 10) Notes
- Existing legacy Node directories (`backend/`, `frontend/`) are not used by this Django app.
- For production, complete Meta App Review for required permissions.
- Keep `DEBUG=False` and strong secrets in production.

## 11) Codex MCP helpers
This repo now includes project-local MCP launchers and custom monitoring servers under `mcp_servers/`.

Included MCPs:
- `social-filesystem` for project files and temporary local logs
- `social-playwright` for browser-based Accounts / Scheduler / Insights validation
- `social-redis-celery` for Redis queue, Celery worker, daily-heavy refresh, and publishing pipeline monitoring
- `social-meta-insights` for cached snapshot summaries, stale profile detection, posting-gap detection, and FB vs IG comparison

Register them in the local Codex config with:

```powershell
powershell -ExecutionPolicy Bypass -File .\mcp_servers\setup_codex_mcp.ps1
```

