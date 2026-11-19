import logging
import re
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, connection, transaction
from django.db.models import F, Q, Window
from django.db.models.functions import Coalesce, RowNumber
from django.utils import timezone

from accounts.models import UserProfile
from core.constants import FACEBOOK, INSTAGRAM, POST_STATUS_FAILED, POST_STATUS_PENDING, POST_STATUS_PROCESSING, POST_STATUS_PUBLISHED
from core.exceptions import MetaAPIError, MetaPublishOutcomeUnknown, MetaTransientError
from core.services.meta_client import MetaClient
from integrations.models import ConnectedAccount

from .models import BLOCKED, OUTCOME_UNKNOWN, RETRY_WAIT, WAITING_STATES, ScheduledPost
from .services import InstagramQuotaExhausted, InstagramQuotaProbeUnavailable, _check_meta_usage_and_throttle, is_invalid_token_error, publish_scheduled_post

logger = logging.getLogger("publishing")
IG_MAX_CONCURRENT_PUBLISH = 3
FB_MAX_OUTSTANDING_PUBLISH = 12
DISPATCH_LOCK_ID = 0x504F53545A594F
OUTCOME_MESSAGE = "Meta may have accepted this post. Check the destination and contact support before posting it again."


class ClaimLost(Exception):
    pass


class DeliveryDeferred(Exception):
    def __init__(self, reason, message, delay=None):
        super().__init__(message)
        self.reason = reason
        self.delay = delay


def _ig_cooldown_key(account_id):
    return f"publishing:ig_cd:{account_id}"


def _lease_until(now):
    return now + timedelta(seconds=max(120, settings.CELERY_TASK_TIME_LIMIT + 60))


def _due(now):
    if not settings.PUBLISH_CLAIMS_ENABLED:
        return ScheduledPost.objects.none()
    posts = ScheduledPost.objects.filter(
        status__in=WAITING_STATES, final_write_started_at__isnull=True, scheduled_for__lte=now,
    ).filter(Q(next_attempt_at__lte=now) | Q(next_attempt_at__isnull=True, status=POST_STATUS_PENDING))
    if settings.PUBLISH_CANARY_USER_IDS:
        posts = posts.filter(owner_id__in=settings.PUBLISH_CANARY_USER_IDS)
    return posts


def _dispatch_claim(post, now):
    try:
        with transaction.atomic():
            return _due(now).filter(pk=post.pk, claim_generation=post.claim_generation).update(
                status=POST_STATUS_PROCESSING, stage="dispatched", updated_at=now,
                claim_generation=F("claim_generation") + 1, lease_expires_at=now + timedelta(minutes=3),
            )
    except IntegrityError:
        # The partial unique index gives each Instagram account a single active claim.
        if post.platform != INSTAGRAM or not ScheduledPost.objects.filter(
            platform=INSTAGRAM, account_id=post.account_id, status=POST_STATUS_PROCESSING,
        ).exists():
            raise
        return 0


def _get_due_posts(batch_size=50):
    with transaction.atomic():
        if connection.vendor == "postgresql":
            # Size the batch once across both current and legacy beat deliveries.
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [DISPATCH_LOCK_ID])
                if not cursor.fetchone()[0]:
                    return []
        return _claim_dispatch_batch(batch_size)


def _claim_dispatch_batch(batch_size):
    now = timezone.now()
    active_ig = ScheduledPost.objects.filter(platform=INSTAGRAM, status=POST_STATUS_PROCESSING)
    active_fb = ScheduledPost.objects.filter(platform=FACEBOOK, status=POST_STATUS_PROCESSING)
    selected = []
    for platform, available in ((INSTAGRAM, IG_MAX_CONCURRENT_PUBLISH - active_ig.count()), (FACEBOOK, FB_MAX_OUTSTANDING_PUBLISH - active_fb.count())):
        limit = min(batch_size, available)
        if limit <= 0:
            continue
        candidates = _due(now).filter(platform=platform)
        if platform == INSTAGRAM:
            candidates = candidates.exclude(account_id__in=active_ig.values("account_id"))
        candidates = candidates.annotate(
            due_at=Coalesce("next_attempt_at", "scheduled_for"),
            account_position=Window(
                expression=RowNumber(), partition_by=[F("account_id")],
                order_by=[F("scheduled_for").asc(), F("id").asc()],
            ),
        ).order_by("account_position", "due_at", "id")
        accounts_seen = set()
        claimed = 0
        for post in candidates.iterator(chunk_size=100):
            if platform == INSTAGRAM:
                if post.account_id in accounts_seen:
                    continue
                accounts_seen.add(post.account_id)
                if cache.get(_ig_cooldown_key(post.account_id)):
                    continue
            if _dispatch_claim(post, now):
                post.claim_generation += 1
                selected.append(post)
                claimed += 1
                if claimed == limit:
                    break
    return selected


