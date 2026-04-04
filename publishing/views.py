import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from urllib.parse import urljoin

import requests
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST

from core.constants import FACEBOOK, INSTAGRAM, PLATFORM_CHOICES, POST_STATUS_FAILED, POST_STATUS_PENDING, POST_STATUS_PROCESSING
from core.exceptions import MetaAPIError
from core.throttle import throttle_per_user
from core.services.meta_client import MetaClient
from integrations.models import ConnectedAccount
from integrations.sync_state import build_account_sync_state
from publishing.media_utils import prepare_instagram_media_url

from .models import ScheduledPost
from .services import is_invalid_token_error, token_reconnect_message
from .tasks import process_due_posts

logger = logging.getLogger("publishing")
ALLOWED_MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".mp4", ".mov", ".webm", ".m4v", ".avi"}


def _bad_request(message: str, details: dict | None = None) -> JsonResponse:
    body = {"error": message}
    if details:
        body["details"] = details
    return JsonResponse(body, status=400)


def _is_retrying_post(row: dict) -> bool:
    return str(row.get("status") or "").lower() == POST_STATUS_PENDING and "auto-retry in" in str(row.get("error_message") or "").lower()


def _build_public_media_url(request: HttpRequest, relative_url: str) -> str:
    if settings.PUBLIC_BASE_URL:
        return urljoin(settings.PUBLIC_BASE_URL.rstrip("/") + "/", relative_url.lstrip("/"))
    return request.build_absolute_uri(relative_url)


def _upload_file_to_media(request: HttpRequest):
    uploaded = request.FILES.get("media_file")
    if not uploaded:
        return None, None

    max_upload_bytes = int(getattr(settings, "MAX_UPLOAD_FILE_BYTES", 100 * 1024 * 1024) or 0)
    if max_upload_bytes > 0 and int(getattr(uploaded, "size", 0) or 0) > max_upload_bytes:
        return None, "Uploaded media exceeds the configured upload size limit."

    ext = os.path.splitext(uploaded.name or "")[1].lower()
    if ext not in ALLOWED_MEDIA_EXTENSIONS:
        return None, f"Unsupported media file type: {ext or 'unknown'}"

    stamp = timezone.now().strftime("%Y/%m/%d")
    unique_name = f"{uuid.uuid4().hex}{ext}"
    storage_path = f"scheduled_uploads/{stamp}/{unique_name}"
    saved_path = default_storage.save(storage_path, uploaded)
    relative_url = default_storage.url(saved_path)
    return _build_public_media_url(request, relative_url), None


def _resolve_dual_accounts(base_account: ConnectedAccount):
    """
    Resolve paired Facebook + Instagram connected accounts for one business entity.
    """
    fb_account = None
    ig_account = None

    if base_account.platform == FACEBOOK:
        fb_account = base_account
        if base_account.ig_user_id:
            ig_account = ConnectedAccount.objects.filter(
                platform=INSTAGRAM,
                user=base_account.user,
                ig_user_id=base_account.ig_user_id,
            ).first()
            if not ig_account:
                ig_account = ConnectedAccount.objects.filter(
                    platform=INSTAGRAM,
                    user=base_account.user,
                    page_id=base_account.ig_user_id,
                ).first()
    elif base_account.platform == INSTAGRAM:
        ig_account = base_account
        fb_account = ConnectedAccount.objects.filter(
            platform=FACEBOOK,
            user=base_account.user,
            ig_user_id=base_account.page_id,
        ).first()

    if not fb_account or not ig_account:
        return None, None, (
            "FB + Insta post requires linked connected accounts for both platforms. "
            "Connect page again and ensure Instagram is linked to the selected Facebook Page."
        )

    return fb_account, ig_account, None


def _ensure_account_is_currently_synced(request: HttpRequest, account: ConnectedAccount):
    sync_state = build_account_sync_state(account, getattr(request.user, "id", None))
    if sync_state["is_sync_stale"]:
        return _bad_request(sync_state["sync_state_reason"])
    return None


