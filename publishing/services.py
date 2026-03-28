import logging

from core.constants import FACEBOOK
from core.exceptions import MetaPermanentError
from core.services.meta_client import MetaClient
from publishing.media_utils import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    ensure_public_media_fetchable,
    media_extension,
    prepare_instagram_media_url,
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


def publish_scheduled_post(post):
    client = MetaClient()
    account = post.account

    if post.platform == FACEBOOK:
        if post.media_url:
            ext = media_extension(post.media_url)
            caption = (post.message or "").strip() or None

            if ext in VIDEO_EXTENSIONS:
                logger.info(
                    "publishing facebook video post id=%s page_id=%s media_url=%s",
                    post.id,
                    account.page_id,
                    post.media_url,
                )
                result = client.publish_facebook_video(
                    page_id=account.page_id,
                    page_access_token=account.access_token,
                    video_url=post.media_url,
                    description=caption,
                )
                logger.info("facebook video publish response post id=%s response=%s", post.id, result)
                return result.get("post_id") or result.get("id")

            if ext and ext not in IMAGE_EXTENSIONS:
                raise MetaPermanentError(f"Unsupported Facebook media type: {ext}")

            logger.info(
                "publishing facebook photo post id=%s page_id=%s media_url=%s",
                post.id,
                account.page_id,
                post.media_url,
            )
            result = client.publish_facebook_photo(
                page_id=account.page_id,
                page_access_token=account.access_token,
                image_url=post.media_url,
                caption=caption,
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

    ensure_public_media_fetchable(post.media_url)

    ext = media_extension(post.media_url)
    media_kind = "video" if ext in VIDEO_EXTENSIONS else "image"
    if ext and media_kind == "image" and ext not in IMAGE_EXTENSIONS:
        raise MetaPermanentError(f"Unsupported Instagram media type: {ext}")

    creation = client.create_instagram_media(
        ig_user_id=ig_user_id,
        page_access_token=account.access_token,
        media_url=post.media_url,
        caption=post.message or "",
        media_kind=media_kind,
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
