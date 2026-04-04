import logging
import os

from django.core.cache import cache
from django.core.files.storage import default_storage

from core.constants import FACEBOOK
from core.exceptions import MetaAPIError, MetaPermanentError, MetaTransientError
from core.services.meta_client import MetaClient
from publishing.media_utils import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    ensure_public_media_fetchable,
    media_extension,
    prepare_instagram_media_url,
    resolve_local_media_storage_path,
)

logger = logging.getLogger("publishing")


def _check_meta_usage_and_throttle() -> None:
    """Publishing tasks call this to check cached Meta usage and self-throttle.

    Only called from Celery tasks (publishing context), not from web requests.
    If usage is high, sleeps to avoid pushing into rate limits.
    """
    import time as _time
    from django.core.cache import cache

    for header in ("X-App-Usage", "X-Business-Use-Case-Usage", "X-Page-Usage"):
        peak = cache.get(f"meta_usage:{header}")
        if peak is None:
            continue
        if peak >= 90:
            logger.warning("Meta usage at %.0f%% — throttling 15s", peak)
            _time.sleep(15)
        elif peak >= 75:
            logger.info("Meta usage at %.0f%% — throttling 5s", peak)
            _time.sleep(5)

TOKEN_INVALID_MARKERS = (
    "error validating access token",
    "code=190",
    "subcode=460",
    "access token has expired",
    "invalid oauth access token",
)


def is_invalid_token_error(value: str | Exception | None) -> bool:
    text = str(value or "").lower()
    return any(marker in text for marker in TOKEN_INVALID_MARKERS)


def token_reconnect_message(account, original_error: str | Exception) -> str:
    return (
        "Meta access token is invalid for this connected account. "
        "Reconnect the profile from Accounts -> Connect Facebook + Instagram, refresh the account list, "
        f"then retry this failed post for {account.page_name}. Original Meta error: {original_error}"
    )


def _read_local_media(media_url: str) -> tuple[bytes | None, str | None]:
    """Read file bytes from local storage for direct multipart upload to Meta."""
    storage_path = resolve_local_media_storage_path(media_url)
    if not storage_path or not default_storage.exists(storage_path):
        return None, None
    try:
        with default_storage.open(storage_path, "rb") as fh:
            return fh.read(), os.path.basename(storage_path)
    except OSError:
        return None, None


def _extract_video_title(caption: str | None) -> str | None:
    """Use first line of caption (max 100 chars) as FB video title."""
    if not caption:
        return None
    first_line = caption.split("\n", 1)[0].strip()
    if not first_line:
        return None
    return first_line[:100]


def publish_scheduled_post(post):
    client = MetaClient()
    account = post.account

    if post.platform == FACEBOOK:
        if post.media_url:
            ext = media_extension(post.media_url)
            caption = (post.message or "").strip() or None

            # Try to read the local file for direct multipart upload.
            # Direct upload gives Meta the original file at full quality
            # instead of making their CDN re-fetch from our server, which
            # is how the native FB/IG apps work and results in higher
            # engagement because Meta processes direct uploads with full
            # fidelity (no re-compression, no fetch timeouts, no CDN cache
            # of a lower-quality proxy version).
            source_bytes, source_filename = _read_local_media(post.media_url)

            if ext in VIDEO_EXTENSIONS:
                title = _extract_video_title(caption)
                logger.info(
                    "publishing facebook video post id=%s page_id=%s upload=%s",
                    post.id,
                    account.page_id,
                    "direct" if source_bytes else "url",
                )
                result = client.publish_facebook_video(
                    page_id=account.page_id,
                    page_access_token=account.access_token,
                    video_url=post.media_url,
                    description=caption,
                    title=title,
                    source_bytes=source_bytes,
                    source_filename=source_filename,
                )
                logger.info("facebook video publish response post id=%s response=%s", post.id, result)
                return result.get("post_id") or result.get("id")

            if ext and ext not in IMAGE_EXTENSIONS:
                raise MetaPermanentError(f"Unsupported Facebook media type: {ext}")

            logger.info(
                "publishing facebook photo post id=%s page_id=%s upload=%s",
                post.id,
                account.page_id,
                "direct" if source_bytes else "url",
            )
            result = client.publish_facebook_photo(
                page_id=account.page_id,
                page_access_token=account.access_token,
                image_url=post.media_url,
                caption=caption,
                source_bytes=source_bytes,
                source_filename=source_filename,
            )
            logger.info("facebook photo publish response post id=%s response=%s", post.id, result)
            return result.get("post_id") or result.get("id")

        logger.info("publishing facebook text post id=%s page_id=%s", post.id, account.page_id)
        result = client.publish_facebook_post(
            page_id=account.page_id,
            page_access_token=account.access_token,
            message=(post.message or "").strip(),
        )
        logger.info("facebook text publish response post id=%s response=%s", post.id, result)
        return result.get("id")

    if not (post.media_url or "").strip():
        raise MetaPermanentError(
            "Instagram posts require a media URL (image or video). "
            "Add media before scheduling an Instagram post."
        )

    ig_user_id = account.ig_user_id or account.page_id
    if not (ig_user_id or "").strip():
        raise MetaPermanentError(
            "Connected account is missing an Instagram User ID. "
            "Reconnect the profile from Accounts -> Connect Facebook + Instagram."
        )

    prepared_media_url = prepare_instagram_media_url(post.media_url)
    if prepared_media_url != post.media_url:
        post.media_url = prepared_media_url
        post.save(update_fields=["media_url", "updated_at"])

    ext = media_extension(post.media_url)
    media_kind = "video" if ext in VIDEO_EXTENSIONS else "image"
    if ext and media_kind == "image" and ext not in IMAGE_EXTENSIONS:
        raise MetaPermanentError(f"Unsupported Instagram media type: {ext}")

    # Check IG publishing quota before wasting API calls on container
    # creation.  The endpoint tells us how many posts this account has
    # left in the rolling 24-hour window.
    quota_cache_key = f"ig_quota_ok:{account.id}"
    if not cache.get(quota_cache_key):
        try:
            quota = client.check_ig_publishing_limit(
                ig_user_id=ig_user_id,
                page_access_token=account.access_token,
            )
            if quota["quota_remaining"] <= 0:
                raise MetaPermanentError(
                    f"Instagram 24-hour publishing limit reached for {account.page_name}. "
                    f"Used {quota['quota_usage']}/{quota['quota_total']}. "
                    f"Remaining posts will be queued for later."
                )
            # Cache the "ok" for 5 minutes to avoid burning API calls
            # on quota checks for every single post.
            cache.set(quota_cache_key, quota["quota_remaining"], timeout=300)
            logger.info(
                "ig quota check account=%s remaining=%s/%s",
                account.id, quota["quota_remaining"], quota["quota_total"],
            )
        except (MetaTransientError, MetaAPIError):
            # Quota endpoint unreachable or permission denied — proceed
            # with publish attempt and let actual publish fail if over quota.
            pass

    # Check if a previous attempt already created a container for this post.
    # This avoids creating duplicate IG media containers when a retry is
    # caused by rate-limited polling (container was created fine, we just
    # couldn't check its status).
    creation_cache_key = f"ig_creation:{post.id}"
    creation_id = cache.get(creation_cache_key)

    if not creation_id:
        # For videos, prefer direct upload (resumable) so Meta doesn't need to
        # fetch from our server — avoids Content-Type / SSL / redirect issues.
        # For images, IG API still requires image_url (no resumable support).
        source_bytes, source_filename = None, None
        if media_kind == "video" and not cache.get(f"ig_skip_resumable:{post.id}"):
            source_bytes, source_filename = _read_local_media(post.media_url)
        elif media_kind == "video":
            logger.info("skipping resumable upload for post id=%s (prior video processing errors)", post.id)

        if not source_bytes:
            # Fallback to URL-based: verify the URL is reachable by Meta.
            ensure_public_media_fetchable(post.media_url)

        creation = client.create_instagram_media(
            ig_user_id=ig_user_id,
            page_access_token=account.access_token,
            media_url=post.media_url,
            caption=post.message or "",
            media_kind=media_kind,
            source_bytes=source_bytes,
            source_filename=source_filename,
        )
        creation_id = creation.get("id")
        if not creation_id:
            raise MetaPermanentError(
                f"Instagram media container creation returned no ID. Response: {creation}"
            )
        # Cache for 30 minutes so retries can reuse the same container.
        cache.set(creation_cache_key, creation_id, timeout=1800)
        logger.info("ig container created post id=%s creation_id=%s", post.id, creation_id)
    else:
        logger.info("ig container reused from cache post id=%s creation_id=%s", post.id, creation_id)

    client.wait_for_instagram_media_ready(
        creation_id=creation_id,
        page_access_token=account.access_token,
    )
    publish_result = client.publish_instagram_media(
        ig_user_id=ig_user_id,
        page_access_token=account.access_token,
        creation_id=creation_id,
    )
    # Clean up — container is published, no need to cache it.
    cache.delete(creation_cache_key)
    return publish_result.get("id")