def _confirmed(posts, now, external_post_id=None):
    fields = {
        "status": POST_STATUS_PUBLISHED, "stage": "confirmed", "blocked_reason": "",
        "error_message": "", "published_at": Coalesce("published_at", now), "updated_at": now, "next_attempt_at": None,
        "lease_expires_at": None,
    }
    if external_post_id:
        fields["external_post_id"] = external_post_id
    return posts.update(**fields)


def _recover_stale_processing():
    now = timezone.now()
    stale = ScheduledPost.objects.filter(status=POST_STATUS_PROCESSING).filter(
        Q(lease_expires_at__lte=now) | Q(lease_expires_at__isnull=True, updated_at__lte=now - timedelta(minutes=12)),
    )
    with transaction.atomic():
        legacy = stale.filter(lease_expires_at__isnull=True)
        _confirmed(legacy.exclude(external_post_id__isnull=True).exclude(external_post_id=""), now)
        post_write = stale.filter(Q(final_write_started_at__isnull=False) | Q(lease_expires_at__isnull=True))
        unknown = post_write.update(
            status=OUTCOME_UNKNOWN, stage="reconciling", blocked_reason="confirmation_missing",
            next_attempt_at=now, lease_expires_at=None, error_message=OUTCOME_MESSAGE, updated_at=now,
        )
        requeued = stale.filter(final_write_started_at__isnull=True, lease_expires_at__isnull=False).update(
            status=RETRY_WAIT, stage="claim_expired", next_attempt_at=now, lease_expires_at=None,
            error_message="The publish worker stopped before sending. Queued to retry.", updated_at=now,
        )
    return unknown + requeued


@shared_task(name="publishing.tasks.process_due_posts")
def process_due_posts(run_inline=False):
    _recover_stale_processing()
    due_posts = _get_due_posts()
    posted = 0
    ig_posted = 0
    for post in due_posts:
        try:
            publish_post_task.apply_async(args=[post.id, post.claim_generation], priority=0 if post.platform == INSTAGRAM else 3)
            posted += 1
            ig_posted += post.platform == INSTAGRAM
        except Exception:
            # A lost broker response can still mean the message was delivered. Leave the claim fenced.
            logger.warning("Could not confirm enqueue for scheduled post id=%s", post.id)
    for post_id in ScheduledPost.objects.filter(
        status=OUTCOME_UNKNOWN, stage="reconciling", next_attempt_at__lte=timezone.now(),
    ).order_by("next_attempt_at").values_list("id", flat=True)[:20]:
        try:
            reconcile_publish_outcome.delay(post_id)
        except Exception:
            logger.warning("Could not enqueue confirmation check for scheduled post id=%s", post_id)
    return {"queued": posted, "ig_dispatched": ig_posted}


def _check_account(post, account, now):
    if not settings.PUBLISH_CLAIMS_ENABLED or (
        settings.PUBLISH_CANARY_USER_IDS and post.owner_id not in settings.PUBLISH_CANARY_USER_IDS
    ):
        raise DeliveryDeferred("release_paused", "Publishing is temporarily paused for this schedule.", 60)
    if not post.owner_id or not account or account.user_id != post.owner_id or account.platform != post.platform:
        raise DeliveryDeferred("owner_mismatch", "The destination is no longer owned by this workspace.")
    user = get_user_model().objects.select_for_update().filter(pk=post.owner_id, is_active=True).first()
    if user is None:
        raise DeliveryDeferred("owner_inactive", "The owner of this schedule is inactive.")
    reconnect_delay = 900
    if not account.is_active or not account.access_token or (account.token_expires_at and account.token_expires_at <= now):
        raise DeliveryDeferred("reconnect_required", "Reconnect this destination in Connected Accounts to resume publishing.", reconnect_delay)
    if getattr(account, "publishing_permission_granted", None) is False:
        raise DeliveryDeferred("missing_publish_permission", "Reconnect and grant publishing permission for this destination.", reconnect_delay)
    if settings.ENFORCE_SUBSCRIPTION_IN_TASKS:
        profile, _ = UserProfile.objects.select_for_update().get_or_create(user_id=post.owner_id)
        if not profile.is_subscription_active:
            delay = max(300, settings.SUBSCRIPTION_HOLD_SECONDS)
            raise DeliveryDeferred("subscription_expired", "Renew your subscription to resume this schedule.", delay)


