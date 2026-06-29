from pathlib import Path

import environ
from celery.schedules import crontab
from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    CELERY_TIMEZONE=(str, "Asia/Kolkata"),
    DAILY_INSIGHTS_ENABLED=(bool, True),
    DAILY_INSIGHTS_SCHEDULE_HOUR=(int, 5),
    DAILY_INSIGHTS_SCHEDULE_MINUTE=(int, 0),
    DAILY_INSIGHTS_POST_LIMIT=(int, 100),
    DAILY_INSIGHTS_POST_STATS_LIMIT=(int, 40),
    OPENAI_MODEL=(str, "gpt-4o-mini"),
    OPENAI_IMAGE_MODEL=(str, "gpt-image-1"),
    OPENAI_IMAGE_TIMEOUT_SECONDS=(int, 90),
    OPENAI_TIMEOUT_SECONDS=(int, 45),
    # AI provider: "openai" (default) or "openrouter". OpenRouter is OpenAI-compatible,
    # so DeepSeek (and other models) work via the same chat/completions path.
    AI_PROVIDER=(str, "openai"),
    OPENAI_BASE_URL=(str, "https://api.openai.com/v1"),
    OPENROUTER_BASE_URL=(str, "https://openrouter.ai/api/v1"),
    OPENROUTER_MODEL=(str, "deepseek/deepseek-chat"),
    GOOGLE_OAUTH_CLIENT_ID=(str, ""),
    GOOGLE_OAUTH_CLIENT_SECRET=(str, ""),
    GOOGLE_OAUTH_REDIRECT_URI=(str, ""),
    EMAIL_BACKEND=(str, "django.core.mail.backends.smtp.EmailBackend"),
    EMAIL_HOST=(str, ""),
    EMAIL_PORT=(int, 587),
    EMAIL_HOST_USER=(str, ""),
    EMAIL_HOST_PASSWORD=(str, ""),
    EMAIL_USE_TLS=(bool, True),
    EMAIL_USE_SSL=(bool, False),
    DEFAULT_FROM_EMAIL=(str, ""),
    HELP_SUPPORT_EMAIL=(str, "1995praritsidana@gmail.com"),
    RAZORPAY_KEY_ID=(str, ""),
    RAZORPAY_KEY_SECRET=(str, ""),
    RAZORPAY_CURRENCY=(str, "INR"),
    CACHE_BACKEND=(str, "locmem"),
    CACHE_DEFAULT_TIMEOUT_SECONDS=(int, 300),
    INSIGHTS_RESPONSE_CACHE_TTL=(int, 90),
    ACCOUNTS_LIST_CACHE_TTL=(int, 120),
    META_PAGES_CATALOG_CACHE_TTL=(int, 3600),
    META_REQUEST_RETRY_ATTEMPTS=(int, 3),
    META_POST_STATS_TIMEOUT=(int, 12),
    META_POST_STATS_RETRIES=(int, 2),
    META_IG_READY_TIMEOUT=(int, 360),
    META_IG_READY_POLL_INTERVAL=(int, 12),
    BULK_REFRESH_STALE_MINUTES=(int, 45),
    FORCE_REFRESH_IG_GUARD_MINUTES=(int, 20),
    FORCE_REFRESH_IG_GUARD_DUE_LIMIT=(int, 1),
    DB_CONN_MAX_AGE=(int, 60),
    DB_CONN_HEALTH_CHECKS=(bool, True),
    IG_PUBLISH_LANE_TTL_SECONDS=(int, 420),
    IG_PUBLISH_LANE_RETRY_SECONDS=(int, 60),
    CELERY_PUBLISH_RATE_LIMIT=(str, "180/m"),
    CELERY_INSIGHTS_REFRESH_RATE_LIMIT=(str, "90/m"),
    CELERY_WORKER_MAX_TASKS_PER_CHILD=(int, 200),
    CELERY_TASK_SOFT_TIME_LIMIT=(int, 540),
    CELERY_TASK_TIME_LIMIT=(int, 600),
    SECURE_SSL_REDIRECT=(bool, False),
    SESSION_COOKIE_SECURE=(bool, False),
    CSRF_COOKIE_SECURE=(bool, False),
    SESSION_COOKIE_AGE=(int, 2592000),
    SESSION_EXPIRE_AT_BROWSER_CLOSE=(bool, False),
    SESSION_SAVE_EVERY_REQUEST=(bool, False),
    SESSION_COOKIE_HTTPONLY=(bool, True),
    SESSION_COOKIE_SAMESITE=(str, "Lax"),
    CSRF_COOKIE_SAMESITE=(str, "Lax"),
    SECURE_HSTS_SECONDS=(int, 0),
    SECURE_HSTS_INCLUDE_SUBDOMAINS=(bool, False),
    SECURE_HSTS_PRELOAD=(bool, False),
    SECURE_REFERRER_POLICY=(str, "same-origin"),
    SECURE_CROSS_ORIGIN_OPENER_POLICY=(str, "same-origin"),
    SECURE_CROSS_ORIGIN_RESOURCE_POLICY=(str, "same-origin"),
    TRUST_REVERSE_PROXY=(bool, False),
    MAX_UPLOAD_FILE_BYTES=(int, 104857600),
    # --- Meta app-usage / rate-limit protection (additive, behavior-preserving) ---
    META_USAGE_CACHE_TTL=(int, 300),
    META_APP_USAGE_STOP_PCT=(int, 100),
    META_INSIGHTS_USAGE_THROTTLE=(bool, True),
    META_RATE_LIMIT_RETRY_COUNTDOWN=(int, 900),
    FORCE_REFRESH_APP_USAGE_BLOCK_PCT=(int, 90),
    FORCE_REFRESH_ALL_COOLDOWN_SECONDS=(int, 1800),
    FORCE_REFRESH_INLINE_FALLBACK_MAX=(int, 5),
    INSIGHT_SNAPSHOT_RETENTION_PER_ACCOUNT=(int, 30),
    INSIGHT_SNAPSHOT_PRUNE_ENABLED=(bool, True),
    INSIGHT_SNAPSHOT_PRUNE_HOUR=(int, 4),
    INSIGHT_SNAPSHOT_PRUNE_MINUTE=(int, 30),
    # --- Security hardening (additive) ---
    LOGIN_FAILURE_LIMIT=(int, 10),
    LOGIN_FAILURE_WINDOW_SECONDS=(int, 900),
    HELP_FORM_RATE=(str, "5/h"),
    OPENAI_MAX_TOKENS=(int, 1500),
    ENFORCE_SUBSCRIPTION_IN_TASKS=(bool, True),
    SUBSCRIPTION_HOLD_SECONDS=(int, 21600),
    RAZORPAY_VERIFY_CAPTURE=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env")

