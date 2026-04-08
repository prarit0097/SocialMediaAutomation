import logging
from datetime import timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
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
            if run.failed_count > 0 or run.enqueue_failed > 0:
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
    return InsightSnapshot.objects.filter(
        account=account,
        fetched_at__gte=start_utc,
        fetched_at__lt=end_utc,
        payload__metadata__collection_mode=DAILY_HEAVY_COLLECTION_MODE,
    ).exists()


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
    # Use iterator + values_list to avoid loading all ConnectedAccount objects
    # into memory at once.  For 10k+ accounts this saves ~100 MB of RAM.
    BATCH = 500
    queued = 0
    skipped = 0
    total = 0

    account_qs = (
        ConnectedAccount.objects.filter(user__isnull=False)
        .order_by("id")
        .values_list("id", "access_token")
    )

    for batch_start in range(0, account_qs.count(), BATCH):
        batch_rows = list(account_qs[batch_start:batch_start + BATCH])
        batch_ids = [row[0] for row in batch_rows]
        total += len(batch_ids)

        if not force:
            # Pre-filter accounts that already have today's snapshot in bulk.
            already_done = set(
                InsightSnapshot.objects.filter(
                    account_id__in=batch_ids,
                    fetched_at__date=timezone.localdate(),
                ).values_list("account_id", flat=True).distinct()
            )
        else:
            already_done = set()

        for account_id, access_token in batch_rows:
            if not (str(access_token or "").strip()):
                skipped += 1
                continue
            if account_id in already_done:
                skipped += 1
                continue
            refresh_account_insights_snapshot.apply_async(
                args=[account_id], kwargs={"force": force}, priority=1,
            )
            queued += 1

    logger.info(
        "daily heavy insight refresh queued total_accounts=%s queued=%s skipped=%s force=%s",
        total, queued, skipped, force,
    )
    return {
        "total_accounts": total,
        "queued": queued,
        "skipped": skipped,
        "forced": bool(force),
    }


@shared_task(bind=True, max_retries=2, default_retry_delay=300, name="analytics.tasks.refresh_account_insights_snapshot")
def refresh_account_insights_snapshot(self, account_id: int, force: bool = False, bulk_run_id: int | None = None):
    close_old_connections()
    lock_key = f"insight_refresh_lock:{account_id}"
    lock_acquired = cache.add(lock_key, timezone.now().isoformat(), timeout=20 * 60)
    if not lock_acquired:
        outcome = "skipped_existing"
        _record_bulk_run_outcome(bulk_run_id, outcome)
        return {"status": "skipped_locked", "account_id": account_id}

    outcome = None
    account = None
    try:
        account = ConnectedAccount.objects.filter(id=account_id).first()
        if not account:
            outcome = "missing"
            return {"status": "missing", "account_id": account_id}

        if not force and _has_daily_heavy_snapshot(account):
            outcome = "skipped_existing"
            return {"status": "skipped_existing", "account_id": account.id}

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
        if account is None:
            logger.warning(
                "daily heavy insights transient error account_id=%s retry=%s error=%s",
                account_id,
                self.request.retries + 1,
                str(exc),
            )
            raise self.retry(exc=exc)
        logger.warning(
            "daily heavy insights transient error account_id=%s retry=%s error=%s",
            account.id,
            self.request.retries + 1,
            str(exc),
        )
        raise self.retry(exc=exc)
    except MetaAPIError as exc:
        logger.warning("daily heavy insights failed account_id=%s error=%s", account.id if account else account_id, str(exc))
        outcome = "failed"
        return {"status": "failed", "account_id": account.id if account else account_id, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("daily heavy insights unexpected failure account_id=%s", account.id if account else account_id)
        outcome = "failed"
        return {"status": "failed", "account_id": account.id if account else account_id, "error": str(exc)}
    finally:
        _record_bulk_run_outcome(bulk_run_id, outcome or "")
        cache.delete(lock_key)
        close_old_connections()
