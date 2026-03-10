import json
import logging
from datetime import datetime, timezone as dt_timezone

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST

from core.constants import PLATFORM_CHOICES, POST_STATUS_FAILED, POST_STATUS_PENDING
from integrations.models import ConnectedAccount

from .models import ScheduledPost

logger = logging.getLogger("publishing")


def _bad_request(message: str, details: dict | None = None) -> JsonResponse:
    body = {"error": message}
    if details:
        body["details"] = details
    return JsonResponse(body, status=400)


@require_POST
@login_required
def schedule_post(request: HttpRequest) -> JsonResponse:
    try:
        payload = json.loads(request.body.decode())
    except json.JSONDecodeError:
        return _bad_request("Invalid JSON body")

    account_id = payload.get("account_id")
    platform = payload.get("platform")
    message = payload.get("message")
    media_url = payload.get("media_url")
    scheduled_for = payload.get("scheduled_for")

    if not account_id or not platform or not scheduled_for:
        return _bad_request("account_id, platform, and scheduled_for are required")

    valid_platforms = {choice[0] for choice in PLATFORM_CHOICES}
    if platform not in valid_platforms:
        return _bad_request("platform must be facebook or instagram")

    account = ConnectedAccount.objects.filter(id=account_id).first()
    if not account:
        return JsonResponse({"error": "Connected account not found"}, status=404)
    if account.platform != platform:
        return _bad_request("account_id does not belong to selected platform")

    dt = parse_datetime(scheduled_for)
    if not isinstance(dt, datetime):
        return _bad_request("scheduled_for must be valid ISO8601 datetime")
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone=dt_timezone.utc)

    post = ScheduledPost(
        account=account,
        platform=platform,
        message=message,
        media_url=media_url,
        scheduled_for=dt,
    )

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
    return JsonResponse({"id": post.id, "status": post.status}, status=201)


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
