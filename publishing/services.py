import logging
from urllib.parse import urlparse

from core.constants import FACEBOOK
from core.exceptions import MetaPermanentError
from core.services.meta_client import MetaClient

logger = logging.getLogger("publishing")


VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v", ".avi"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def _media_extension(media_url: str) -> str:
    path = urlparse(media_url).path.lower()
    if "." not in path:
        return ""
    return path[path.rfind(".") :]


def publish_scheduled_post(post):
    client = MetaClient()
    account = post.account

    if post.platform == FACEBOOK:
        if post.media_url:
            ext = _media_extension(post.media_url)
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
                return result.get("id")

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

    ext = _media_extension(post.media_url or "")
    media_kind = "video" if ext in VIDEO_EXTENSIONS else "image"
    if ext and media_kind == "image" and ext not in IMAGE_EXTENSIONS:
        raise MetaPermanentError(f"Unsupported Instagram media type: {ext}")

    creation = client.create_instagram_media(
        ig_user_id=account.ig_user_id or account.page_id,
        page_access_token=account.access_token,
        media_url=post.media_url,
        caption=post.message or "",
        media_kind=media_kind,
    )
    publish_result = client.publish_instagram_media(
        ig_user_id=account.ig_user_id or account.page_id,
        page_access_token=account.access_token,
        creation_id=creation.get("id"),
    )
    return publish_result.get("id")