DEBUG = env("DEBUG")
SECRET_KEY = env("SECRET_KEY", default="")
if not SECRET_KEY and DEBUG:
    SECRET_KEY = get_random_secret_key()
if not SECRET_KEY:
    raise ImproperlyConfigured("SECRET_KEY must be configured.")
if not DEBUG and SECRET_KEY in {"change-me", "django-insecure-change-me"}:
    raise ImproperlyConfigured("SECRET_KEY must not use a known placeholder in production.")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["127.0.0.1", "localhost"])
CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS",
    default=["http://127.0.0.1:8000", "http://localhost:8000"],
)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_celery_beat",
    "accounts",
    "integrations",
    "publishing",
    "planning",
    "analytics",
    "dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "dashboard.middleware.SubscriptionAccessMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "social_automation.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.pixel_events",
            ],
        },
    },
]

WSGI_APPLICATION = "social_automation.wsgi.application"
ASGI_APPLICATION = "social_automation.asgi.application"

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
    )
}
DATABASES["default"]["CONN_MAX_AGE"] = env("DB_CONN_MAX_AGE")
DATABASES["default"]["CONN_HEALTH_CHECKS"] = env("DB_CONN_HEALTH_CHECKS")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", default="")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/login/"

META_APP_ID = env("META_APP_ID", default="")
META_APP_SECRET = env("META_APP_SECRET", default="")
META_REDIRECT_URI = env("META_REDIRECT_URI", default="http://localhost:8000/auth/meta/callback")
META_GRAPH_VERSION = env("META_GRAPH_VERSION", default="v22.0")

FERNET_KEY = env("FERNET_KEY", default="")
FERNET_KEYS = env.list("FERNET_KEYS", default=[])
if not FERNET_KEY and not FERNET_KEYS and not DEBUG:
    raise ImproperlyConfigured("Set FERNET_KEY or FERNET_KEYS before starting production.")

REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
# No code reads task results (no .get()/AsyncResult), so persisting a result key per
# task execution (per-minute beat + per-item fan-out) is dead weight in the same Redis
# used for broker/cache/sessions — and gets fsync'd to AOF on a memory-constrained VPS.
CELERY_TASK_IGNORE_RESULT = True
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = env("CELERY_TIMEZONE")
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_TASK_TRACK_STARTED = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_WORKER_MAX_TASKS_PER_CHILD = env("CELERY_WORKER_MAX_TASKS_PER_CHILD")
CELERY_TASK_SOFT_TIME_LIMIT = env("CELERY_TASK_SOFT_TIME_LIMIT")
CELERY_TASK_TIME_LIMIT = env("CELERY_TASK_TIME_LIMIT")
CELERY_TASK_DEFAULT_PRIORITY = 5
CELERY_TASK_QUEUE_MAX_PRIORITY = 10
CELERY_TASK_ROUTES = {
    "publishing.tasks.process_due_posts": {"priority": 9},
    "publishing.tasks.publish_post_task": {"priority": 9},
    "analytics.tasks.queue_daily_heavy_insight_refresh": {"priority": 2},
    "analytics.tasks.refresh_account_insights_snapshot": {"priority": 1},
}
CELERY_BROKER_TRANSPORT_OPTIONS = {"queue_order_strategy": "priority"}
CELERY_TASK_ANNOTATIONS = {
    "publishing.tasks.publish_post_task": {"rate_limit": env("CELERY_PUBLISH_RATE_LIMIT")},
    "analytics.tasks.refresh_account_insights_snapshot": {"rate_limit": env("CELERY_INSIGHTS_REFRESH_RATE_LIMIT")},
}
DAILY_INSIGHTS_ENABLED = env("DAILY_INSIGHTS_ENABLED")
DAILY_INSIGHTS_SCHEDULE_HOUR = env("DAILY_INSIGHTS_SCHEDULE_HOUR")
DAILY_INSIGHTS_SCHEDULE_MINUTE = env("DAILY_INSIGHTS_SCHEDULE_MINUTE")
DAILY_INSIGHTS_POST_LIMIT = env("DAILY_INSIGHTS_POST_LIMIT")
DAILY_INSIGHTS_POST_STATS_LIMIT = env("DAILY_INSIGHTS_POST_STATS_LIMIT")
OPENAI_API_KEY = env("OPENAI_API_KEY", default="")
OPENAI_MODEL = env("OPENAI_MODEL")
OPENAI_IMAGE_MODEL = env("OPENAI_IMAGE_MODEL")
OPENAI_IMAGE_TIMEOUT_SECONDS = env("OPENAI_IMAGE_TIMEOUT_SECONDS")
OPENAI_TIMEOUT_SECONDS = env("OPENAI_TIMEOUT_SECONDS")
AI_PROVIDER = env("AI_PROVIDER")
OPENAI_BASE_URL = env("OPENAI_BASE_URL")
OPENROUTER_API_KEY = env("OPENROUTER_API_KEY", default="")
OPENROUTER_BASE_URL = env("OPENROUTER_BASE_URL")
OPENROUTER_MODEL = env("OPENROUTER_MODEL")
GOOGLE_OAUTH_CLIENT_ID = env("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_OAUTH_CLIENT_SECRET = env("GOOGLE_OAUTH_CLIENT_SECRET")
GOOGLE_OAUTH_REDIRECT_URI = env("GOOGLE_OAUTH_REDIRECT_URI")
EMAIL_BACKEND = env("EMAIL_BACKEND")
EMAIL_HOST = env("EMAIL_HOST")
EMAIL_PORT = env("EMAIL_PORT")
EMAIL_HOST_USER = env("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")
EMAIL_USE_TLS = env("EMAIL_USE_TLS")
EMAIL_USE_SSL = env("EMAIL_USE_SSL")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL") or EMAIL_HOST_USER or "Postzyo Support <no-reply@postzyo.com>"
HELP_SUPPORT_EMAIL = env("HELP_SUPPORT_EMAIL")
RAZORPAY_KEY_ID = env("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = env("RAZORPAY_KEY_SECRET")
RAZORPAY_CURRENCY = env("RAZORPAY_CURRENCY")
INSIGHTS_RESPONSE_CACHE_TTL = env("INSIGHTS_RESPONSE_CACHE_TTL")
ACCOUNTS_LIST_CACHE_TTL = env("ACCOUNTS_LIST_CACHE_TTL")
META_PAGES_CATALOG_CACHE_TTL = env("META_PAGES_CATALOG_CACHE_TTL")
META_REQUEST_RETRY_ATTEMPTS = env("META_REQUEST_RETRY_ATTEMPTS")
META_POST_STATS_TIMEOUT = env("META_POST_STATS_TIMEOUT")
META_POST_STATS_RETRIES = env("META_POST_STATS_RETRIES")
META_IG_READY_TIMEOUT = env("META_IG_READY_TIMEOUT")
META_IG_READY_POLL_INTERVAL = env("META_IG_READY_POLL_INTERVAL")
IG_PUBLISH_LANE_TTL_SECONDS = env("IG_PUBLISH_LANE_TTL_SECONDS")
IG_PUBLISH_LANE_RETRY_SECONDS = env("IG_PUBLISH_LANE_RETRY_SECONDS")
BULK_REFRESH_STALE_MINUTES = env("BULK_REFRESH_STALE_MINUTES")
FORCE_REFRESH_IG_GUARD_MINUTES = env("FORCE_REFRESH_IG_GUARD_MINUTES")
FORCE_REFRESH_IG_GUARD_DUE_LIMIT = env("FORCE_REFRESH_IG_GUARD_DUE_LIMIT")
MAX_UPLOAD_FILE_BYTES = env("MAX_UPLOAD_FILE_BYTES")
META_USAGE_CACHE_TTL = env("META_USAGE_CACHE_TTL")
META_APP_USAGE_STOP_PCT = env("META_APP_USAGE_STOP_PCT")
META_INSIGHTS_USAGE_THROTTLE = env("META_INSIGHTS_USAGE_THROTTLE")
META_RATE_LIMIT_RETRY_COUNTDOWN = env("META_RATE_LIMIT_RETRY_COUNTDOWN")
FORCE_REFRESH_APP_USAGE_BLOCK_PCT = env("FORCE_REFRESH_APP_USAGE_BLOCK_PCT")
FORCE_REFRESH_ALL_COOLDOWN_SECONDS = env("FORCE_REFRESH_ALL_COOLDOWN_SECONDS")
FORCE_REFRESH_INLINE_FALLBACK_MAX = env("FORCE_REFRESH_INLINE_FALLBACK_MAX")
INSIGHT_SNAPSHOT_RETENTION_PER_ACCOUNT = env("INSIGHT_SNAPSHOT_RETENTION_PER_ACCOUNT")
INSIGHT_SNAPSHOT_PRUNE_ENABLED = env("INSIGHT_SNAPSHOT_PRUNE_ENABLED")
INSIGHT_SNAPSHOT_PRUNE_HOUR = env("INSIGHT_SNAPSHOT_PRUNE_HOUR")
INSIGHT_SNAPSHOT_PRUNE_MINUTE = env("INSIGHT_SNAPSHOT_PRUNE_MINUTE")
LOGIN_FAILURE_LIMIT = env("LOGIN_FAILURE_LIMIT")
LOGIN_FAILURE_WINDOW_SECONDS = env("LOGIN_FAILURE_WINDOW_SECONDS")
HELP_FORM_RATE = env("HELP_FORM_RATE")
OPENAI_MAX_TOKENS = env("OPENAI_MAX_TOKENS")
ENFORCE_SUBSCRIPTION_IN_TASKS = env("ENFORCE_SUBSCRIPTION_IN_TASKS")
SUBSCRIPTION_HOLD_SECONDS = env("SUBSCRIPTION_HOLD_SECONDS")
RAZORPAY_VERIFY_CAPTURE = env("RAZORPAY_VERIFY_CAPTURE")
CELERY_BEAT_SCHEDULE = {
    "process-due-posts-every-minute": {
        "task": "publishing.tasks.process_due_posts",
        "schedule": crontab(minute="*"),
    }
}
if DAILY_INSIGHTS_ENABLED:
    CELERY_BEAT_SCHEDULE["queue-daily-heavy-insight-refresh"] = {
        "task": "analytics.tasks.queue_daily_heavy_insight_refresh",
        "schedule": crontab(hour=DAILY_INSIGHTS_SCHEDULE_HOUR, minute=DAILY_INSIGHTS_SCHEDULE_MINUTE),
    }
if INSIGHT_SNAPSHOT_PRUNE_ENABLED:
    CELERY_BEAT_SCHEDULE["prune-insight-snapshots"] = {
        "task": "analytics.tasks.prune_insight_snapshots",
        "schedule": crontab(hour=INSIGHT_SNAPSHOT_PRUNE_HOUR, minute=INSIGHT_SNAPSHOT_PRUNE_MINUTE),
    }

CACHE_BACKEND = env("CACHE_BACKEND").strip().lower()
CACHE_DEFAULT_TIMEOUT_SECONDS = env("CACHE_DEFAULT_TIMEOUT_SECONDS")

if CACHE_BACKEND == "redis":
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "TIMEOUT": CACHE_DEFAULT_TIMEOUT_SECONDS,
            "OPTIONS": {
                "socket_connect_timeout": 5,
                "socket_timeout": 5,
            },
        }
    }
    # Store sessions in Redis instead of DB — avoids per-request DB writes.
    SESSION_ENGINE = "django.contrib.sessions.backends.cache"
    SESSION_CACHE_ALIAS = "default"
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "social-automation-cache",
            "TIMEOUT": CACHE_DEFAULT_TIMEOUT_SECONDS,
        }
    }

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "loggers": {
        "publishing": {"handlers": ["console"], "level": "INFO"},
        "integrations": {"handlers": ["console"], "level": "INFO"},
        "analytics": {"handlers": ["console"], "level": "INFO"},
        "meta_client": {"handlers": ["console"], "level": "INFO"},
    },
}

