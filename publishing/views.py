import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST

from core.constants import FACEBOOK, INSTAGRAM, PLATFORM_CHOICES, POST_STATUS_FAILED, POST_STATUS_PENDING, POST_STATUS_PROCESSING
from core.exceptions import MetaAPIError, MetaPermanentError
from core.throttle import throttle_per_user
from core.services.meta_client import MetaClient
from integrations.models import ConnectedAccount
from integrations.sync_state import build_account_sync_state
from publishing.media_utils import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, media_extension, prepare_instagram_media_url, resolve_local_media_storage_path, validate_public_media_url

from .models import BLOCKED, CANCELLED, OUTCOME_UNKNOWN, RETRY_WAIT, WAITING_STATES, ScheduleChange, ScheduledPost, ScheduleRequest
from .services import is_invalid_token_error, token_reconnect_message

ALLOWED_MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".mp4", ".mov", ".webm", ".m4v", ".avi"}
INSTAGRAM_SCHEDULE_BUFFER_SECONDS = 120


def _bad_request(message: str, details: dict | None = None) -> JsonResponse:
    body = {"error": message}
    if details:
        body["details"] = details
    return JsonResponse(body, status=400)


def _is_retrying_post(row: dict) -> bool:
    return row["status"] == RETRY_WAIT or (
        row["status"] == POST_STATUS_PENDING and "auto-retry in" in str(row.get("error_message") or "").lower()
    )


def _build_public_media_url(request: HttpRequest, relative_url: str) -> str:
    if settings.PUBLIC_BASE_URL:
        return urljoin(settings.PUBLIC_BASE_URL.rstrip("/") + "/", relative_url.lstrip("/"))
    return request.build_absolute_uri(relative_url)


_IMAGE_UPLOAD_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def _verify_upload_content(uploaded, ext: str) -> str | None:
    """Verify uploaded bytes match the declared extension, so a mislabeled file (e.g.
    an HTML/script payload named foo.jpg) is rejected before it's served as media to
    Meta. Returns an error string on mismatch, else None. Fails OPEN when it can't sniff
    (e.g. Pillow missing) so it never blocks legitimate uploads."""
    try:
        uploaded.seek(0)
        head = uploaded.read(16)
        uploaded.seek(0)
    except Exception:  # noqa: BLE001
        return None
    if ext in _IMAGE_UPLOAD_EXTS:
        try:
            from PIL import Image
        except ImportError:
            return None
        try:
            uploaded.seek(0)
            Image.open(uploaded).verify()
        except Exception:  # noqa: BLE001
            return "Uploaded file is not a valid image."
        finally:
            try:
                uploaded.seek(0)
            except Exception:  # noqa: BLE001
                pass
        return None
    # Video magic-byte sniff (mp4/mov/m4v, webm/mkv, avi).
    video_ok = (
        head[4:8] == b"ftyp"
        or head[:4] == b"\x1aE\xdf\xa3"
        or (head[:4] == b"RIFF" and head[8:12] == b"AVI ")
    )
    return None if video_ok else "Uploaded file does not look like a valid video."


def _upload_file_to_media(request: HttpRequest):
    uploaded = request.FILES.get("media_file")
    if not uploaded:
        return None, None

    # A blank/0 MAX_UPLOAD_FILE_BYTES must NOT disable the limit — fall back to the
    # default cap so the guard is always enforced.
    max_upload_bytes = int(getattr(settings, "MAX_UPLOAD_FILE_BYTES", 100 * 1024 * 1024) or 0)
    if max_upload_bytes <= 0:
        max_upload_bytes = 100 * 1024 * 1024
    if int(getattr(uploaded, "size", 0) or 0) > max_upload_bytes:
        return None, "Uploaded media exceeds the configured upload size limit."

    ext = os.path.splitext(uploaded.name or "")[1].lower()
    if ext not in ALLOWED_MEDIA_EXTENSIONS:
        return None, f"Unsupported media file type: {ext or 'unknown'}"

    content_error = _verify_upload_content(uploaded, ext)
    if content_error:
        return None, content_error

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
    cache_key = f"token_valid:{account.id}:{account.token_generation}"
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
    if account.publishing_permission_granted is False:
        return _bad_request("Reconnect and grant publishing permission for this destination.")
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
    except MetaPermanentError as exc:
        return None, _bad_request(str(exc))
    except OSError:
        return None, _bad_request("Instagram media could not be prepared. Re-upload the file and try again.")
    return prepared, None


