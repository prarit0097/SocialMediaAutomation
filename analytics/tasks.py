import logging
from datetime import timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from celery import shared_task
from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone

from core.exceptions import MetaAPIError, MetaTransientError
from integrations.models import ConnectedAccount

from .models import BulkInsightRefreshRun, InsightSnapshot
from .services import fetch_and_store_insights

logger = logging.getLogger("analytics")

DAILY_HEAVY_COLLECTION_MODE = "daily_heavy"

OUTCOME_SUCCESS = {"stored", "skipped_existing"}
OUTCOME_FAILURE = {"missing", "failed"}


def _record_bulk_run_outcome(run_id: int | None, outcome: str) -> None:
    if not run_id or outcome not in (OUTCOME_SUCCESS | OUTCOME_FAILURE):
        return

    with transaction.atomic():
        run = BulkInsightRefreshRun.objects.select_for_update().filter(id=run_id).first()
        if not run or run.status != BulkInsightRefreshRun.STATUS_RUNNING:
            return

        if outcome in OUTCOME_SUCCESS:
            run.completed_count += 1
        else:
            run.failed_count += 1

        processed = run.completed_count + run.failed_count
        if processed >= run.queued_count:
            if run.failed_count > 0:
                run.status = BulkInsightRefreshRun.STATUS_COMPLETED_WITH_ERRORS
            else:
                run.status = BulkInsightRefreshRun.STATUS_COMPLETED
            run.finished_at = timezone.now()
        run.save(
            update_fields=[
                "completed_count",
                "failed_count",
                "status",
                "finished_at",
                "updated_at",
            ]
        )


def _collection_timezone() -> ZoneInfo:
    return ZoneInfo(settings.CELERY_TIMEZONE)


def _local_day_window(reference_time=None):
    current = reference_time or timezone.now()
    localized = current.astimezone(_collection_timezone())
    start_local = localized.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return localized, start_local.astimezone(dt_timezone.utc), end_local.astimezone(dt_timezone.utc)


def _has_daily_heavy_snapshot(account: ConnectedAccount, reference_time=None) -> bool:
    _, start_utc, end_utc = _local_day_window(reference_time)
    snapshots = InsightSnapshot.objects.filter(
        account=account,
        fetched_at__gte=start_utc,
        fetched_at__lt=end_utc,
    ).only("payload")
    for snapshot in snapshots:
        metadata = ((snapshot.payload or {}).get("metadata") or {})
        if metadata.get("collection_mode") == DAILY_HEAVY_COLLECTION_MODE:
            return True
    return False


def _daily_snapshot_metadata() -> dict:
    localized, _, _ = _local_day_window()
    return {
        "collection_mode": DAILY_HEAVY_COLLECTION_MODE,
        "collection_source": "celery_beat",
        "collection_timezone": settings.CELERY_TIMEZONE,
        "collection_local_date": localized.date().isoformat(),
        "post_limit": settings.DAILY_INSIGHTS_POST_LIMIT,
        "post_stats_limit": settings.DAILY_INSIGHTS_POST_STATS_LIMIT,
    }


@shared_task(name="analytics.tasks.queue_daily_heavy_insight_refresh")
def queue_daily_heavy_insight_refresh(force: bool = False):
    accounts = list(ConnectedAccount.objects.order_by("id"))
    queued = 0
    skipped = 0

    for account in accounts:
        if not account.access_token:
            skipped += 1
            continue
        if not force and _has_daily_heavy_snapshot(account):
            skipped += 1
            continue
        # Keep daily-heavy refresh in lower priority than user-facing publish jobs.
        refresh_account_insights_snapshot.apply_async(args=[account.id], kwargs={"force": force}, priority=1)
        queued += 1

    logger.info(
        "daily heavy insight refresh queued total_accounts=%s queued=%s skipped=%s force=%s",
        len(accounts),
        queued,
        skipped,
        force,
    )
    return {
        "total_accounts": len(accounts),
        "queued": queued,
        "skipped": skipped,
        "forced": bool(force),
    }


@shared_task(bind=True, max_retries=2, default_retry_delay=300, name="analytics.tasks.refresh_account_insights_snapshot")
def refresh_account_insights_snapshot(self, account_id: int, force: bool = False, bulk_run_id: int | None = None):
    close_old_connections()
    outcome = None
    account = ConnectedAccount.objects.filter(id=account_id).first()
    if not account:
        outcome = "missing"
        return {"status": "missing", "account_id": account_id}

    if not force and _has_daily_heavy_snapshot(account):
        outcome = "skipped_existing"
        return {"status": "skipped_existing", "account_id": account.id}

    try:
        data = fetch_and_store_insights(
            account,
            include_post_stats=True,
            post_limit=settings.DAILY_INSIGHTS_POST_LIMIT,
            post_stats_limit=settings.DAILY_INSIGHTS_POST_STATS_LIMIT,
            payload_metadata=_daily_snapshot_metadata(),
        )
        logger.info(
            "daily heavy insights stored account_id=%s snapshot_id=%s platform=%s",
            account.id,
            data.get("snapshot_id"),
            account.platform,
        )
        outcome = "stored"
        return {
            "status": "stored",
            "account_id": account.id,
            "platform": account.platform,
            "snapshot_id": data.get("snapshot_id"),
        }
    except MetaTransientError as exc:
        logger.warning(
            "daily heavy insights transient error account_id=%s retry=%s error=%s",
            account.id,
            self.request.retries + 1,
            str(exc),
        )
        raise self.retry(exc=exc)
    except MetaAPIError as exc:
        logger.warning("daily heavy insights failed account_id=%s error=%s", account.id, str(exc))
        outcome = "failed"
        return {"status": "failed", "account_id": account.id, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("daily heavy insights unexpected failure account_id=%s", account.id)
        outcome = "failed"
        return {"status": "failed", "account_id": account.id, "error": str(exc)}
    finally:
        _record_bulk_run_outcome(bulk_run_id, outcome or "")
        close_old_connections()