SECURE_SSL_REDIRECT = env("SECURE_SSL_REDIRECT")
SESSION_COOKIE_SECURE = env("SESSION_COOKIE_SECURE")
CSRF_COOKIE_SECURE = env("CSRF_COOKIE_SECURE")
SESSION_COOKIE_AGE = env("SESSION_COOKIE_AGE")
SESSION_EXPIRE_AT_BROWSER_CLOSE = env("SESSION_EXPIRE_AT_BROWSER_CLOSE")
SESSION_SAVE_EVERY_REQUEST = env("SESSION_SAVE_EVERY_REQUEST")
SESSION_COOKIE_HTTPONLY = env("SESSION_COOKIE_HTTPONLY")
SESSION_COOKIE_SAMESITE = env("SESSION_COOKIE_SAMESITE")
CSRF_COOKIE_SAMESITE = env("CSRF_COOKIE_SAMESITE")
SECURE_HSTS_SECONDS = env("SECURE_HSTS_SECONDS")
SECURE_HSTS_INCLUDE_SUBDOMAINS = env("SECURE_HSTS_INCLUDE_SUBDOMAINS")
SECURE_HSTS_PRELOAD = env("SECURE_HSTS_PRELOAD")
SECURE_REFERRER_POLICY = env("SECURE_REFERRER_POLICY")
SECURE_CROSS_ORIGIN_OPENER_POLICY = env("SECURE_CROSS_ORIGIN_OPENER_POLICY")
SECURE_CROSS_ORIGIN_RESOURCE_POLICY = env("SECURE_CROSS_ORIGIN_RESOURCE_POLICY")
TRUST_REVERSE_PROXY = env("TRUST_REVERSE_PROXY")
# Number of trusted reverse-proxy hops that append to X-Forwarded-For (VPS = host
# nginx + container nginx = 2). Used by core.throttle.client_ip to pick the real,
# spoof-resistant client IP.
TRUSTED_PROXY_HOPS = env.int("TRUSTED_PROXY_HOPS", default=2)
if TRUST_REVERSE_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True
else:
    USE_X_FORWARDED_HOST = False