def _claim(post_id, generation):
    with transaction.atomic():
        post = ScheduledPost.objects.select_for_update().filter(pk=post_id).first()
        if not post:
            return None, "missing"
        now = timezone.now()
        if post.status == POST_STATUS_PUBLISHED:
            return None, "already_published"
        if post.external_post_id and not post.provider_upload_id and (
            post.status in WAITING_STATES or (post.status == POST_STATUS_PROCESSING and post.stage == "dispatched")
        ):
            _confirmed(ScheduledPost.objects.filter(pk=post.pk), now)
            return None, "already_published"
        if generation is None:
            # Legacy one-argument messages may acquire only an unclaimed, due leg.
            if not _dispatch_claim(post, now):
                return None, "skipped_state"
            post.refresh_from_db()
            generation = post.claim_generation
        if (
            post.status != POST_STATUS_PROCESSING or post.stage != "dispatched"
            or post.claim_generation != generation or not post.lease_expires_at or post.lease_expires_at <= now
        ):
            return None, "skipped_state"
        account = ConnectedAccount.objects.select_for_update().filter(pk=post.account_id).first()
        try:
            _check_account(post, account, now)
            if post.platform == INSTAGRAM and cache.get(_ig_cooldown_key(post.account_id)):
                raise DeliveryDeferred("provider_cooldown", "Instagram is pacing this destination.", 60)
        except DeliveryDeferred as exc:
            _defer(post, exc)
            return None, BLOCKED
        post.account = account
        token_generation = account.token_generation
        fields = {
            "stage": "preparing", "attempt_count": post.attempt_count + 1, "blocked_reason": "",
            "attempt_token_generation": token_generation, "lease_expires_at": _lease_until(now),
        }
        if post.attempt_token_generation != token_generation:
            fields.update(ig_container_id="", ig_container_created_at=None)
        for name, value in fields.items():
            setattr(post, name, value)
        post.save(update_fields=[*fields, "updated_at"])
        return post, POST_STATUS_PROCESSING


def _owned_claim(post):
    return ScheduledPost.objects.filter(
        pk=post.pk, status=POST_STATUS_PROCESSING, claim_generation=post.claim_generation,
    )


def _defer(post, exc):
    now = timezone.now()
    _owned_claim(post).update(
        status=BLOCKED, stage="blocked", blocked_reason=exc.reason, error_message=str(exc),
        next_attempt_at=now + timedelta(seconds=exc.delay) if exc.delay else None,
        lease_expires_at=None, final_write_started_at=None, updated_at=now,
    )


def _before_final_write(post):
    with transaction.atomic():
        claim = _owned_claim(post).select_for_update().filter(stage="preparing").first()
        if claim is None:
            raise ClaimLost()
        account = ConnectedAccount.objects.select_for_update().filter(pk=post.account_id).first()
        _check_account(claim, account, timezone.now())
        if account.token_generation != post.attempt_token_generation or account.page_id != post.account.page_id or account.ig_user_id != post.account.ig_user_id:
            raise DeliveryDeferred("token_rotated", "The destination changed during preparation. Retrying with the current connection.", 60)
        now = timezone.now()
        if not claim.lease_expires_at or claim.lease_expires_at <= now:
            raise ClaimLost()
        claim.stage = "final_write"
        claim.final_write_started_at = now
        claim.save(update_fields=["stage", "final_write_started_at", "updated_at"])


