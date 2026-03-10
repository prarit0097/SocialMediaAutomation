from core.constants import FACEBOOK
from core.services.meta_client import MetaClient


def publish_scheduled_post(post):
    client = MetaClient()
    account = post.account

    if post.platform == FACEBOOK:
        if post.media_url:
            result = client.publish_facebook_photo(
                page_id=account.page_id,
                page_access_token=account.access_token,
                image_url=post.media_url,
                caption=(post.message or "").strip() or None,
            )
            return result.get("post_id") or result.get("id")

        result = client.publish_facebook_post(
            page_id=account.page_id,
            page_access_token=account.access_token,
            message=(post.message or "").strip(),
        )
        return result.get("id")

    creation = client.create_instagram_media(
        ig_user_id=account.ig_user_id or account.page_id,
        page_access_token=account.access_token,
        image_url=post.media_url,
        caption=post.message or "",
    )
    publish_result = client.publish_instagram_media(
        ig_user_id=account.ig_user_id or account.page_id,
        page_access_token=account.access_token,
        creation_id=creation.get("id"),
    )
    return publish_result.get("id")