def _current_token_validity(account: ConnectedAccount) -> bool | None:
    cache_key = f"token_valid:{account.id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        data = MetaClient().debug_token(account.access_token).get("data", {})
    except MetaAPIError:
        return None
    is_valid = bool(data.get("is_valid"))
    # Cache for 10 min — token validity rarely changes mid-session.
    cache.set(cache_key, is_valid, timeout=600)
    return is_valid


def _ensure_account_token_is_valid(account: ConnectedAccount):
    if not account.is_active:
        return _bad_request(
            "This connected profile is inactive because it was not included in the latest Meta reconnect. "
            "Reconnect and select this profile again before scheduling."
        )
    if not (account.access_token or "").strip():
        return _bad_request(
            "This connected profile is not active in the latest reconnect. "
            "Reconnect from Accounts and then schedule using the refreshed row."
        )
    token_valid = _current_token_validity(account)
    if token_valid is False:
        return _bad_request(
            token_reconnect_message(
                account,
                "Meta token validation failed before scheduling. Stored page token is no longer valid.",
            )
        )
    return None


def _prepare_media_for_instagram_schedule(media_url: str | None) -> tuple[str | None, JsonResponse | None]:
    if not media_url:
        return media_url, None
    try:
        prepared = prepare_instagram_media_url(media_url)
    except Exception as exc:  # noqa: BLE001
        return None, _bad_request(str(exc))
    return prepared, None