def _unknown(post, upload_id="", external_id=None, delay=0):
    now = timezone.now()
    return ScheduledPost.objects.filter(
        pk=post.pk, claim_generation=post.claim_generation, status__in=[POST_STATUS_PROCESSING, OUTCOME_UNKNOWN],
    ).update(
        status=OUTCOME_UNKNOWN, stage="reconciling", blocked_reason="confirmation_missing",
        provider_upload_id=upload_id, external_post_id=external_id, error_message=OUTCOME_MESSAGE,
        next_attempt_at=now + timedelta(seconds=delay), lease_expires_at=None, updated_at=now,
    )


def _retry_transient(post, exc):
    now = timezone.now()
    message = str(exc).lower()
    rate_limited = bool(re.search(r"code=(?:4|17|32|613)(?:\D|$)", message)) or "rate-limit" in message or "meta app usage" in message
    quota_exhausted = isinstance(exc, InstagramQuotaExhausted) or (
        isinstance(exc, MetaAPIError) and _is_ig_quota_rejection(post, exc)
    )
    processing_error = any(marker in message for marker in ("status_code=error", "status_code=expired", "status_code=failed"))
    max_attempts = 30 if quota_exhausted or rate_limited else (15 if processing_error else 7)
    retry_in = min(180, 30 + post.attempt_count * 20)
    reason = "preparation_delay"
    description = "Meta reported a temporary delay. The scheduler will retry this post."
    if isinstance(exc, InstagramQuotaProbeUnavailable):
        reason = "instagram_quota_probe"
        description = "Instagram's publishing quota could not be checked. The scheduler will try again."
    if rate_limited:
        reason = "provider_cooldown"
        retry_in = min(600, 45 + post.attempt_count * 30)
        try:
            retry_in = max(retry_in, int(float(cache.get("meta_buc_regain_minutes") or 0) * 60))
        except (ValueError, TypeError):
            pass
        description = "Meta is pacing requests. The scheduler will retry after the cooldown."
    if quota_exhausted:
        reason = "instagram_quota"
        retry_in = 3600
        description = "Instagram's rolling publishing quota is exhausted. The scheduler will check again in one hour."
    if post.platform == INSTAGRAM and (rate_limited or quota_exhausted):
        cache.set(_ig_cooldown_key(post.account_id), True, timeout=retry_in)
    fields = {"ig_container_id": "", "ig_container_created_at": None} if processing_error else {}
    status = RETRY_WAIT if post.attempt_count < max_attempts else POST_STATUS_FAILED
    _owned_claim(post).update(
        status=status, stage="retry_scheduled" if status == RETRY_WAIT else "rejected",
        blocked_reason=reason, error_message=description if status == RETRY_WAIT else "Meta's temporary error persisted. Review the destination and retry.",
        next_attempt_at=now + timedelta(seconds=retry_in) if status == RETRY_WAIT else None,
        lease_expires_at=None, final_write_started_at=None, updated_at=now, **fields,
    )
    return {"status": status, "post_id": post.pk, "retry_in": retry_in}


def _is_ig_quota_rejection(post, exc):
    error = exc.payload.get("error", {})
    return post.platform == INSTAGRAM and error.get("code") == 9 and error.get("error_subcode") == 2207042


