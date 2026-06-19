"""Lightweight per-user cache-based throttle for Django views."""

from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse


def _parse_rate(rate: str) -> tuple[int, int]:
    count, period_char = int(rate.split("/")[0]), rate.split("/")[1]
    period = {"s": 1, "m": 60, "h": 3600}.get(period_char, 60)
    return count, period


def client_ip(request) -> str:
    """Best-effort client IP. Trusts X-Forwarded-For only behind a trusted proxy."""
    if getattr(settings, "TRUST_REVERSE_PROXY", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or "unknown"


def is_ip_rate_limited(request, scope: str, rate: str) -> bool:
    """Anonymous-safe per-IP limiter. Returns True when OVER the limit.

    Fails OPEN on any cache error so a cache hiccup never locks out real users.
    """
    count, period = _parse_rate(rate)
    key = f"ratelimit:{scope}:{client_ip(request)}"
    try:
        hits = cache.get(key, 0)
        if hits >= count:
            return True
        cache.set(key, hits + 1, timeout=period)
    except Exception:  # noqa: BLE001 - never block real users on cache failure
        return False
    return False


def _login_fail_key(request) -> str:
    return f"login_fail:{client_ip(request)}"


def is_login_locked(request) -> bool:
    limit = int(getattr(settings, "LOGIN_FAILURE_LIMIT", 10) or 10)
    try:
        return int(cache.get(_login_fail_key(request), 0)) >= limit
    except Exception:  # noqa: BLE001
        return False


def note_login_failure(request) -> None:
    window = int(getattr(settings, "LOGIN_FAILURE_WINDOW_SECONDS", 900) or 900)
    key = _login_fail_key(request)
    try:
        hits = int(cache.get(key, 0)) + 1
        cache.set(key, hits, timeout=window)
    except Exception:  # noqa: BLE001
        pass


def reset_login_failures(request) -> None:
    try:
        cache.delete(_login_fail_key(request))
    except Exception:  # noqa: BLE001
        pass


def throttle_per_user(rate: str, scope: str = ""):
    """Decorator that limits how often an authenticated user can call a view.

    *rate* is ``"<count>/<period>"`` where period is one of ``s``, ``m``, ``h``
    (second, minute, hour).  Example: ``"30/m"`` = 30 requests per minute.

    Uses Django's cache backend for storage — works across gunicorn workers
    when Redis is the cache.
    """
    count, period_char = int(rate.split("/")[0]), rate.split("/")[1]
    period = {"s": 1, "m": 60, "h": 3600}.get(period_char, 60)

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return view_func(request, *args, **kwargs)
            user_id = request.user.pk
            key = f"throttle:{scope or view_func.__name__}:{user_id}"
            hits = cache.get(key, 0)
            if hits >= count:
                return JsonResponse(
                    {"error": "Too many requests. Please wait a moment and try again."},
                    status=429,
                )
            cache.set(key, hits + 1, timeout=period)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator
