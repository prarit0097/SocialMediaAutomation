import logging
from datetime import timedelta

from celery import shared_task
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


def _is_ig_globally_throttled() -> bool:
    return bool(cache.get(IG_GLOBAL_COOLDOWN_KEY))


def _get_due_posts(batch_size: int = 20) -> list[ScheduledPost]:
    now = timezone.now()
    with transaction.atomic():
        base_qs = ScheduledPost.objects.select_for_update(skip_locked=True).filter(
            status=POST_STATUS_PENDING,
            scheduled_for__lte=now,
        )
        if _is_ig_globally_throttled():
            base_qs = base_qs.exclude(platform=INSTAGRAM)
        due_posts = list(base_qs.order_by("scheduled_for")[:batch_size])

        for post in due_posts:
            post.status = POST_STATUS_PROCESSING
            post.error_message = ""
            post.save(update_fields=["status", "error_message", "updated_at"])

    return due_posts


@shared_task(name="publishing.tasks.process_due_posts")
def process_due_posts(run_inline: bool = False):
    try:
        due_posts = _get_due_posts()
    except DatabaseError:
        # Fallback for DBs that do not support skip_locked.
        now = timezone.now()
        fallback_qs = ScheduledPost.objects.filter(
            status=POST_STATUS_PENDING,
            scheduled_for__lte=now,
        )
        if _is_ig_globally_throttled():
            fallback_qs = fallback_qs.exclude(platform=INSTAGRAM)
        due_posts = list(fallback_qs.order_by("scheduled_for")[:20])
        for post in due_posts:
            post.status = POST_STATUS_PROCESSING
            post.error_message = ""
            post.save(update_fields=["status", "error_message", "updated_at"])

    for post in due_posts:
        if run_inline:
            publish_post_task(post.id)
            continue
        # Keep due publishing jobs ahead of heavy background analytics work.
        publish_post_task.apply_async(args=[post.id], priority=9)

    return {"queued": len(due_posts)}


@shared_task(bind=True, max_retries=6, default_retry_delay=60, name="publishing.tasks.publish_post_task")
def publish_post_task(self, post_id: int):
    lock_key = f"publish_task_lock:{post_id}"
    if not cache.add(lock_key, timezone.now().isoformat(), timeout=600):
        return {"status": "locked", "post_id": post_id}

    ig_lane_locked = False
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
            lane_ttl = max(120, int(getattr(settings, "IG_PUBLISH_LANE_TTL_SECONDS", 420)))
            lane_retry = max(30, int(getattr(settings, "IG_PUBLISH_LANE_RETRY_SECONDS", 60)))
            ig_lane_locked = cache.add(IG_PUBLISH_LANE_LOCK_KEY, f"{post.id}:{timezone.now().isoformat()}", timeout=lane_ttl)
            if not ig_lane_locked:
                post.status = POST_STATUS_PENDING
                post.scheduled_for = timezone.now() + timedelta(seconds=lane_retry)
                post.error_message = (
                    f"Instagram publish lane is busy. Auto-retry in {lane_retry}s to reduce Meta throttle risk."
                )
                post.save(update_fields=["status", "scheduled_for", "error_message", "updated_at"])
                logger.info("instagram lane busy post id=%s retry_in=%s", post.id, lane_retry)
                return {"status": "requeued_lane_busy", "post_id": post.id, "retry_in": lane_retry}

        external_post_id = publish_scheduled_post(post)
        post.status = POST_STATUS_PUBLISHED
        post.external_post_id = external_post_id
        post.error_message = ""
        post.published_at = timezone.now()
        post.save(update_fields=["status", "external_post_id", "error_message", "published_at", "updated_at"])
        logger.info("post published id=%s external_post_id=%s", post.id, external_post_id)
    except MetaTransientError as exc:
        attempts = self.request.retries + 1
        now = timezone.now()
        if attempts > self.max_retries:
            post.status = POST_STATUS_FAILED
            post.error_message = str(exc)
            post.save(update_fields=["status", "error_message", "updated_at"])
            logger.exception("post failed after retries id=%s", post.id)
            return
        message = str(exc).lower()
        if "code=4" in message or "code=17" in message or "code=32" in message or "code=613" in message:
            # Graph app/page rate-limit needs longer cool-down.
            countdown = min(1800, 2 ** attempts * 90)
            if post.platform == INSTAGRAM:
                cache.set(IG_GLOBAL_COOLDOWN_KEY, timezone.now().isoformat(), timeout=countdown)
        else:
            countdown = min(900, 2 ** attempts * 30)
        post.status = POST_STATUS_PENDING
        post.scheduled_for = now + timedelta(seconds=countdown)
        post.error_message = (
            f"Temporary Meta delay/throttle. Auto-retry in {countdown}s. "
            f"Last error: {exc}"
        )
        post.save(update_fields=["status", "scheduled_for", "error_message", "updated_at"])
        logger.warning("transient error post id=%s retry_in=%s", post.id, countdown)
        raise self.retry(exc=exc, countdown=countdown)
    except MetaPermanentError as exc:
        post.status = POST_STATUS_FAILED
        post.error_message = token_reconnect_message(post.account, exc) if is_invalid_token_error(exc) else str(exc)
        post.save(update_fields=["status", "error_message", "updated_at"])
        logger.exception("permanent error post id=%s", post.id)
    except Exception as exc:  # noqa: BLE001
        post.status = POST_STATUS_FAILED
        post.error_message = str(exc)
        post.save(update_fields=["status", "error_message", "updated_at"])
        logger.exception("unexpected publish failure post id=%s", post.id)
    finally:
        if ig_lane_locked:
            cache.delete(IG_PUBLISH_LANE_LOCK_KEY)
        cache.delete(lock_key)

