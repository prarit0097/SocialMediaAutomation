import os
from datetime import timedelta

from django.core.files.storage import default_storage
from django.utils import timezone

from core.constants import FACEBOOK, POST_STATUS_PROCESSING
from core.exceptions import MetaAPIError, MetaPermanentError, MetaPublishOutcomeUnknown, MetaTransientError
from core.services.meta_client import MetaClient, meta_app_over_budget
from publishing.media_utils import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    ensure_public_media_fetchable,
    media_extension,
    prepare_instagram_media_url,
    resolve_local_media_storage_path,
)


class InstagramQuotaExhausted(MetaTransientError):
    pass


class InstagramQuotaProbeUnavailable(MetaTransientError):
    pass


def _check_meta_usage_and_throttle():
    if meta_app_over_budget():
        raise MetaTransientError("Meta app usage is at capacity")


TOKEN_INVALID_MARKERS = (
    "error validating access token",
    "code=190",
    "subcode=460",
    "access token has expired",
    "invalid oauth access token",
)


def is_invalid_token_error(value):
    if isinstance(value, MetaAPIError):
        error = value.payload.get("error", {})
        if error.get("code") == 190:
            return True
    return any(marker in str(value or "").lower() for marker in TOKEN_INVALID_MARKERS)


def token_reconnect_message(account, original_error=None):
    return "Meta access token is invalid. Reconnect the profile from Accounts before scheduling or retrying."


def _read_local_media(media_url):
    storage_path = resolve_local_media_storage_path(media_url)
    if not storage_path or not default_storage.exists(storage_path):
        return None, None
    try:
        with default_storage.open(storage_path, "rb") as stream:
            return stream.read(), os.path.basename(storage_path)
    except OSError:
        return None, None


def _save_preparation(post, **fields):
    return type(post).objects.filter(
        pk=post.pk, status=POST_STATUS_PROCESSING, claim_generation=post.claim_generation,
        final_write_started_at__isnull=True,
    ).update(updated_at=timezone.now(), **fields)


def publish_scheduled_post(post, *, before_final_write):
    client = MetaClient()
    account = post.account
    caption = (post.message or "").strip()
    if post.platform == FACEBOOK:
        if not post.media_url:
            before_final_write()
            result = client.publish_facebook_post(
                page_id=account.page_id, page_access_token=account.access_token, message=caption,
            )
            return result.get("id")

        ext = media_extension(post.media_url)
        if ext and ext not in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
            raise MetaPermanentError("Unsupported Facebook media type.")
        if not resolve_local_media_storage_path(post.media_url):
            ensure_public_media_fetchable(post.media_url)
        source_bytes, source_filename = _read_local_media(post.media_url)
        before_final_write()
        if ext in VIDEO_EXTENSIONS:
            result = client.publish_facebook_video(
                page_id=account.page_id, page_access_token=account.access_token, video_url=post.media_url,
                description=caption or None, title=caption.splitlines()[0][:100] if caption else None,
                source_bytes=source_bytes, source_filename=source_filename,
            )
            return result.get("id") or "", result.get("post_id")

        result = client.publish_facebook_photo(
            page_id=account.page_id, page_access_token=account.access_token, image_url=post.media_url,
            caption=caption or None, source_bytes=source_bytes, source_filename=source_filename,
        )
        return result.get("post_id") or result.get("id")

    if not post.media_url:
        raise MetaPermanentError("Instagram posts require an image or video.")
    ig_user_id = account.ig_user_id or account.page_id
    if not ig_user_id:
        raise MetaPermanentError("Connected account is missing an Instagram User ID.")

    try:
        quota = client.check_ig_publishing_limit(ig_user_id=ig_user_id, page_access_token=account.access_token)
    except MetaAPIError as exc:
        if is_invalid_token_error(exc):
            raise
        raise InstagramQuotaProbeUnavailable("Instagram publishing quota could not be checked.") from exc
    if quota["quota_remaining"] <= 0:
        raise InstagramQuotaExhausted("Instagram publishing quota is exhausted.")

    media_url = prepare_instagram_media_url(post.media_url)
    if media_url != post.media_url:
        _save_preparation(post, media_url=media_url)
    ext = media_extension(media_url)
    if ext and ext not in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
        raise MetaPermanentError("Unsupported Instagram media type.")
    media_kind = "video" if ext in VIDEO_EXTENSIONS else "image"

    creation_id = post.ig_container_id
    if not post.ig_container_created_at or post.ig_container_created_at <= timezone.now() - timedelta(hours=23):
        creation_id = ""
    if not creation_id:
        source_bytes, source_filename = _read_local_media(media_url) if media_kind == "video" else (None, None)
        if not source_bytes:
            ensure_public_media_fetchable(media_url)
        creation = client.create_instagram_media(
            ig_user_id=ig_user_id, page_access_token=account.access_token, media_url=media_url,
            caption=caption, media_kind=media_kind, source_bytes=source_bytes, source_filename=source_filename,
        )
        creation_id = creation.get("id")
        if not creation_id:
            raise MetaTransientError("Instagram media container creation returned no ID.")
        _save_preparation(post, ig_container_id=creation_id, ig_container_created_at=timezone.now())

    container = client.wait_for_instagram_media_ready(creation_id=creation_id, page_access_token=account.access_token)
    if container.get("status_code") == "PUBLISHED":
        raise MetaPublishOutcomeUnknown("Instagram container has already been published.")
    before_final_write()
    publication = client.publish_instagram_media(
        ig_user_id=ig_user_id, page_access_token=account.access_token, creation_id=creation_id,
    )
    return publication.get("id")
