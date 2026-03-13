from __future__ import annotations

from datetime import timedelta

from django.core.cache import cache
from django.db.models import Max
from django.utils import timezone

from .models import ConnectedAccount


SYNC_CACHE_KEY_TEMPLATE = "meta_last_sync:{user_id}"
SYNC_FRESHNESS_WINDOW = timedelta(minutes=10)


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

    latest_updated_at = ConnectedAccount.objects.filter(is_active=True).aggregate(value=Max("updated_at")).get("value")
    return latest_updated_at


def build_account_sync_state(account, user_id: int | None) -> dict:
    if getattr(account, "is_active", True) is False:
        return {
            "is_sync_stale": True,
            "sync_state": "inactive",
            "sync_state_reason": (
                "This profile is inactive because it was not included in the latest Meta reconnect. "
                "Reconnect and select this profile again."
            ),
        }

    recent_sync_time = get_recent_sync_time(user_id)
    if not recent_sync_time:
        return {
            "is_sync_stale": False,
            "sync_state": "unknown",
            "sync_state_reason": "No recent Meta reconnect found for this session.",
        }

    window_start = recent_sync_time - SYNC_FRESHNESS_WINDOW
    if account.updated_at >= window_start:
        return {
            "is_sync_stale": False,
            "sync_state": "current",
            "sync_state_reason": "This account was refreshed in the most recent Meta reconnect.",
        }

    return {
        "is_sync_stale": True,
        "sync_state": "stale",
        "sync_state_reason": (
            "This account was not refreshed in the latest Meta reconnect. "
            "Its stored page token may be stale. Reconnect and then choose a currently synced profile."
        ),
    }
