from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"


def bootstrap_django() -> None:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "social_automation.settings")

    import django
    from django.apps import apps

    if not apps.ready:
        django.setup()


def parse_datetime_like(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt_timezone.utc)
    if isinstance(value, str):
        normalized = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value.strip())
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_timezone.utc)
    return None


def iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def hours_since(value: datetime | None, now: datetime) -> float | None:
    if not value:
        return None
    delta = now - value
    return round(delta.total_seconds() / 3600, 2)


def normalize_platform_filter(platform: str | None) -> str | None:
    if not platform:
        return None
    normalized = platform.strip().lower()
    if normalized in {"all", "*"}:
        return None
    if normalized in {"facebook", "instagram"}:
        return normalized
    raise ValueError("platform must be one of: all, facebook, instagram")


@dataclass
class SnapshotBundle:
    account: object
    snapshot: object
    response: dict


def _latest_snapshots_by_account(account_ids: Iterable[int]) -> dict[int, object]:
    bootstrap_django()

    from analytics.models import InsightSnapshot

    latest_by_account: dict[int, object] = {}
    snapshots = (
        InsightSnapshot.objects.filter(account_id__in=list(account_ids))
        .select_related("account")
        .order_by("account_id", "-fetched_at")
    )
    for snapshot in snapshots:
        latest_by_account.setdefault(snapshot.account_id, snapshot)
    return latest_by_account


def load_cached_snapshot_response(account) -> SnapshotBundle | None:
    bootstrap_django()

    from analytics.services import build_insight_response
    from analytics.models import InsightSnapshot

    latest = InsightSnapshot.objects.filter(account=account).order_by("-fetched_at").first()
    if not latest:
        return None

    payload = latest.payload or {}
    published_posts = payload.get("published_posts") if "published_posts" in payload else None
    if published_posts == []:
        published_posts = None

    response = build_insight_response(
        account=account,
        platform=latest.platform,
        insights=payload.get("insights", []),
        snapshot_id=latest.id,
        fetched_at=latest.fetched_at,
        cached=True,
        published_posts=published_posts,
        include_generated_post_stats=False,
        total_post_share_override=payload.get("published_posts_count"),
    )
    return SnapshotBundle(account=account, snapshot=latest, response=response)


def resolve_linked_account(account):
    bootstrap_django()

    from integrations.models import ConnectedAccount

    if account.platform == "facebook" and account.ig_user_id:
        return ConnectedAccount.objects.filter(platform="instagram", page_id=account.ig_user_id).first()
    if account.platform == "instagram":
        return ConnectedAccount.objects.filter(platform="facebook", ig_user_id=account.page_id).order_by("-updated_at").first()
    return None


def latest_post_times_by_account(account_ids: Iterable[int]) -> dict[int, datetime | None]:
    bootstrap_django()

    from analytics.models import InsightSnapshot
    from django.db.models import Max
    from publishing.models import ScheduledPost

    account_ids = list(account_ids)
    latest_by_account: dict[int, datetime | None] = {
        row["account_id"]: row["latest_published_at"]
        for row in ScheduledPost.objects.filter(account_id__in=account_ids, published_at__isnull=False)
        .values("account_id")
        .annotate(latest_published_at=Max("published_at"))
    }

    unresolved = set(account_ids)
    snapshots = InsightSnapshot.objects.filter(account_id__in=account_ids).order_by("account_id", "-fetched_at")
    for snapshot in snapshots:
        account_id = snapshot.account_id
        if account_id not in unresolved:
            continue
        payload = snapshot.payload or {}
        latest_post = None
        for post in payload.get("published_posts") or []:
            published_at = parse_datetime_like(post.get("published_at")) or parse_datetime_like(post.get("scheduled_for"))
            if not published_at:
                continue
            if latest_post is None or published_at > latest_post:
                latest_post = published_at
        if latest_post is not None:
            current = latest_by_account.get(account_id)
            latest_by_account[account_id] = latest_post if current is None or latest_post > current else current
        unresolved.discard(account_id)
        if not unresolved:
            break
    return latest_by_account


def today_daily_heavy_status(reference_time=None) -> dict:
    bootstrap_django()

    from analytics.models import InsightSnapshot
    from analytics.tasks import DAILY_HEAVY_COLLECTION_MODE, _local_day_window
    from django.conf import settings
    from integrations.models import ConnectedAccount

    localized, start_utc, end_utc = _local_day_window(reference_time)
    accounts = list(ConnectedAccount.objects.order_by("id"))
    accounts_with_token = [account for account in accounts if bool(account.access_token)]
    latest_by_account: dict[int, dict] = {}
    snapshots = (
        InsightSnapshot.objects.filter(fetched_at__gte=start_utc, fetched_at__lt=end_utc)
        .only("id", "account_id", "platform", "fetched_at", "payload")
        .order_by("account_id", "-fetched_at")
    )

    for snapshot in snapshots:
        metadata = ((snapshot.payload or {}).get("metadata") or {})
        if metadata.get("collection_mode") != DAILY_HEAVY_COLLECTION_MODE:
            continue
        latest_by_account.setdefault(
            snapshot.account_id,
            {
                "snapshot_id": snapshot.id,
                "platform": snapshot.platform,
                "fetched_at": snapshot.fetched_at,
                "collection_source": metadata.get("collection_source"),
                "collection_local_date": metadata.get("collection_local_date"),
            },
        )

    completed_rows = []
    missing_rows = []
    for account in accounts_with_token:
        latest = latest_by_account.get(account.id)
        row = {
            "account_id": account.id,
            "platform": account.platform,
            "page_name": account.page_name,
            "page_id": account.page_id,
        }
        if latest:
            completed_rows.append(
                {
                    **row,
                    "snapshot_id": latest["snapshot_id"],
                    "fetched_at": iso_or_none(latest["fetched_at"]),
                    "collection_source": latest["collection_source"],
                    "collection_local_date": latest["collection_local_date"],
                }
            )
        else:
            missing_rows.append(row)

    completed_rows.sort(key=lambda row: row["fetched_at"] or "", reverse=True)
    missing_rows.sort(key=lambda row: (row["platform"], row["page_name"].lower()))
    return {
        "collection_mode": DAILY_HEAVY_COLLECTION_MODE,
        "collection_local_date": localized.date().isoformat(),
        "timezone": settings.CELERY_TIMEZONE,
        "scheduled_run_time": f"{settings.DAILY_INSIGHTS_SCHEDULE_HOUR:02d}:{settings.DAILY_INSIGHTS_SCHEDULE_MINUTE:02d}",
        "total_accounts": len(accounts),
        "accounts_with_tokens": len(accounts_with_token),
        "completed_accounts": len(completed_rows),
        "remaining_accounts": len(missing_rows),
        "completed": completed_rows,
        "missing": missing_rows,
    }
