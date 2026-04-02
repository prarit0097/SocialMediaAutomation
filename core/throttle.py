"""Lightweight per-user cache-based throttle for Django views."""

from functools import wraps

from django.core.cache import cache
from django.http import JsonResponse


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
