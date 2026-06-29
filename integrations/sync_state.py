from __future__ import annotations

from datetime import timedelta

from django.core.cache import cache
from django.db.models import Max
from django.utils import timezone

from .models import ConnectedAccount


SYNC_CACHE_KEY_TEMPLATE = "meta_last_sync:{user_id}"
SYNC_FRESHNESS_WINDOW = timedelta(minutes=10)

# Sentinel so callers can pass an already-resolved recent_sync_time (including a
# legitimate None) and skip the per-account lookup.
_SYNC_TIME_UNSET = object()


def get_recent_sync_time(user_id: int | None):
    if user_id:
        payload = cache.get(SYNC_CACHE_KEY_TEMPLATE.format(user_id=user_id)) or {}
        synced_at_raw = payload.get("synced_at")
        if synced_at_raw:
            try:
                synced_at = timezone.datetime.fromisoformat(str(synced_at_raw).replace("Z", "+00:00"))
            except ValueError:
                synced_at = None
            if synced_at is not None:
                if timezone.is_naive(synced_at):
                    synced_at = timezone.make_aware(synced_at, timezone=timezone.utc)
                return synced_at

    if not user_id:
        return None

    latest_updated_at = (
        ConnectedAccount.objects.filter(is_active=True, user_id=user_id).aggregate(value=Max("updated_at")).get("value")
    )
    return latest_updated_at


def build_account_sync_state(account, user_id: int | None, recent_sync_time=_SYNC_TIME_UNSET) -> dict:
    if getattr(account, "is_active", True) is False:
        return {
            "is_sync_stale": True,
            "sync_state": "inactive",
            "sync_state_reason": (
                "This profile is inactive because it was not included in the latest Meta reconnect. "
                "Reconnect and select this profile again."
            ),
        }

    # Staleness is based on REAL token validity, not reconnect-recency. /me/accounts only
    # returns a user's directly-managed pages, so Business-Manager-managed pages (synced
    # via catalog discovery) legitimately are NOT refreshed on every reconnect — flagging
    # them "stale" (and blocking scheduling) by comparing updated_at to the last reconnect
    # was a false positive that hit most accounts in a Business-Manager setup. A genuinely
    # dead/revoked/expired token is still caught at schedule/publish time by the live
    # debug_token gate (_ensure_account_token_is_valid) and the Meta API error path.
    # `recent_sync_time` is accepted for call-site compatibility but no longer used here.
    expires_at = getattr(account, "token_expires_at", None)
    if expires_at is not None and expires_at <= timezone.now():
        return {
            "is_sync_stale": True,
            "sync_state": "token_expired",
            "sync_state_reason": (
                "This account's stored page token has expired. Reconnect to refresh it."
            ),
        }
    return {
        "is_sync_stale": False,
        "sync_state": "current",
        "sync_state_reason": "This account is connected.",
    }
