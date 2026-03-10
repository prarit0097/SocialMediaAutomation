from core.constants import FACEBOOK, POST_STATUS_PUBLISHED
from core.services.meta_client import MetaClient
from integrations.models import ConnectedAccount
from publishing.models import ScheduledPost

from .models import InsightSnapshot


def _first_metric_value(insights: list[dict], names: list[str]):
    for metric in insights:
        if metric.get("name") in names:
            values = metric.get("values") or []
            if values and isinstance(values[0], dict):
                return values[0].get("value")
    return None


def _get_published_posts(account: ConnectedAccount) -> list[dict]:
    return list(
        ScheduledPost.objects.filter(account=account, status=POST_STATUS_PUBLISHED)
        .values("id", "message", "media_url", "external_post_id", "published_at", "scheduled_for")
        .order_by("-published_at", "-id")[:50]
    )


def build_insight_response(
    account: ConnectedAccount,
    platform: str,
    insights: list[dict],
    snapshot_id: int | None,
    fetched_at,
    cached: bool,
) -> dict:
    total_followers = _first_metric_value(insights, ["followers_count", "fan_count", "follower_count"])
    total_following = _first_metric_value(insights, ["follows_count", "following_count"])
    published_posts = _get_published_posts(account)

    return {
        "platform": platform,
        "insights": insights,
        "summary": {
            "total_followers": total_followers,
            "total_following": total_following,
            "total_post_share": len(published_posts),
        },
        "published_posts": published_posts,
        "snapshot_id": snapshot_id,
        "fetched_at": fetched_at.isoformat() if fetched_at else None,
        "cached": cached,
    }


def fetch_and_store_insights(account: ConnectedAccount) -> dict:
    client = MetaClient()

    if account.platform == FACEBOOK:
        insights = client.fetch_facebook_insights(account.page_id, account.access_token)
        platform = FACEBOOK
    else:
        insights = client.fetch_instagram_insights(account.ig_user_id or account.page_id, account.access_token)
        platform = "instagram"

    snapshot = InsightSnapshot.objects.create(
        account=account,
        platform=platform,
        payload={"insights": insights},
    )

    return build_insight_response(
        account=account,
        platform=platform,
        insights=insights,
        snapshot_id=snapshot.id,
        fetched_at=snapshot.fetched_at,
        cached=False,
    )
