import json
import logging
import os
import uuid
from datetime import datetime, timezone as dt_timezone
from urllib.parse import urljoin

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST

from core.constants import FACEBOOK, INSTAGRAM, PLATFORM_CHOICES, POST_STATUS_FAILED, POST_STATUS_PENDING
from core.exceptions import MetaAPIError
from core.services.meta_client import MetaClient
from integrations.models import ConnectedAccount

from .models import ScheduledPost
from .services import is_invalid_token_error, token_reconnect_message

logger = logging.getLogger("publishing")
ALLOWED_MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov", ".webm", ".m4v"}


def _bad_request(message: str, details: dict | None = None) -> JsonResponse:
    body = {"error": message}
    if details:
        body["details"] = details
    return JsonResponse(body, status=400)


def _build_public_media_url(request: HttpRequest, relative_url: str) -> str:
    if settings.PUBLIC_BASE_URL:
        return urljoin(settings.PUBLIC_BASE_URL.rstrip("/") + "/", relative_url.lstrip("/"))
    return request.build_absolute_uri(relative_url)


def _upload_file_to_media(request: HttpRequest):
    uploaded = request.FILES.get("media_file")
    if not uploaded:
        return None, None

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
            ig_account = ConnectedAccount.objects.filter(platform=INSTAGRAM, ig_user_id=base_account.ig_user_id).first()
            if not ig_account:
                ig_account = ConnectedAccount.objects.filter(platform=INSTAGRAM, page_id=base_account.ig_user_id).first()
    elif base_account.platform == INSTAGRAM:
        ig_account = base_account
        fb_account = ConnectedAccount.objects.filter(platform=FACEBOOK, ig_user_id=base_account.page_id).first()

    if not fb_account or not ig_account:
        return None, None, (
            "FB + Insta post requires linked connected accounts for both platforms. "
            "Connect page again and ensure Instagram is linked to the selected Facebook Page."
        )

    return fb_account, ig_account, None


def _current_token_validity(account: ConnectedAccount) -> bool | None:
    try:
        data = MetaClient().debug_token(account.access_token).get("data", {})
    except MetaAPIError:
        return None
    return bool(data.get("is_valid"))


@require_POST
@login_required
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

    account = ConnectedAccount.objects.filter(id=account_id).first()
    if not account:
        return JsonResponse({"error": "Connected account not found"}, status=404)
    if platform in valid_platforms and account.platform != platform:
        return _bad_request("account_id does not belong to selected platform")

    dt = parse_datetime(scheduled_for)
    if not isinstance(dt, datetime):
        return _bad_request("scheduled_for must be valid ISO8601 datetime")
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone=dt_timezone.utc)

    if platform == "both":
        if not media_url:
            return _bad_request("media_url or media_file is required when platform is both")
        fb_account, ig_account, resolve_error = _resolve_dual_accounts(account)
        if resolve_error:
            return _bad_request(resolve_error)

        targets = [
            (fb_account, FACEBOOK),
            (ig_account, INSTAGRAM),
        ]

        created_posts = []
        try:
            with transaction.atomic():
                for target_account, target_platform in targets:
                    post = ScheduledPost(
                        account=target_account,
                        platform=target_platform,
                        message=message,
                        media_url=media_url,
                        scheduled_for=dt,
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
def list_scheduled_posts(_request: HttpRequest) -> JsonResponse:
    rows = list(
        ScheduledPost.objects.select_related("account").values(
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


@require_POST
@login_required
def retry_failed_post(request: HttpRequest, post_id: int) -> JsonResponse:
    post = ScheduledPost.objects.filter(id=post_id).first()
    if not post:
        return JsonResponse({"error": "Scheduled post not found"}, status=404)

    if post.status != POST_STATUS_FAILED:
        return _bad_request("Only failed posts can be retried")

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

    post.status = POST_STATUS_PENDING
    post.error_message = ""
    post.external_post_id = ""
    post.published_at = None
    post.scheduled_for = retry_time
    post.save(update_fields=["status", "error_message", "external_post_id", "published_at", "scheduled_for", "updated_at"])

    logger.info("failed post retried id=%s by_user=%s scheduled_for=%s", post.id, request.user.id, post.scheduled_for)
    return JsonResponse({"id": post.id, "status": post.status, "scheduled_for": post.scheduled_for.isoformat()})