# SECURE_SSL_REDIRECT behind a TLS-terminating proxy needs SECURE_PROXY_SSL_HEADER
# (set only when TRUST_REVERSE_PROXY=True). Without it request.is_secure() is always
# False and SecurityMiddleware redirects every request -> infinite HTTPS loop that
# takes the whole site down. Fail fast at boot instead.
if not DEBUG and SECURE_SSL_REDIRECT and not TRUST_REVERSE_PROXY:
    raise ImproperlyConfigured(
        "SECURE_SSL_REDIRECT=True requires TRUST_REVERSE_PROXY=True behind a TLS-terminating "
        "reverse proxy; otherwise Django cannot detect HTTPS and will redirect-loop."
    )

if not DEBUG and (not SESSION_COOKIE_SECURE or not CSRF_COOKIE_SECURE):
    raise ImproperlyConfigured("SESSION_COOKIE_SECURE and CSRF_COOKIE_SECURE must be enabled in production.")

# A shared cache is required in production: per-IP/per-user rate limits, login
# lockout, OAuth state, and subscription-order recovery all depend on it. LocMem
# is per-process, so multi-worker prod would silently multiply every limit by the
# worker count and split OAuth/order state across workers.
if not DEBUG and CACHE_BACKEND != "redis":
    raise ImproperlyConfigured(
        "CACHE_BACKEND=redis is required in production (rate limiting, login lockout, "
        "OAuth state, and subscription-order recovery rely on a shared cache)."
    )

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
