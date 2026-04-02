import logging
import os

from django.core.files.storage import default_storage

from core.constants import FACEBOOK
from core.exceptions import MetaPermanentError
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

    # For videos, prefer direct upload (resumable) so Meta doesn't need to
    # fetch from our server — avoids Content-Type / SSL / redirect issues.
    # For images, IG API still requires image_url (no resumable support).
    source_bytes, source_filename = None, None
    if media_kind == "video":
        source_bytes, source_filename = _read_local_media(post.media_url)

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

    client.wait_for_instagram_media_ready(
        creation_id=creation_id,
        page_access_token=account.access_token,
    )
    publish_result = client.publish_instagram_media(
        ig_user_id=ig_user_id,
        page_access_token=account.access_token,
        creation_id=creation_id,
    )
    return publish_result.get("id")
