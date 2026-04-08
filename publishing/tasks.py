import logging
import random
import re
from datetime import timedelta

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.db import DatabaseError, transaction
from django.core.cache import cache
from django.utils import timezone

from core.constants import (
    INSTAGRAM,
    POST_STATUS_FAILED,
    POST_STATUS_PENDING,
    POST_STATUS_PROCESSING,
    POST_STATUS_PUBLISHED,
)
from core.exceptions import MetaPermanentError, MetaTransientError

from .models import ScheduledPost
from .services import _check_meta_usage_and_throttle, is_invalid_token_error, publish_scheduled_post, token_reconnect_message

logger = logging.getLogger("publishing")
IG_PUBLISH_LANE_LOCK_PREFIX = "publishing:ig_lane"
IG_COOLDOWN_PREFIX = "publishing:ig_cd"
PUBLISH_TRANSIENT_ATTEMPT_KEY_PREFIX = "publishing:transient_attempts"


def _ig_lane_key(account_id) -> str:
    """Per-account lane lock: each IG account publishes independently."""
    return f"{IG_PUBLISH_LANE_LOCK_PREFIX}:{account_id}"


def _ig_cooldown_key(account_id) -> str:
    return f"{IG_COOLDOWN_PREFIX}:{account_id}"


def _is_ig_throttled_for_account(account_id) -> bool:
    return bool(cache.get(_ig_cooldown_key(account_id)))


def _publish_attempt_cache_key(post_id: int) -> str:
    return f"{PUBLISH_TRANSIENT_ATTEMPT_KEY_PREFIX}:{post_id}"


def _get_publish_attempts(post_id: int) -> int:
    try:
        return max(0, int(cache.get(_publish_attempt_cache_key(post_id)) or 0))
    except (TypeError, ValueError):
        return 0


def _bump_publish_attempts(post_id: int) -> int:
    attempts = _get_publish_attempts(post_id) + 1
    cache.set(_publish_attempt_cache_key(post_id), attempts, timeout=24 * 60 * 60)
    return attempts


def _clear_publish_attempts(post_id: int) -> None:
    cache.delete(_publish_attempt_cache_key(post_id))


IG_MAX_CONCURRENT_PUBLISH = 3  # Max IG posts actively publishing at once


def _count_ig_currently_processing() -> int:
    """Count IG posts already in PROCESSING state (actively publishing)."""
    return ScheduledPost.objects.filter(
        platform=INSTAGRAM,
        status=POST_STATUS_PROCESSING,
    ).count()


def _select_dispatchable_due_posts(posts: list[ScheduledPost], batch_size: int) -> list[ScheduledPost]:
    ig_processing = _count_ig_currently_processing()
    ig_slots = max(0, IG_MAX_CONCURRENT_PUBLISH - ig_processing)

    selected: list[ScheduledPost] = []
    ig_selected = 0
    for post in posts:
        if post.platform == INSTAGRAM:
            if _is_ig_throttled_for_account(post.account_id):
                continue
            if ig_selected >= ig_slots:
                continue
            ig_selected += 1
        selected.append(post)
        if len(selected) >= batch_size:
            break
    return selected


def _get_due_posts(batch_size: int = 50) -> list[ScheduledPost]:
    now = timezone.now()
    with transaction.atomic():
        # Over-fetch a little so IG rows currently under per-account cooldown
        # can be skipped without starving the dispatch batch.
        candidate_limit = max(batch_size * 3, batch_size)
        base_qs = ScheduledPost.objects.select_for_update(skip_locked=True).filter(
            status=POST_STATUS_PENDING,
            scheduled_for__lte=now,
        )
        candidate_posts = list(base_qs.order_by("scheduled_for")[:candidate_limit])
        due_posts = _select_dispatchable_due_posts(candidate_posts, batch_size)

        for post in due_posts:
            post.status = POST_STATUS_PROCESSING
            post.error_message = ""
            post.save(update_fields=["status", "error_message", "updated_at"])

    return due_posts


