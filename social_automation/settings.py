from pathlib import Path

import environ
from celery.schedules import crontab

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
    OPENAI_TIMEOUT_SECONDS=(int, 45),
    CACHE_BACKEND=(str, "locmem"),
    CACHE_DEFAULT_TIMEOUT_SECONDS=(int, 300),
    INSIGHTS_RESPONSE_CACHE_TTL=(int, 90),
    ACCOUNTS_LIST_CACHE_TTL=(int, 20),
    META_REQUEST_RETRY_ATTEMPTS=(int, 2),
    META_POST_STATS_TIMEOUT=(int, 12),
    META_POST_STATS_RETRIES=(int, 2),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="django-insecure-change-me")
DEBUG = env("DEBUG")
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
    "analytics",
    "dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
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
META_GRAPH_VERSION = env("META_GRAPH_VERSION", default="v21.0")

FERNET_KEY = env("FERNET_KEY", default="")

REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = env("CELERY_TIMEZONE")
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_DEFAULT_PRIORITY = 5
CELERY_TASK_QUEUE_MAX_PRIORITY = 10
CELERY_TASK_ROUTES = {
    "publishing.tasks.process_due_posts": {"priority": 9},
    "publishing.tasks.publish_post_task": {"priority": 9},
    "analytics.tasks.queue_daily_heavy_insight_refresh": {"priority": 2},
    "analytics.tasks.refresh_account_insights_snapshot": {"priority": 1},
}
CELERY_BROKER_TRANSPORT_OPTIONS = {"queue_order_strategy": "priority"}
DAILY_INSIGHTS_ENABLED = env("DAILY_INSIGHTS_ENABLED")
DAILY_INSIGHTS_SCHEDULE_HOUR = env("DAILY_INSIGHTS_SCHEDULE_HOUR")
DAILY_INSIGHTS_SCHEDULE_MINUTE = env("DAILY_INSIGHTS_SCHEDULE_MINUTE")
DAILY_INSIGHTS_POST_LIMIT = env("DAILY_INSIGHTS_POST_LIMIT")
DAILY_INSIGHTS_POST_STATS_LIMIT = env("DAILY_INSIGHTS_POST_STATS_LIMIT")
OPENAI_API_KEY = env("OPENAI_API_KEY", default="")
OPENAI_MODEL = env("OPENAI_MODEL")
OPENAI_TIMEOUT_SECONDS = env("OPENAI_TIMEOUT_SECONDS")
INSIGHTS_RESPONSE_CACHE_TTL = env("INSIGHTS_RESPONSE_CACHE_TTL")
ACCOUNTS_LIST_CACHE_TTL = env("ACCOUNTS_LIST_CACHE_TTL")
META_REQUEST_RETRY_ATTEMPTS = env("META_REQUEST_RETRY_ATTEMPTS")
META_POST_STATS_TIMEOUT = env("META_POST_STATS_TIMEOUT")
META_POST_STATS_RETRIES = env("META_POST_STATS_RETRIES")
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

CACHE_BACKEND = env("CACHE_BACKEND").strip().lower()
CACHE_DEFAULT_TIMEOUT_SECONDS = env("CACHE_DEFAULT_TIMEOUT_SECONDS")

if CACHE_BACKEND == "redis":
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "TIMEOUT": CACHE_DEFAULT_TIMEOUT_SECONDS,
        }
    }
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
    },
}