@shared_task(name="publishing.tasks.publish_post_task")
def publish_post_task(post_id, claim_generation=None):
    post, status = _claim(post_id, claim_generation)
    if post is None:
        return {"status": status, "post_id": post_id}
    try:
        if post.platform == INSTAGRAM:
            _check_meta_usage_and_throttle()
        result = publish_scheduled_post(post, before_final_write=lambda: _before_final_write(post))
        if isinstance(result, tuple):
            video_id, external_id = result
            _unknown(post, upload_id=video_id, external_id=external_id, delay=30)
            return {"status": OUTCOME_UNKNOWN, "post_id": post_id}
        if not result:
            raise MetaPublishOutcomeUnknown("Missing publish confirmation")
        _confirmed(ScheduledPost.objects.filter(
            pk=post.pk, claim_generation=post.claim_generation, status__in=[POST_STATUS_PROCESSING, OUTCOME_UNKNOWN],
        ), timezone.now(), external_post_id=result)
        return {"status": POST_STATUS_PUBLISHED, "post_id": post_id}
    except ClaimLost:
        return {"status": "skipped_state", "post_id": post_id}
    except DeliveryDeferred as exc:
        _defer(post, exc)
        return {"status": BLOCKED, "post_id": post_id}
    except MetaPublishOutcomeUnknown:
        _unknown(post)
        return {"status": OUTCOME_UNKNOWN, "post_id": post_id}
    except MetaTransientError as exc:
        return _retry_transient(post, exc)
    except MetaAPIError as exc:
        if _is_ig_quota_rejection(post, exc):
            return _retry_transient(post, exc)
        if is_invalid_token_error(exc):
            changed = ConnectedAccount.objects.filter(
                pk=post.account_id, user_id=post.owner_id, token_generation=post.attempt_token_generation, is_active=True,
            ).update(is_active=False, updated_at=timezone.now())
            reason = "reconnect_required" if changed else "token_rotated"
            _defer(post, DeliveryDeferred(reason, "Reconnect this destination if it still needs permission to publish.", 900 if changed else 60))
            return {"status": BLOCKED, "post_id": post_id}
        claim = _owned_claim(post)
        sent = claim.filter(final_write_started_at__isnull=False).exists()
        reason = "provider_rejected" if sent or exc.status_code is not None else "media_preparation_failed"
        message = (
            "Meta rejected this post. Verify the destination, media and publishing permissions before retrying."
            if reason == "provider_rejected" else "Media preparation failed. Verify the file or link before retrying."
        )
        claim.update(
            status=POST_STATUS_FAILED, stage="rejected", blocked_reason=reason,
            next_attempt_at=None, lease_expires_at=None, updated_at=timezone.now(),
            error_message=message,
        )
        return {"status": POST_STATUS_FAILED, "post_id": post_id}
    except Exception as exc:
        if _owned_claim(post).filter(final_write_started_at__isnull=False).exists():
            _unknown(post)
            return {"status": OUTCOME_UNKNOWN, "post_id": post_id}
        logger.warning("Publish preparation stopped for post id=%s type=%s", post_id, type(exc).__name__)
        return _retry_transient(post, exc)


@shared_task(name="publishing.tasks.reconcile_publish_outcome")
def reconcile_publish_outcome(post_id):
    now = timezone.now()
    pending = ScheduledPost.objects.filter(
        pk=post_id, status=OUTCOME_UNKNOWN, stage="reconciling", next_attempt_at__lte=now,
    )
    if not pending.update(next_attempt_at=now + timedelta(minutes=2)):
        return
    post = ScheduledPost.objects.select_related("account").get(pk=post_id)
    result = ScheduledPost.objects.filter(pk=post_id, status=OUTCOME_UNKNOWN, stage="reconciling", claim_generation=post.claim_generation)
    account = post.account
    deadline = (post.final_write_started_at or now) + timedelta(hours=24)
    if account.user_id == post.owner_id and account.is_active:
        client = MetaClient()
        try:
            if post.provider_upload_id or post.external_post_id:
                video_ready = True
                external_id = post.external_post_id
                if post.provider_upload_id:
                    video = client.get_facebook_video_publish_status(post.provider_upload_id, account.access_token)
                    status = video.get("status") if isinstance(video, dict) else None
                    video_ready = isinstance(status, dict) and status.get("video_status") == "ready"
                    if video_ready and not external_id:
                        external_id = client.find_facebook_page_post_for_video(account.page_id, post.provider_upload_id, account.access_token)
                        if external_id:
                            result.update(external_post_id=external_id)
                if video_ready and external_id:
                    publication = client.get_facebook_page_post_status(external_id, account.access_token)
                    if isinstance(publication, dict) and publication.get("is_published") is True:
                        _confirmed(result, timezone.now())
                        return
            elif post.ig_container_id:
                container = client.get_instagram_container_status(post.ig_container_id, account.access_token)
                if isinstance(container, dict) and container.get("status_code") == "PUBLISHED":
                    result.update(
                        stage="operator_review", blocked_reason="container_published_media_id_missing",
                        next_attempt_at=None, updated_at=now,
                    )
                    return
        except MetaAPIError:
            pass
    if now >= deadline or not post.final_write_started_at or not (post.provider_upload_id or post.ig_container_id or post.external_post_id):
        result.update(stage="operator_review", next_attempt_at=None, updated_at=now)
