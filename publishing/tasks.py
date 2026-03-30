import logging
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
IG_PUBLISH_LANE_LOCK_KEY = "publishing:ig_publish_lane"
IG_GLOBAL_COOLDOWN_KEY = "publishing:ig_global_cooldown"
PUBLISH_TRANSIENT_ATTEMPT_KEY_PREFIX = "publishing:transient_attempts"


def _is_ig_globally_throttled() -> bool:
    return bool(cache.get(IG_GLOBAL_COOLDOWN_KEY))


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


def _limit_instagram_batch(posts: list[ScheduledPost]) -> list[ScheduledPost]:
    limited: list[ScheduledPost] = []
    ig_taken = False
    for post in posts:
        if post.platform == INSTAGRAM:
            if ig_taken:
                continue
            ig_taken = True
        limited.append(post)
    return limited


def _get_due_posts(batch_size: int = 20) -> list[ScheduledPost]:
    now = timezone.now()
    with transaction.atomic():
        base_qs = ScheduledPost.objects.select_for_update(skip_locked=True).filter(
            status=POST_STATUS_PENDING,
            scheduled_for__lte=now,
        )
        if _is_ig_globally_throttled():
            base_qs = base_qs.exclude(platform=INSTAGRAM)
        due_posts = _limit_instagram_batch(list(base_qs.order_by("scheduled_for")[:batch_size]))

        for post in due_posts:
            post.status = POST_STATUS_PROCESSING
            post.error_message = ""
            post.save(update_fields=["status", "error_message", "updated_at"])

    return due_posts


def _claim_due_posts_without_skip_locked(batch_size: int = 20) -> list[ScheduledPost]:
    now = timezone.now()
    fallback_qs = ScheduledPost.objects.select_related("account").filter(
        status=POST_STATUS_PENDING,
        scheduled_for__lte=now,
    )
    if _is_ig_globally_throttled():
        fallback_qs = fallback_qs.exclude(platform=INSTAGRAM)
    candidate_posts = _limit_instagram_batch(list(fallback_qs.order_by("scheduled_for")[:batch_size]))
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

    for post in due_posts:
        try:
            if run_inline:
                publish_post_task(post.id)
                continue
            # Keep due publishing jobs ahead of heavy background analytics work.
            publish_post_task.apply_async(args=[post.id], priority=9)
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
            # Task-level cooldown check: another IG post may have just
            # published or been rate-limited, setting the global cooldown
            # AFTER this task was already dispatched to the Celery queue.
            # Re-queue silently so the user never sees a rate-limit error.
            if _is_ig_globally_throttled():
                requeue_delay = 65
                post.status = POST_STATUS_PENDING
                post.scheduled_for = timezone.now() + timedelta(seconds=requeue_delay)
                post.error_message = ""
                post.save(update_fields=["status", "scheduled_for", "error_message", "updated_at"])
                logger.info("ig cooldown active at task level post id=%s retry_in=%s", post.id, requeue_delay)
                return {"status": "requeued_cooldown", "post_id": post.id, "retry_in": requeue_delay}

            lane_ttl = max(120, int(getattr(settings, "IG_PUBLISH_LANE_TTL_SECONDS", 420)))
            lane_retry = max(30, int(getattr(settings, "IG_PUBLISH_LANE_RETRY_SECONDS", 60)))
            ig_lane_locked = cache.add(IG_PUBLISH_LANE_LOCK_KEY, f"{post.id}:{timezone.now().isoformat()}", timeout=lane_ttl)
            if not ig_lane_locked:
                post.status = POST_STATUS_PENDING
                post.scheduled_for = timezone.now() + timedelta(seconds=lane_retry)
                post.error_message = ""
                post.save(update_fields=["status", "scheduled_for", "error_message", "updated_at"])
                logger.info("instagram lane busy post id=%s retry_in=%s", post.id, lane_retry)
                return {"status": "requeued_lane_busy", "post_id": post.id, "retry_in": lane_retry}

        external_post_id = publish_scheduled_post(post)
        post.status = POST_STATUS_PUBLISHED
        post.external_post_id = external_post_id
        post.error_message = ""
        post.published_at = timezone.now()
        post.save(update_fields=["status", "external_post_id", "error_message", "published_at", "updated_at"])
        _clear_publish_attempts(post.id)
        if post.platform == INSTAGRAM:
            # Proactive cooldown: IG media-ready polling burns ~12 API calls
            # per publish on the shared Meta App rate-limit bucket.  Without a
            # gap the next IG post (any account) would fire immediately and
            # hit the still-hot burst window → rate-limit error.  60s lets
            # Meta's per-app counter reset before the next IG publish starts.
            cache.set(IG_GLOBAL_COOLDOWN_KEY, timezone.now().isoformat(), timeout=60)
        logger.info("post published id=%s external_post_id=%s", post.id, external_post_id)
    except MetaTransientError as exc:
        if post is None:
            logger.exception("transient publish failure before post load post_id=%s", post_id)
            raise
        attempts = _bump_publish_attempts(post.id)
        now = timezone.now()
        if attempts > self.max_retries:
            post.status = POST_STATUS_FAILED
            post.error_message = str(exc)
            post.save(update_fields=["status", "error_message", "updated_at"])
            _clear_publish_attempts(post.id)
            logger.exception("post failed after retries id=%s", post.id)
            return {"status": "failed_after_retries", "post_id": post.id}
        message = str(exc).lower()
        if re.search(r'code=(?:2|4|17|32|613)(?:\D|$)', message):
            # Graph app/page rate-limit: moderate backoff, not exponential explosion.
            countdown = min(300, 40 + attempts * 35)
            global_cooldown = min(90, 20 + attempts * 15)
            if post.platform == INSTAGRAM:
                cache.set(IG_GLOBAL_COOLDOWN_KEY, timezone.now().isoformat(), timeout=global_cooldown)
            user_message = (
                f"Meta is pacing requests. Auto-retry in {countdown}s. "
                f"Last Meta response: {exc}"
            )
        else:
            countdown = min(300, 45 + attempts * 30)
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
        post.status = POST_STATUS_FAILED
        post.error_message = token_reconnect_message(post.account, exc) if is_invalid_token_error(exc) else str(exc)
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
        if ig_lane_locked:
            cache.delete(IG_PUBLISH_LANE_LOCK_KEY)
        cache.delete(lock_key)