def _auto_dispatch_due_posts_guarded() -> None:
    """
    Self-healing fallback:
    If beat misses a cycle and due pending posts exist, kick dispatcher via
    Celery.  Only falls back to inline execution (single post) when Celery
    itself is unreachable, to avoid blocking the HTTP request for minutes.
    """
    now = timezone.now()
    has_due_pending = ScheduledPost.objects.filter(status=POST_STATUS_PENDING, scheduled_for__lte=now).exists()
    if not has_due_pending:
        return

    lock_key = "publishing:auto_dispatch_due_posts_lock"
    if not cache.add(lock_key, now.isoformat(), timeout=30):
        return

    try:
        # Preferred: dispatch via Celery so the HTTP request returns fast.
        process_due_posts.apply_async(priority=9)
    except Exception:  # noqa: BLE001
        # Celery/Redis unreachable — last-resort inline for at most 1 FB post
        # (never IG inline since IG publishing can block for several minutes).
        try:
            process_due_posts(run_inline=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("auto dispatcher inline fallback failed error=%s", str(exc))


def _recover_stale_processing_posts(user=None, stale_minutes: int = 12) -> int:
    # stale_minutes must exceed the Celery hard time limit (default 480s = 8 min)
    # so we never reset a post that a worker is still actively processing.
    lock_key = f"publishing:recover_stale_processing_posts:{getattr(user, 'id', 'global')}"
    if not cache.add(lock_key, timezone.now().isoformat(), timeout=30):
        return 0

    cutoff = timezone.now() - timedelta(minutes=stale_minutes)
    stale_qs = ScheduledPost.objects.filter(
        status=POST_STATUS_PROCESSING,
        updated_at__lt=cutoff,
    )
    if user is not None:
        stale_qs = stale_qs.filter(account__user=user)
    stale_ids = list(stale_qs.values_list("id", flat=True))
    try:
        if not stale_ids:
            return 0
        ScheduledPost.objects.filter(id__in=stale_ids).update(
            status=POST_STATUS_PENDING,
            error_message="Recovered from stale processing state; auto re-queued.",
            updated_at=timezone.now(),
        )
        logger.warning("recovered stale processing posts count=%s ids=%s", len(stale_ids), stale_ids[:20])
        return len(stale_ids)
    finally:
        cache.delete(lock_key)


@require_POST
@login_required
@throttle_per_user("60/m", scope="schedule_post")
def schedule_post(request: HttpRequest) -> JsonResponse:
    is_json_request = request.content_type and request.content_type.startswith("application/json")
    if is_json_request:
        try:
            payload = json.loads(request.body.decode())
        except json.JSONDecodeError:
            return _bad_request("Invalid JSON body")
        account_id = payload.get("account_id")
        platform = payload.get("platform")
        message = payload.get("message")
        media_url = payload.get("media_url")
        scheduled_for = payload.get("scheduled_for")
    else:
        account_id = request.POST.get("account_id")
        platform = request.POST.get("platform")
        message = request.POST.get("message")
        media_url = request.POST.get("media_url")
        scheduled_for = request.POST.get("scheduled_for")
        uploaded_media_url, upload_error = _upload_file_to_media(request)
        if upload_error:
            return _bad_request(upload_error)
        if uploaded_media_url:
            media_url = uploaded_media_url

    if not account_id or not platform or not scheduled_for:
        return _bad_request("account_id, platform, and scheduled_for are required")

    valid_platforms = {choice[0] for choice in PLATFORM_CHOICES}
    if platform not in valid_platforms and platform != "both":
        return _bad_request("platform must be facebook, instagram, or both")

    account = ConnectedAccount.objects.filter(id=account_id, user=request.user).first()
    if not account:
        return JsonResponse({"error": "Connected account not found"}, status=404)
    if platform in valid_platforms and account.platform != platform:
        return _bad_request("account_id does not belong to selected platform")
    stale_response = _ensure_account_is_currently_synced(request, account)
    if stale_response:
        return stale_response
    invalid_token_response = _ensure_account_token_is_valid(account)
    if invalid_token_response:
        return invalid_token_response

    dt = parse_datetime(scheduled_for)
    if not isinstance(dt, datetime):
        return _bad_request("scheduled_for must be valid ISO8601 datetime")
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone=dt_timezone.utc)

    if platform == "both":
        if not media_url:
            return _bad_request("media_url or media_file is required when platform is both")
        # Keep the original URL for Facebook (full quality, direct upload)
        # and prepare a separate optimized URL for Instagram.
        fb_media_url = media_url
        ig_media_url, media_error = _prepare_media_for_instagram_schedule(media_url)
        if media_error:
            return media_error
        fb_account, ig_account, resolve_error = _resolve_dual_accounts(account)
        if resolve_error:
            return _bad_request(resolve_error)
        for target in [fb_account, ig_account]:
            stale_response = _ensure_account_is_currently_synced(request, target)
            if stale_response:
                return stale_response
            invalid_token_response = _ensure_account_token_is_valid(target)
            if invalid_token_response:
                return invalid_token_response

        # Small stagger: FB at scheduled time, IG at scheduled time + 5s.
        # Per-user lane lock serializes each user's IG posts, so different
        # users' IG posts can run in parallel (separate rate-limit buckets).
        targets = [
            (fb_account, FACEBOOK, dt, fb_media_url),
            (ig_account, INSTAGRAM, dt + timedelta(seconds=5), ig_media_url),
        ]

        created_posts = []
        try:
            with transaction.atomic():
                for target_account, target_platform, target_dt, target_media in targets:
                    post = ScheduledPost(
                        account=target_account,
                        platform=target_platform,
                        message=message,
                        media_url=target_media,
                        scheduled_for=target_dt,
                    )
                    post.save()
                    created_posts.append(post)
                    logger.info(
                        "post scheduled id=%s platform=%s account_id=%s scheduled_for=%s",
                        post.id,
                        post.platform,
                        post.account_id,
                        post.scheduled_for,
                    )
        except Exception as exc:  # noqa: BLE001
            return _bad_request("Validation failed", {"message": str(exc)})

        return JsonResponse(
            {
                "ids": [p.id for p in created_posts],
                "status": POST_STATUS_PENDING,
                "media_url": media_url,
                "created": len(created_posts),
                "posts": [
                    {"id": p.id, "platform": p.platform, "account_id": p.account_id, "page_name": p.account.page_name}
                    for p in created_posts
                ],
            },
            status=201,
        )

    post = ScheduledPost(account=account, platform=platform, message=message, media_url=media_url, scheduled_for=dt)
    if platform == INSTAGRAM:
        media_url, media_error = _prepare_media_for_instagram_schedule(media_url)
        if media_error:
            return media_error
        post.media_url = media_url

    try:
        post.save()
    except Exception as exc:  # noqa: BLE001
        return _bad_request("Validation failed", {"message": str(exc)})

    logger.info(
        "post scheduled id=%s platform=%s account_id=%s scheduled_for=%s",
        post.id,
        post.platform,
        post.account_id,
        post.scheduled_for,
    )
    return JsonResponse({"id": post.id, "status": post.status, "media_url": post.media_url}, status=201)


