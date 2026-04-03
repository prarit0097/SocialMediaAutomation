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
from .services import is_invalid_token_error, publish_scheduled_post, token_reconnect_message

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


def _select_dispatchable_due_posts(posts: list[ScheduledPost], batch_size: int) -> list[ScheduledPost]:
    selected: list[ScheduledPost] = []
    for post in posts:
        if post.platform == INSTAGRAM and _is_ig_throttled_for_account(post.account_id):
            continue
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


@shared_task(name="publishing.tasks.process_due_posts")
def process_due_posts(run_inline: bool = False):
    try:
        due_posts = _get_due_posts()
    except DatabaseError:
        # Fallback for DBs that do not support skip_locked.
        due_posts = _claim_due_posts_without_skip_locked()

    ig_index = 0
    for post in due_posts:
        try:
            if run_inline:
                publish_post_task(post.id)
                continue
            # Stagger IG tasks by 5-8s each so they don't burst Meta's
            # app-level rate limit.  15 posts × 6-8 API calls each = 90-120
            # calls; spreading over ~2 min keeps us well under 200/hour.
            eta_delay = 0
            if post.platform == INSTAGRAM:
                eta_delay = ig_index * random.uniform(5.0, 8.0)
                ig_index += 1
            publish_post_task.apply_async(
                args=[post.id], priority=9,
                countdown=eta_delay if eta_delay > 0 else None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "failed to dispatch post id=%s error=%s", post.id, exc,
            )

    return {"queued": len(due_posts)}


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

        external_post_id = publish_scheduled_post(post)
        post.status = POST_STATUS_PUBLISHED
        post.external_post_id = external_post_id
        post.error_message = ""
        post.published_at = timezone.now()
        post.save(update_fields=["status", "external_post_id", "error_message", "published_at", "updated_at"])
        _clear_publish_attempts(post.id)
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
        if is_poll_rate_limit:
            # Half-count: bump attempts by 0.5 via separate key so we
            # still cap runaway loops but give polling retries more room.
            attempts = _bump_publish_attempts(post.id)
            max_allowed = self.max_retries * 2  # twice the headroom
        else:
            attempts = _bump_publish_attempts(post.id)
            max_allowed = self.max_retries
        if attempts > max_allowed:
            post.status = POST_STATUS_FAILED
            post.error_message = str(exc)
            post.save(update_fields=["status", "error_message", "updated_at"])
            _clear_publish_attempts(post.id)
            logger.exception("post failed after retries id=%s", post.id)
            return {"status": "failed_after_retries", "post_id": post.id}
        # Container expired/not found — clear the cached creation_id so a
        # fresh container is created on the next attempt.
        if "container expired" in message or ("code=24" in message and "2207006" in message):
            cache.delete(f"ig_creation:{post.id}")
            logger.info("cleared expired ig container cache post id=%s", post.id)

        if is_poll_rate_limit:
            # Polling was rate-limited but container exists in cache.
            # Short retry — just need to re-check status and publish.
            countdown = min(90, 30 + attempts * 10) + random.randint(0, 15)
            user_message = (
                f"Checking media status, retrying in {countdown}s. "
                f"(Container already created, waiting for Meta processing.)"
            )
        elif re.search(r'code=(?:2|4|17|32|613)(?:\D|$)', message):
            # Graph app/page rate-limit during actual API calls.
            countdown = min(120, 20 + attempts * 15) + random.randint(0, 20)
            cooldown_duration = min(60, 10 + attempts * 10)
            if post.platform == INSTAGRAM:
                cache.set(_ig_cooldown_key(post.account_id), timezone.now().isoformat(), timeout=cooldown_duration)
            user_message = (
                f"Meta is pacing requests. Auto-retry in {countdown}s. "
                f"Last Meta response: {exc}"
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
        logger.warning("transient error post id=%s retry_in=%s", post.id, countdown)
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