def _next_available_instagram_slot(account: ConnectedAccount, desired_dt: datetime, exclude_id=None) -> datetime:
    candidate = desired_dt
    window = timedelta(seconds=INSTAGRAM_SCHEDULE_BUFFER_SECONDS)
    active = ScheduledPost.objects.filter(
        account=account,
        platform=INSTAGRAM,
        status__in=[*WAITING_STATES, POST_STATUS_PROCESSING],
    ).exclude(pk=exclude_id)
    while active.filter(
        next_attempt_at__gt=candidate - window,
        next_attempt_at__lt=candidate + window,
    ).exists():
        candidate += window
    return candidate


def _parse_schedule_time(value):
    try:
        dt = parse_datetime(value)
    except (ValueError, TypeError):
        return None
    if dt and timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone=dt_timezone.utc)
    return dt.astimezone(dt_timezone.utc) if dt else None


def _display_timezone(value):
    try:
        return ZoneInfo(value).key
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        return None


def _request_payload(request):
    if request.content_type == "application/json":
        try:
            payload = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return payload if isinstance(payload, dict) else None
    return request.POST


def _schedule_receipt(request, kind, targets, dt, display_timezone, message, media_url):
    key = request.headers.get("Idempotency-Key")
    if key is None:
        return None, None
    if not key.strip() or len(key) > 128:
        return None, _bad_request("Idempotency-Key must contain between 1 and 128 characters.")
    upload = request.FILES.get("media_file")
    file_fingerprint = None
    if upload:
        maximum = int(getattr(settings, "MAX_UPLOAD_FILE_BYTES", 0) or 0)
        if maximum <= 0:
            maximum = 100 * 1024 * 1024
        if upload.size > maximum:
            return None, _bad_request("Uploaded media exceeds the configured upload size limit.")
        digest = hashlib.sha256()
        for chunk in upload.chunks():
            digest.update(chunk)
        upload.seek(0)
        file_fingerprint = [os.path.splitext(upload.name)[1].lower(), digest.hexdigest()]
    canonical = [kind, targets, dt.isoformat(), display_timezone, message, media_url if not upload else None, file_fingerprint]
    fingerprint = hashlib.sha256(json.dumps(canonical, separators=(",", ":")).encode()).hexdigest()
    receipt, _ = ScheduleRequest.objects.get_or_create(
        user=request.user, key=key, defaults={"fingerprint": fingerprint},
    )
    if receipt.fingerprint != fingerprint:
        return None, JsonResponse({"error": "This scheduling key was already used for different content."}, status=409)
    return receipt, None


def _leg(post):
    return {
        "id": post.id, "platform": post.platform, "account_id": post.account_id, "page_name": post.account.page_name,
        "status": post.status, "scheduled_for": post.scheduled_for.isoformat(),
        "next_attempt_at": post.next_attempt_at.isoformat() if post.next_attempt_at else None,
        "display_timezone": post.display_timezone, "external_post_id": post.external_post_id,
    }


def _single_result(posts, receipt, replay=False):
    payload = {
        "status": posts[0].status if len(posts) == 1 else POST_STATUS_PENDING,
        "media_url": posts[0].media_url, "created": len(posts),
        "posts": [_leg(post) for post in posts], "schedule_request_id": receipt.id if receipt else None,
    }
    if len(posts) == 1:
        payload["id"] = posts[0].id
    else:
        payload["ids"] = [post.id for post in posts]
    return JsonResponse(payload, status=200 if replay else 201)


def _target_media(account, media_url):
    if not media_url:
        return (None, _bad_request("Instagram requires an image or video.")) if account.platform == INSTAGRAM else (None, None)
    try:
        parsed = urlparse(media_url)
    except ValueError:
        return None, _bad_request("Provide a valid media URL.")
    local = resolve_local_media_storage_path(media_url)
    if not parsed.hostname or parsed.username or parsed.password or (parsed.scheme != "https" and not local):
        return None, _bad_request("Provide a public HTTPS media URL.")
    if not local:
        try:
            validate_public_media_url(media_url)
        except MetaPermanentError as exc:
            return None, _bad_request(str(exc))
    ext = media_extension(media_url)
    if account.platform == INSTAGRAM:
        supported = IMAGE_EXTENSIONS | {".mp4", ".mov"} if local else {".jpg", ".jpeg", ".mp4", ".mov"}
        if ext not in supported:
            return None, _bad_request("Instagram link media must be a JPEG image or MP4/MOV video. Upload local images directly to convert them.")
        return _prepare_media_for_instagram_schedule(media_url)
    if ext and ext not in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
        return None, _bad_request("Unsupported Facebook media format.")
    return media_url, None


