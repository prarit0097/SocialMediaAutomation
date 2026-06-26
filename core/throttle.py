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
    """Best-effort client IP. Trusts X-Forwarded-For only behind a trusted proxy.

    Takes the entry just left of the trusted proxy hops (the client IP as the
    outermost trusted proxy saw it), NOT the leftmost entry — the leftmost is
    fully attacker-spoofable (a client can prepend any value), which would let an
    attacker forge IPs to evade rate limits or frame another address.
    TRUSTED_PROXY_HOPS defaults to 2 (host nginx + container nginx on the VPS).
    """
    if getattr(settings, "TRUST_REVERSE_PROXY", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            parts = [p.strip() for p in forwarded.split(",") if p.strip()]
            if parts:
                hops = int(getattr(settings, "TRUSTED_PROXY_HOPS", 2) or 0)
                idx = len(parts) - 1 - hops
                if idx < 0:
                    idx = 0
                return parts[idx]
    return request.META.get("REMOTE_ADDR") or "unknown"


def _atomic_hit(key: str, period: int):
    """Atomically count one hit under *key* and return the new count.

    cache.add creates the counter with its window TTL only if absent; cache.incr is
    atomic on Redis, closing the get-then-set race where a burst slips past the limit.
    Returns None on cache error so callers can fail OPEN (never block real users).
    """
    try:
        cache.add(key, 0, timeout=period)
        return cache.incr(key)
    except ValueError:
        # Key expired between add and incr — recreate the window.
        try:
            cache.add(key, 1, timeout=period)
            return 1
        except Exception:  # noqa: BLE001
            return None
    except Exception:  # noqa: BLE001
        return None


def is_ip_rate_limited(request, scope: str, rate: str) -> bool:
    """Anonymous-safe per-IP limiter. Returns True when OVER the limit.

    Fails OPEN on any cache error so a cache hiccup never locks out real users.
    """
    count, period = _parse_rate(rate)
    key = f"ratelimit:{scope}:{client_ip(request)}"
    hits = _atomic_hit(key, period)
    return hits is not None and hits > count


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
    _atomic_hit(_login_fail_key(request), window)


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
            hits = _atomic_hit(key, period)
            if hits is not None and hits > count:
                return JsonResponse(
                    {"error": "Too many requests. Please wait a moment and try again."},
                    status=429,
                )
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator
