import logging

from core.constants import FACEBOOK, POST_STATUS_PUBLISHED
from core.exceptions import MetaAPIError
from core.services.meta_client import MetaClient
from integrations.models import ConnectedAccount
from publishing.models import ScheduledPost

from .models import InsightSnapshot

logger = logging.getLogger("analytics")


def _first_metric_value(insights: list[dict], names: list[str]):
    # Respect priority order from `names` (e.g. followers_count before fan_count).
    for target_name in names:
        for metric in insights:
            if metric.get("name") != target_name:
                continue
            values = metric.get("values") or []
            if values and isinstance(values[0], dict):
                value = values[0].get("value")
                if value is not None:
                    return value
    return None


def _get_published_posts(account: ConnectedAccount) -> list[dict]:
    rows = list(
        ScheduledPost.objects.filter(account=account, status=POST_STATUS_PUBLISHED)
        .values("id", "message", "media_url", "external_post_id", "published_at", "scheduled_for")
        .order_by("-published_at", "-id")[:50]
    )
    client = MetaClient()
    enriched_rows = []
    for row in rows:
        stats = {
            "total_views": None,
            "total_likes": None,
            "total_comments": None,
        }
        if account.platform == FACEBOOK and row.get("external_post_id"):
            try:
                stats = client.fetch_facebook_post_stats(
                    post_id=row["external_post_id"],
                    page_access_token=account.access_token,
                )
            except MetaAPIError as exc:
                logger.warning(
                    "failed to fetch post stats account_id=%s post_id=%s error=%s",
                    account.id,
                    row.get("external_post_id"),
                    str(exc),
                )
                stats["stats_error"] = str(exc)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "failed to fetch post stats account_id=%s post_id=%s error=%s",
                    account.id,
                    row.get("external_post_id"),
                    str(exc),
                )
                stats["stats_error"] = str(exc)

        enriched_rows.append(
            {
                "id": row["id"],
                "message": row["message"],
                "media_url": row["media_url"],
                "published_at": row["published_at"],
                "scheduled_for": row["scheduled_for"],
                "total_views": stats.get("total_views"),
                "total_likes": stats.get("total_likes"),
                "total_comments": stats.get("total_comments"),
                "reason": stats.get("stats_error"),
            }
        )

    return enriched_rows


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
        "account_id": account.id,
        "page_id": account.page_id,
        "page_name": account.page_name,
        "platform": platform,
        "insights": insights,
        "summary": {
            "total_followers": total_followers,
            "total_following": 0 if total_following is None else total_following,
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