def _create_leg(account, receipt, message, media_url, dt, display_timezone, initial_delay=0):
    current_account = ConnectedAccount.objects.select_for_update().filter(pk=account.pk, user_id=account.user_id).first()
    if current_account is None or not current_account.is_active or current_account.token_generation != account.token_generation:
        raise ValidationError("Destination changed before the schedule was saved.")
    next_attempt_at = dt
    if account.platform == INSTAGRAM:
        next_attempt_at = _next_available_instagram_slot(account, dt + timedelta(seconds=initial_delay))
    return ScheduledPost.objects.create(
        account=current_account, platform=account.platform, message=message, media_url=media_url,
        scheduled_for=dt, next_attempt_at=next_attempt_at, display_timezone=display_timezone, schedule_request=receipt,
    )


@require_POST
@login_required
@throttle_per_user("60/m", scope="schedule_post")
def schedule_post(request):
    payload = _request_payload(request)
    if payload is None:
        return _bad_request("Invalid JSON body")
    try:
        account_id = int(payload.get("account_id"))
    except (ValueError, TypeError):
        return _bad_request("A valid account_id is required.")
    platform = payload.get("platform")
    valid_platforms = {choice[0] for choice in PLATFORM_CHOICES}
    if not isinstance(platform, str) or (platform not in valid_platforms and platform != "both"):
        return _bad_request("platform must be facebook, instagram, or both")
    dt = _parse_schedule_time(payload.get("scheduled_for"))
    display_timezone = _display_timezone(payload.get("display_timezone", "Asia/Kolkata"))
    message, media_url = payload.get("message") or "", payload.get("media_url") or None
    if not dt or not display_timezone or not isinstance(message, str) or (media_url and not isinstance(media_url, str)):
        return _bad_request("Provide valid schedule time, timezone, message and media URL.")
    message = message.strip()
    receipt, error = _schedule_receipt(request, "single", [account_id, platform], dt, display_timezone, message, media_url)
    if error:
        return error
    if receipt:
        existing = list(_user_legs(request.user).filter(schedule_request=receipt).select_related("account").order_by("id"))
        if existing:
            return _single_result(existing, receipt, replay=True)

    account = ConnectedAccount.objects.filter(id=account_id, user=request.user).first()
    if not account:
        return JsonResponse({"error": "Connected account not found"}, status=404)
    if platform != "both" and account.platform != platform:
        return _bad_request("account_id does not belong to selected platform")
    if platform == "both":
        fb_account, ig_account, error = _resolve_dual_accounts(account)
        if error:
            return _bad_request(error)
        accounts = (fb_account, ig_account)
    else:
        accounts = (account,)
    for target in accounts:
        error = _ensure_account_is_currently_synced(request, target) or _ensure_account_token_is_valid(target)
        if error:
            return error

    try:
        with transaction.atomic():
            if receipt:
                receipt = ScheduleRequest.objects.select_for_update().get(pk=receipt.pk)
                existing = list(_user_legs(request.user).filter(schedule_request=receipt).select_related("account").order_by("id"))
                if existing:
                    return _single_result(existing, receipt, replay=True)
            uploaded_url, upload_error = _upload_file_to_media(request)
            if upload_error:
                return _bad_request(upload_error)
            media_url = uploaded_url or media_url
            prepared = []
            for target in accounts:
                target_url, error = _target_media(target, media_url)
                if error:
                    return error
                prepared.append((target, target_url))
            posts = [
                _create_leg(target, receipt, message, target_url, dt, display_timezone, initial_delay=5 if platform == "both" else 0)
                for target, target_url in prepared
            ]
    except ValidationError as exc:
        return _bad_request("Validation failed", {"messages": exc.messages})
    return _single_result(posts, receipt)