@require_GET
@login_required
def list_scheduled_posts(request: HttpRequest) -> JsonResponse:
    _recover_stale_processing_posts(request.user)
    _auto_dispatch_due_posts_guarded()
    rows = list(
        ScheduledPost.objects.select_related("account").filter(account__user=request.user).values(
            "id",
            "platform",
            "message",
            "media_url",
            "scheduled_for",
            "status",
            "error_message",
            "external_post_id",
            "account__page_name",
        )
        .order_by("-scheduled_for", "-id")
    )
    for row in rows:
        row["page_name"] = row.pop("account__page_name")
    return JsonResponse(rows, safe=False)


@require_GET
@login_required
def publish_health_status(request: HttpRequest) -> JsonResponse:
    now = timezone.now()
    rows = list(
        ScheduledPost.objects.filter(account__user=request.user).values(
            "id",
            "status",
            "platform",
            "scheduled_for",
            "published_at",
            "error_message",
        ).order_by("-updated_at")[:200]
    )
    retrying = [row for row in rows if _is_retrying_post(row)]
    retrying_instagram = [row for row in retrying if str(row.get("platform") or "").lower() == INSTAGRAM]
    processing = [row for row in rows if str(row.get("status") or "").lower() == POST_STATUS_PROCESSING]
    due_pending = [
        row
        for row in rows
        if str(row.get("status") or "").lower() == POST_STATUS_PENDING and row.get("scheduled_for") and row["scheduled_for"] <= now
    ]
    published_recent = [row for row in rows if row.get("published_at") and row["published_at"] >= now - timedelta(hours=6)]

    latest_retry = retrying_instagram[0] if retrying_instagram else (retrying[0] if retrying else None)
    payload = {
        "retrying_count": len(retrying),
        "retrying_instagram_count": len(retrying_instagram),
        "processing_count": len(processing),
        "due_pending_count": len(due_pending),
        "published_last_6h": len(published_recent),
        "latest_retry_message": str((latest_retry or {}).get("error_message") or ""),
        "latest_retry_scheduled_for": latest_retry["scheduled_for"].isoformat() if latest_retry and latest_retry.get("scheduled_for") else None,
    }
    return JsonResponse(payload)


@require_POST
@login_required
def retry_failed_post(request: HttpRequest, post_id: int) -> JsonResponse:
    post = ScheduledPost.objects.select_related("account").filter(id=post_id, account__user=request.user).first()
    if not post:
        return JsonResponse({"error": "Scheduled post not found"}, status=404)

    if post.status != POST_STATUS_FAILED:
        return _bad_request("Only failed posts can be retried")
    stale_response = _ensure_account_is_currently_synced(request, post.account)
    if stale_response:
        return stale_response

    if is_invalid_token_error(post.error_message):
        token_valid = _current_token_validity(post.account)
        if token_valid is False:
            return _bad_request(token_reconnect_message(post.account, post.error_message))

    retry_time = timezone.now()
    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return _bad_request("Invalid JSON body")

    requested_time = payload.get("scheduled_for")
    if requested_time:
        dt = parse_datetime(requested_time)
        if not isinstance(dt, datetime):
            return _bad_request("scheduled_for must be valid ISO8601 datetime")
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone=dt_timezone.utc)
        retry_time = dt

    if post.platform == INSTAGRAM and post.media_url:
        prepared_media_url, media_error = _prepare_media_for_instagram_schedule(post.media_url)
        if media_error:
            return media_error
        post.media_url = prepared_media_url

    post.status = POST_STATUS_PENDING
    post.error_message = ""
    post.external_post_id = ""
    post.published_at = None
    post.scheduled_for = retry_time
    post.save(
        update_fields=[
            "status",
            "error_message",
            "external_post_id",
            "published_at",
            "scheduled_for",
            "media_url",
            "updated_at",
        ]
    )

    logger.info("failed post retried id=%s by_user=%s scheduled_for=%s", post.id, request.user.id, post.scheduled_for)
    return JsonResponse({"id": post.id, "status": post.status, "scheduled_for": post.scheduled_for.isoformat()})