def _claim_due_posts_without_skip_locked(batch_size: int = 50) -> list[ScheduledPost]:
    now = timezone.now()
    candidate_limit = max(batch_size * 3, batch_size)
    fallback_qs = ScheduledPost.objects.select_related("account").filter(
        status=POST_STATUS_PENDING,
        scheduled_for__lte=now,
    )
    candidate_posts = _select_dispatchable_due_posts(
        list(fallback_qs.order_by("scheduled_for")[:candidate_limit]),
        batch_size,
    )
    claimed_ids: list[int] = []
    for post in candidate_posts:
        updated = ScheduledPost.objects.filter(id=post.id, status=POST_STATUS_PENDING).update(
            status=POST_STATUS_PROCESSING,
            error_message="",
            updated_at=timezone.now(),
        )
        if updated:
            claimed_ids.append(post.id)
    if not claimed_ids:
        return []
    return list(ScheduledPost.objects.select_related("account").filter(id__in=claimed_ids).order_by("scheduled_for"))


def _recover_stale_processing() -> int:
    """Auto-recover IG posts stuck in PROCESSING beyond the task hard limit.

    This runs inside process_due_posts (every minute) so stuck posts are
    found quickly without waiting for a user to open the scheduler page.
    """
    stale_minutes = max(12, int(getattr(settings, "CELERY_TASK_TIME_LIMIT", 480)) // 60 + 2)
    cutoff = timezone.now() - timedelta(minutes=stale_minutes)
    stale_ids = list(
        ScheduledPost.objects.filter(
            status=POST_STATUS_PROCESSING,
            updated_at__lt=cutoff,
        ).values_list("id", flat=True)[:20]
    )
    if stale_ids:
        ScheduledPost.objects.filter(id__in=stale_ids).update(
            status=POST_STATUS_PENDING,
            error_message="Recovered from stale processing state; auto re-queued.",
            scheduled_for=timezone.now() + timedelta(seconds=30),
            updated_at=timezone.now(),
        )
        logger.warning("auto-recovered stale processing posts count=%s ids=%s", len(stale_ids), stale_ids)
    return len(stale_ids)


@shared_task(name="publishing.tasks.process_due_posts")
def process_due_posts(run_inline: bool = False):
    _recover_stale_processing()

    try:
        due_posts = _get_due_posts()
    except DatabaseError:
        # Fallback for DBs that do not support skip_locked.
        due_posts = _claim_due_posts_without_skip_locked()

    ig_count = sum(1 for p in due_posts if p.platform == INSTAGRAM)
    non_ig_count = len(due_posts) - ig_count
    if ig_count:
        logger.info(
            "dispatching due posts ig=%s non_ig=%s (max_concurrent_ig=%s)",
            ig_count, non_ig_count, IG_MAX_CONCURRENT_PUBLISH,
        )

    ig_index = 0
    for post in due_posts:
        try:
            if run_inline:
                publish_post_task(post.id)
                continue
            # Small stagger between IG posts (10-20s) — with only 3 per
            # cycle this keeps them from creating containers at the same
            # instant while still publishing quickly.
            eta_delay = 0
            if post.platform == INSTAGRAM and ig_index > 0:
                eta_delay = ig_index * random.uniform(10.0, 20.0)
            if post.platform == INSTAGRAM:
                ig_index += 1
            publish_post_task.apply_async(
                args=[post.id], priority=9,
                countdown=eta_delay if eta_delay > 0 else None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "failed to dispatch post id=%s error=%s", post.id, exc,
            )
            # Reset to PENDING so the post isn't stuck in PROCESSING
            # if the task queue (Redis/Celery) was temporarily down.
            try:
                ScheduledPost.objects.filter(id=post.id, status=POST_STATUS_PROCESSING).update(
                    status=POST_STATUS_PENDING,
                    error_message="Task dispatch failed; auto re-queued.",
                    updated_at=timezone.now(),
                )
            except Exception:  # noqa: BLE001
                pass

    return {"queued": len(due_posts), "ig_dispatched": ig_count}


@shared_task(bind=True, max_retries=6, default_retry_delay=60, name="publishing.tasks.publish_post_task")
def publish_post_task(self, post_id: int):
    lock_key = f"publish_task_lock:{post_id}"
    lock_timeout = max(120, int(getattr(settings, "CELERY_TASK_TIME_LIMIT", 480)))
    if not cache.add(lock_key, timezone.now().isoformat(), timeout=lock_timeout):
        return {"status": "locked", "post_id": post_id}

    ig_lane_locked = False
    ig_lane_key = None
    post = None
    try:
        post = ScheduledPost.objects.select_related("account").filter(id=post_id).first()
        if not post:
            return {"status": "missing", "post_id": post_id}

        # Guard against duplicate queue deliveries or delayed retries after the
        # row was already finalized by another worker.
        if post.status == POST_STATUS_PUBLISHED:
            return {"status": "already_published", "post_id": post.id}
        if post.status == POST_STATUS_FAILED:
            return {"status": "already_failed", "post_id": post.id}
        if post.status not in {POST_STATUS_PENDING, POST_STATUS_PROCESSING}:
            return {"status": "skipped_state", "post_id": post.id, "state": post.status}

        if post.platform == INSTAGRAM:
            # Per-account lock: different IG accounts publish in parallel.
            # Only guards against duplicate publishes for the same account.
            ig_lane_key = _ig_lane_key(post.account_id)
            lane_ttl = max(120, int(getattr(settings, "IG_PUBLISH_LANE_TTL_SECONDS", 420)))
            lane_retry = max(30, int(getattr(settings, "IG_PUBLISH_LANE_RETRY_SECONDS", 60)))
            ig_lane_locked = cache.add(ig_lane_key, f"{post.id}:{timezone.now().isoformat()}", timeout=lane_ttl)
            if not ig_lane_locked:
                post.status = POST_STATUS_PENDING
                post.scheduled_for = timezone.now() + timedelta(seconds=lane_retry)
                post.error_message = ""
                post.save(update_fields=["status", "scheduled_for", "error_message", "updated_at"])
                logger.info("ig lane busy account=%s post id=%s retry_in=%s", post.account_id, post.id, lane_retry)
                return {"status": "requeued_lane_busy", "post_id": post.id, "retry_in": lane_retry}

            # Check if Meta is at high usage and self-throttle before attempting
            # to avoid pushing ourselves into rate limits.
            _check_meta_usage_and_throttle()

        external_post_id = publish_scheduled_post(post)
        post.status = POST_STATUS_PUBLISHED
        post.external_post_id = external_post_id
        post.error_message = ""
        post.published_at = timezone.now()
        post.save(update_fields=["status", "external_post_id", "error_message", "published_at", "updated_at"])
        _clear_publish_attempts(post.id)
        cache.delete(f"ig_skip_resumable:{post.id}")
        logger.info("post published id=%s external_post_id=%s", post.id, external_post_id)
    except MetaTransientError as exc:
        if post is None:
            logger.exception("transient publish failure before post load post_id=%s", post_id)
            raise
        now = timezone.now()
        message = str(exc).lower()
        # Rate-limited during status polling — the container was already
        # created, so this is not a real publish failure.  Don't count it
        # as heavily toward max_retries.
        is_poll_rate_limit = "status checks were rate-limited" in message
        is_rate_limit_error = is_poll_rate_limit or bool(re.search(r'code=(?:2|4|17|32|613)(?:\D|$)', message))
        # Video processing ERROR/FAILED is Meta-side flakiness (same video
        # succeeds on other accounts).  Don't exhaust the low retry cap —
        # keep retrying with backoff like rate-limit errors.
        is_video_processing_error = (
            "status_code=error" in message
            or "status_code=failed" in message
            or "media processing returned error" in message
            or "media processing returned failed" in message
        )
        attempts = _bump_publish_attempts(post.id)

        # Rate-limit and video-processing errors are ALWAYS temporary —
        # never permanently fail on the low retry cap.
        if not is_rate_limit_error and not is_video_processing_error and attempts > self.max_retries:
            post.status = POST_STATUS_FAILED
            post.error_message = str(exc)
            post.save(update_fields=["status", "error_message", "updated_at"])
            _clear_publish_attempts(post.id)
            logger.exception("post failed after retries id=%s", post.id)
            return {"status": "failed_after_retries", "post_id": post.id}

        # Safety cap: after many retries give up to avoid infinite loops.
        # Rate-limit: 30 attempts (~45+ min).
        # Video processing: 15 attempts (~30 min) then permanent fail.
        if is_video_processing_error and attempts > 15:
            # After 15 attempts Meta consistently fails to process the video
            # for this account.  Likely a persistent account-level issue.
            post.status = POST_STATUS_FAILED
            post.error_message = (
                f"Instagram video processing failed after {attempts} attempts. "
                f"This may be an account-level issue — try re-uploading the video "
                f"or posting from the Instagram app to verify the account works. "
                f"Last error: {exc}"
            )
            post.save(update_fields=["status", "error_message", "updated_at"])
            _clear_publish_attempts(post.id)
            cache.delete(f"ig_skip_resumable:{post.id}")
            logger.exception("post failed after video processing retries id=%s attempts=%s", post.id, attempts)
            return {"status": "failed_after_retries", "post_id": post.id}
        if is_rate_limit_error and attempts > 30:
            post.status = POST_STATUS_FAILED
            post.error_message = (
                f"Rate-limited by Meta after {attempts} retries over extended period. "
                f"Please retry manually later. Last error: {exc}"
            )
            post.save(update_fields=["status", "error_message", "updated_at"])
            _clear_publish_attempts(post.id)
            logger.exception("post failed after extended rate-limit retries id=%s attempts=%s", post.id, attempts)
            return {"status": "failed_after_retries", "post_id": post.id}

        # Container expired/error/failed — clear the cached creation_id so a
        # fresh container is created on the next attempt.
        if (
            "container expired" in message
            or "container will be recreated" in message
            or "status_code=error" in message
            or "status_code=expired" in message
            or ("code=24" in message and "2207006" in message)
        ):
            cache.delete(f"ig_creation:{post.id}")
            logger.info("cleared expired ig container cache post id=%s", post.id)

        if is_video_processing_error:
            # Video processing failed on Meta's side — create fresh container
            # with longer delay to give Meta's infra time to recover.
            countdown = min(300, 60 + attempts * 30) + random.randint(0, 30)
            # After 3+ failures with resumable upload, switch to URL-based
            # so Meta fetches the video itself (different processing path).
            if attempts >= 3:
                cache.set(f"ig_skip_resumable:{post.id}", "1", timeout=3600)
                logger.info("switching to URL-based upload for post id=%s after %s video errors", post.id, attempts)
            user_message = (
                f"Instagram video processing error (attempt {attempts}/15). "
                f"Auto-retry in {countdown}s with fresh upload."
            )
        elif is_poll_rate_limit:
            # Polling was rate-limited but container exists in cache.
            # Use progressively longer delays: start short, back off to 5 min.
            countdown = min(300, 45 + attempts * 20) + random.randint(0, 30)
            user_message = (
                f"Checking media status, retrying in {countdown}s. "
                f"(Container already created, waiting for Meta processing.)"
            )
        elif is_rate_limit_error:
            # Graph app/page rate-limit during actual API calls.
            # Progressively longer delays: start at 75s, back off up to 10 min.
            countdown = min(600, 45 + attempts * 30)
            cooldown_duration = min(180, 30 + attempts * 20)
            if post.platform == INSTAGRAM:
                cache.set(_ig_cooldown_key(post.account_id), timezone.now().isoformat(), timeout=cooldown_duration)
            user_message = (
                f"Meta is pacing requests. Auto-retry in {countdown}s. "
                f"(Attempt {attempts}, backing off to avoid further limits.)"
            )
        else:
            countdown = min(180, 30 + attempts * 20) + random.randint(0, 15)
            user_message = (
                f"Temporary Meta delay. Auto-retry in {countdown}s. "
                f"Last Meta response: {exc}"
            )
        post.status = POST_STATUS_PENDING
        post.scheduled_for = now + timedelta(seconds=countdown)
        post.error_message = user_message
        post.save(update_fields=["status", "scheduled_for", "error_message", "updated_at"])
        logger.warning("transient error post id=%s retry_in=%s attempts=%s", post.id, countdown, attempts)
        return {"status": "requeued_transient", "post_id": post.id, "retry_in": countdown}
    except MetaPermanentError as exc:
        if post is None:
            logger.exception("permanent publish failure before post load post_id=%s", post_id)
            return {"status": "failed_before_load", "post_id": post_id, "error": str(exc)}

        perm_message = str(exc)

        # 24-hour publishing limit: mark as pending and schedule for later
        # instead of failing, so posts auto-publish when the window resets.
        if "24-hour publishing limit" in perm_message.lower():
            # Block this account for 1 hour (actual reset is rolling 24h,
            # but 1h cooldown prevents hammering the limit check).
            cache.set(_ig_cooldown_key(post.account_id), timezone.now().isoformat(), timeout=3600)
            post.status = POST_STATUS_PENDING
            post.scheduled_for = timezone.now() + timedelta(hours=1)
            post.error_message = (
                "Instagram 24-hour publishing limit reached. "
                "Auto-retry in ~1 hour when quota resets."
            )
            post.save(update_fields=["status", "scheduled_for", "error_message", "updated_at"])
            logger.warning("ig 24h limit post id=%s account=%s, requeued +1h", post.id, post.account_id)
            return {"status": "requeued_quota_limit", "post_id": post.id}

        post.status = POST_STATUS_FAILED
        post.error_message = token_reconnect_message(post.account, exc) if is_invalid_token_error(exc) else perm_message
        post.save(update_fields=["status", "error_message", "updated_at"])
        _clear_publish_attempts(post.id)
        cache.delete(f"ig_skip_resumable:{post.id}")
        cache.delete(f"ig_creation:{post.id}")
        logger.exception("permanent error post id=%s", post.id)
    except SoftTimeLimitExceeded:
        # Celery killed the task for exceeding the soft time limit.  This
        # typically happens during IG media processing (wait-for-ready).
        # Requeue instead of failing so the next attempt can finish.
        if post is not None:
            retry_delay = 90
            post.status = POST_STATUS_PENDING
            post.scheduled_for = timezone.now() + timedelta(seconds=retry_delay)
            post.error_message = (
                f"Publishing timed out (Celery soft limit). Auto-retry in {retry_delay}s."
            )
            post.save(update_fields=["status", "scheduled_for", "error_message", "updated_at"])
            logger.warning("soft time limit exceeded post id=%s, requeued", post.id)
            return {"status": "requeued_timeout", "post_id": post.id, "retry_in": retry_delay}
        logger.exception("soft time limit exceeded before post load post_id=%s", post_id)
    except Exception as exc:  # noqa: BLE001
        if post is None:
            logger.exception("unexpected publish failure before post load post_id=%s", post_id)
            return {"status": "failed_before_load", "post_id": post_id, "error": str(exc)}
        post.status = POST_STATUS_FAILED
        post.error_message = str(exc)
        post.save(update_fields=["status", "error_message", "updated_at"])
        _clear_publish_attempts(post.id)
        logger.exception("unexpected publish failure post id=%s", post.id)
    finally:
        if ig_lane_locked and ig_lane_key:
            cache.delete(ig_lane_key)
        cache.delete(lock_key)