@require_POST
@login_required
@throttle_per_user("20/m", scope="bulk_schedule")
def bulk_schedule_post(request):
    payload = _request_payload(request)
    if payload is None:
        return _bad_request("Invalid JSON body")
    account_ids = payload.get("account_ids")
    if isinstance(account_ids, str):
        try:
            account_ids = json.loads(account_ids) if account_ids.startswith("[") else account_ids.split(",")
        except json.JSONDecodeError:
            return _bad_request("Invalid account_ids.")
    if not isinstance(account_ids, list) or not account_ids:
        return _bad_request("account_ids (a non-empty list) is required")
    deduped = {}
    for raw in account_ids:
        try:
            deduped[int(raw)] = None
        except (TypeError, ValueError):
            continue
    max_accounts = int(getattr(settings, "BULK_SCHEDULE_MAX_ACCOUNTS", 100) or 100)
    if not deduped or len(deduped) > max_accounts:
        return _bad_request(f"Provide between 1 and {max_accounts} account IDs.")
    dt = _parse_schedule_time(payload.get("scheduled_for"))
    display_timezone = _display_timezone(payload.get("display_timezone", "Asia/Kolkata"))
    message, media_url = payload.get("message") or "", payload.get("media_url") or None
    if not dt or not display_timezone or not isinstance(message, str) or (media_url and not isinstance(media_url, str)):
        return _bad_request("Provide valid schedule time, timezone, message and media URL.")
    message = message.strip()
    receipt, error = _schedule_receipt(request, "bulk", sorted(deduped), dt, display_timezone, message, media_url)
    if error:
        return error

    owned = {a.id: a for a in ConnectedAccount.objects.filter(pk__in=deduped, user=request.user)}
    existing = {
        post.account_id: post for post in _user_legs(request.user).filter(schedule_request=receipt).select_related("account")
    } if receipt else {}
    created = []
    skipped = []
    new_count = 0
    upload_checked = False
    for aid in deduped:
        if aid in existing:
            created.append(_leg(existing[aid]))
            continue
        account = owned.get(aid)
        if not account:
            skipped.append({"account_id": aid, "reason": "Not found or not your account"})
            continue
        error = _ensure_account_is_currently_synced(request, account) or _ensure_account_token_is_valid(account)
        if not error:
            if not upload_checked:
                uploaded_url, upload_error = _upload_file_to_media(request)
                if upload_error:
                    return _bad_request(upload_error)
                media_url = uploaded_url or media_url
                upload_checked = True
            target_media, error = _target_media(account, media_url)
        if error:
            skipped.append({"account_id": aid, "page_name": account.page_name, "reason": json.loads(error.content)["error"]})
            continue
        try:
            with transaction.atomic():
                if receipt:
                    ScheduleRequest.objects.select_for_update().get(pk=receipt.pk)
                    accepted = _user_legs(request.user).select_related("account").filter(schedule_request=receipt, account_id=aid).first()
                    if accepted:
                        created.append(_leg(accepted))
                        continue
                post = _create_leg(account, receipt, message, target_media, dt, display_timezone)
        except ValidationError as exc:
            skipped.append({"account_id": aid, "page_name": account.page_name, "reason": " ".join(exc.messages)})
            continue
        new_count += 1
        created.append(_leg(post))

    return JsonResponse({
        "created_count": len(created), "new_count": new_count, "skipped_count": len(skipped),
        "total_requested": len(deduped), "status": POST_STATUS_PENDING, "schedule_request_id": receipt.id if receipt else None,
        "created": created, "skipped": skipped,
    }, status=201 if new_count else 200 if created else 400)

@require_GET
@login_required
def list_scheduled_posts(request: HttpRequest) -> JsonResponse:
    rows = list(
        _user_legs(request.user).values(
            "id",
            "platform",
            "message",
            "media_url",
            "scheduled_for",
            "display_timezone",
            "next_attempt_at",
            "schedule_request_id",
            "status",
            "stage",
            "blocked_reason",
            "error_message",
            "final_write_started_at",
            "external_post_id",
            "provider_upload_id",
            "account__page_name",
        )
        .order_by("-scheduled_for", "-id")
    )
    for row in rows:
        row["page_name"] = row.pop("account__page_name")
        unconfirmed = not (row["external_post_id"] or row["provider_upload_id"])
        can_change = unconfirmed and not row["final_write_started_at"] and row["status"] in [*WAITING_STATES, POST_STATUS_PROCESSING]
        row["cancelable"] = row["editable"] = can_change
        row["retryable"] = unconfirmed and row["status"] == POST_STATUS_FAILED
    return JsonResponse(rows, safe=False)


