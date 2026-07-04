import logging
from datetime import timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.db import close_old_connections, transaction
from django.utils import timezone

from core.exceptions import MetaAPIError, MetaTransientError
from core.services.meta_client import meta_app_over_budget
from integrations.models import ConnectedAccount

from .models import BulkInsightRefreshRun, InsightSnapshot
from .services import fetch_and_store_insights

logger = logging.getLogger("analytics")

DAILY_HEAVY_COLLECTION_MODE = "daily_heavy"

OUTCOME_SUCCESS = {"stored", "skipped_existing"}
OUTCOME_FAILURE = {"missing", "failed"}

_RATE_LIMIT_MARKERS = (
    "request limit reached",
    "rate limit",
    "too many requests",
    "over budget",
    "code=4",
    "code=17",
    "code=32",
    "code=613",
)


def _transient_retry_countdown(exc: Exception) -> int | None:
    """Long, quota-aware backoff for app-level rate-limit transients; else None.

    Returning None means "use Celery's default_retry_delay" (existing behavior).
    """
    message = str(exc).lower()
    if any(marker in message for marker in _RATE_LIMIT_MARKERS):
        try:
            return int(getattr(settings, "META_RATE_LIMIT_RETRY_COUNTDOWN", 900) or 900)
        except (TypeError, ValueError):
            return 900
    return None


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
            # Pre-filter accounts that already have today's DAILY-HEAVY snapshot.
            # Scope to collection_mode so a user's manual/force refresh earlier today
            # (a non-daily_heavy snapshot) does not make the daily job skip them.
            already_done = set(
                InsightSnapshot.objects.filter(
                    account_id__in=batch_ids,
                    fetched_at__date=timezone.localdate(),
                    payload__metadata__collection_mode=DAILY_HEAVY_COLLECTION_MODE,
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

        # Subscription enforcement: don't spend Meta/OpenAI quota refreshing insights
        # for a lapsed owner. Counts as a benign skip so any bulk run still finalizes.
        if getattr(settings, "ENFORCE_SUBSCRIPTION_IN_TASKS", True):
            from accounts.models import is_user_subscription_active

            if not is_user_subscription_active(getattr(account, "user_id", None)):
                outcome = "skipped_existing"
                return {"status": "skipped_inactive_subscription", "account_id": account.id}

        if not force and _has_daily_heavy_snapshot(account):
            outcome = "skipped_existing"
            return {"status": "skipped_existing", "account_id": account.id}

        # App-level Meta rate-limit backpressure. If Meta already reports us at/over
        # the configured stop threshold, defer rather than spend more quota now.
        # Cache-miss (no recent usage signal, e.g. in tests) is treated as under budget.
        if meta_app_over_budget():
            raise MetaTransientError(
                f"Meta app usage over budget; deferring insights refresh for account {account.id}"
            )

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
        # Rate-limit / over-budget transients get a much longer backoff so we do not
        # retry the heavy fan-out back into a still-saturated rolling-hour window.
        countdown = _transient_retry_countdown(exc)
        target_id = account_id if account is None else account.id
        logger.warning(
            "daily heavy insights transient error account_id=%s retry=%s retry_in=%ss error=%s",
            target_id,
            self.request.retries + 1,
            countdown if countdown is not None else "default",
            str(exc),
        )
        if countdown is not None:
            raise self.retry(exc=exc, countdown=countdown)
        raise self.retry(exc=exc)
    except MetaAPIError as exc:
        logger.warning("daily heavy insights failed account_id=%s error=%s", account.id if account else account_id, str(exc))
        outcome = "failed"
        # Self-heal: a hard token invalidation (OAuth code=190) will keep failing every
        # run and burn shared Meta app quota across the whole fleet. Deactivate the row so
        # the daily/force fan-out stops hammering it. A reconnect or `resync_page_tokens`
        # re-mints a valid token and flips is_active back on (update_or_create sets it True).
        try:
            err = (getattr(exc, "payload", None) or {}).get("error", {})
            if account is not None and err.get("code") == 190:
                ConnectedAccount.objects.filter(id=account.id).update(is_active=False)
                logger.warning(
                    "deactivated account_id=%s: invalid token (code=190) — reconnect/resync needed",
                    account.id,
                )
        except Exception:  # noqa: BLE001 - self-heal must never mask the original failure
            pass
        return {"status": "failed", "account_id": account.id if account else account_id, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("daily heavy insights unexpected failure account_id=%s", account.id if account else account_id)
        outcome = "failed"
        return {"status": "failed", "account_id": account.id if account else account_id, "error": str(exc)}
    finally:
        _record_bulk_run_outcome(bulk_run_id, outcome or "")
        cache.delete(lock_key)
        close_old_connections()


@shared_task(name="analytics.tasks.prune_insight_snapshots")
def prune_insight_snapshots(retention_per_account: int | None = None, batch_size: int = 500):
    """Keep only the latest N InsightSnapshot rows per account; delete the rest.

    Only the most-recent snapshot per account is ever read for responses, so older
    rows are dead weight that bloat Postgres (and every multi-row reader). This task
    is purely additive: it touches no read path. Deletes in id-batches to bound memory.
    """
    close_old_connections()
    if retention_per_account is None:
        retention_per_account = getattr(settings, "INSIGHT_SNAPSHOT_RETENTION_PER_ACCOUNT", 30)
    try:
        retention_per_account = max(1, int(retention_per_account))
    except (TypeError, ValueError):
        retention_per_account = 30

    deleted_total = 0
    accounts_pruned = 0
    account_ids = list(InsightSnapshot.objects.values_list("account_id", flat=True).distinct())
    for account_id in account_ids:
        keep_ids = list(
            InsightSnapshot.objects.filter(account_id=account_id)
            .order_by("-fetched_at")
            .values_list("id", flat=True)[:retention_per_account]
        )
        stale_qs = InsightSnapshot.objects.filter(account_id=account_id).exclude(id__in=keep_ids)
        pruned_this_account = False
        while True:
            batch_ids = list(stale_qs.values_list("id", flat=True)[:batch_size])
            if not batch_ids:
                break
            deleted, _ = InsightSnapshot.objects.filter(id__in=batch_ids).delete()
            deleted_total += deleted
            pruned_this_account = True
            if len(batch_ids) < batch_size:
                break
        if pruned_this_account:
            accounts_pruned += 1

    logger.info(
        "prune_insight_snapshots done accounts_pruned=%s deleted=%s keep_per_account=%s",
        accounts_pruned, deleted_total, retention_per_account,
    )
    close_old_connections()
    return {
        "accounts_pruned": accounts_pruned,
        "deleted": deleted_total,
        "keep_per_account": retention_per_account,
    }