@require_GET
@login_required
def publish_health_status(request: HttpRequest) -> JsonResponse:
    now = timezone.now()
    rows = list(
        _user_legs(request.user).values(
            "id",
            "status",
            "platform",
            "scheduled_for",
            "next_attempt_at",
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
        if row["status"] == POST_STATUS_PENDING and (row["next_attempt_at"] or row["scheduled_for"]) <= now
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
        "latest_retry_scheduled_for": (latest_retry["next_attempt_at"] or latest_retry["scheduled_for"]).isoformat() if latest_retry else None,
        "outcome_unknown_count": sum(row["status"] == OUTCOME_UNKNOWN for row in rows),
        "blocked_count": sum(row["status"] == BLOCKED for row in rows),
    }
    return JsonResponse(payload)


def _user_legs(user):
    return ScheduledPost.objects.filter(owner=user, account__user=user)


def _modifiable(legs):
    return legs.filter(
        status__in=[*WAITING_STATES, POST_STATUS_PROCESSING],
        final_write_started_at__isnull=True, provider_upload_id="",
    ).filter(Q(external_post_id__isnull=True) | Q(external_post_id=""))


def _retryable(legs):
    return legs.filter(status=POST_STATUS_FAILED, provider_upload_id="").filter(
        Q(external_post_id__isnull=True) | Q(external_post_id=""),
    )


def _conflict():
    return JsonResponse({"error": "This post changed or has already entered delivery. Reload to see its status."}, status=409)


@require_POST
@login_required
def cancel_scheduled_post(request, post_id):
    legs = _user_legs(request.user).filter(pk=post_id)
    with transaction.atomic():
        post = legs.select_for_update(of=("self",)).first()
        if post is None or not ConnectedAccount.objects.select_for_update().filter(pk=post.account_id, user=request.user).first():
            return JsonResponse({"error": "Scheduled post not found"}, status=404)
        now = timezone.now()
        if not _modifiable(ScheduledPost.objects.filter(pk=post_id, owner=request.user)).update(
            status=CANCELLED, stage="cancelled", error_message="", blocked_reason="",
            next_attempt_at=None, lease_expires_at=None, updated_at=now,
        ):
            return _conflict()
    return JsonResponse({"id": post_id, "status": CANCELLED})


@require_POST
@login_required
def edit_scheduled_post(request, post_id):
    return _update_scheduled_post(request, post_id, is_retry=False)


@require_POST
@login_required
def retry_failed_post(request, post_id):
    return _update_scheduled_post(request, post_id, is_retry=True)


def _update_scheduled_post(request, post_id, *, is_retry):
    legs = _user_legs(request.user).filter(pk=post_id)
    post = legs.select_related("account").first()
    if not post:
        return JsonResponse({"error": "Scheduled post not found"}, status=404)
    eligible = _retryable(legs) if is_retry else _modifiable(legs)
    if not eligible.exists():
        return _conflict()
    error = _ensure_account_is_currently_synced(request, post.account)
    if error:
        return error
    if is_retry and is_invalid_token_error(post.error_message):
        if _current_token_validity(post.account) is False:
            return _bad_request(token_reconnect_message(post.account))
    payload = _request_payload(request)
    if payload is None:
        return _bad_request("Invalid JSON body")
    dt = _parse_schedule_time(payload.get("scheduled_for")) if "scheduled_for" in payload else post.scheduled_for
    display_timezone = _display_timezone(payload.get("display_timezone", post.display_timezone))
    message, media_url = payload.get("message", post.message) or "", payload.get("media_url", post.media_url) or None
    if not dt or not display_timezone or not isinstance(message, str) or (media_url and not isinstance(media_url, str)):
        return _bad_request("Provide valid schedule time, timezone, message and media URL.")
    media_url, error = _target_media(post.account, media_url)
    if error:
        return error
    candidate = ScheduledPost(account=post.account, platform=post.platform, message=message, media_url=media_url, scheduled_for=dt)
    try:
        candidate.full_clean()
    except ValidationError as exc:
        return _bad_request("Validation failed", {"messages": exc.messages})
    with transaction.atomic():
        current = eligible.select_for_update(of=("self",)).filter(updated_at=post.updated_at).first()
        if current is None:
            return _conflict()
        account = ConnectedAccount.objects.select_for_update().filter(pk=post.account_id, user=request.user).first()
        if account is None or not account.is_active or account.token_generation != post.account.token_generation:
            return _conflict()
        now = timezone.now()
        next_attempt = max(dt, now)
        if post.platform == INSTAGRAM:
            next_attempt = _next_available_instagram_slot(post.account, next_attempt, exclude_id=post.id)
        current_leg = ScheduledPost.objects.filter(pk=post.pk, owner=request.user)
        updating = _retryable(current_leg) if is_retry else _modifiable(current_leg)
        if not updating.update(
            status=POST_STATUS_PENDING, stage="", blocked_reason="", error_message="",
            message=message.strip(), media_url=media_url, scheduled_for=dt, display_timezone=display_timezone,
            next_attempt_at=next_attempt, claim_generation=current.claim_generation + 1, lease_expires_at=None,
            attempt_count=0, final_write_started_at=None, ig_container_id="", ig_container_created_at=None, updated_at=now,
        ):
            return _conflict()
        if post.scheduled_for != dt:
            ScheduleChange.objects.create(
                post=post, changed_by=request.user, previous_scheduled_for=post.scheduled_for, scheduled_for=dt,
            )
    return JsonResponse(_leg(legs.get()))
