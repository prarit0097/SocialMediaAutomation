import logging

from core.constants import FACEBOOK, POST_STATUS_PUBLISHED
from core.exceptions import MetaAPIError
from core.services.meta_client import MetaClient
from integrations.models import ConnectedAccount
from publishing.models import ScheduledPost

from .models import InsightSnapshot

logger = logging.getLogger("analytics")


def _metric_entry_value(metric: dict):
    total_value = metric.get("total_value")
    if isinstance(total_value, dict):
        value = total_value.get("value")
        if value is not None:
            return value

    values = metric.get("values") or []
    if values and isinstance(values[-1], dict):
        value = values[-1].get("value")
        if value is not None:
            return value

    return None


def _first_metric_value(insights: list[dict], names: list[str]):
    # Respect priority order from `names` (e.g. followers_count before fan_count).
    for target_name in names:
        for metric in insights:
            if metric.get("name") != target_name:
                continue
            value = _metric_entry_value(metric)
            if value is not None:
                return value
    return None


def _get_published_posts(account: ConnectedAccount, include_post_stats: bool = True) -> list[dict]:
    client = MetaClient()

    if account.platform == FACEBOOK:
        try:
            page_posts = client.fetch_facebook_published_posts(
                page_id=account.page_id,
                page_access_token=account.access_token,
                limit=50,
            )
            enriched_rows = []
            for post in page_posts:
                stats = {
                    "total_views": None,
                    "total_likes": None,
                    "total_comments": None,
                    "stats_error": None,
                }
                if include_post_stats and post.get("id"):
                    try:
                        stats = client.fetch_facebook_post_stats(
                            post_id=post["id"],
                            page_access_token=account.access_token,
                        )
                    except MetaAPIError as exc:
                        logger.warning(
                            "failed to fetch post stats account_id=%s post_id=%s error=%s",
                            account.id,
                            post.get("id"),
                            str(exc),
                        )
                        stats["stats_error"] = str(exc)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "failed to fetch post stats account_id=%s post_id=%s error=%s",
                            account.id,
                            post.get("id"),
                            str(exc),
                        )
                        stats["stats_error"] = str(exc)

                attachment_data = ((post.get("attachments") or {}).get("data") or [{}])[0]
                subattachments = ((attachment_data.get("subattachments") or {}).get("data") or [{}])[0]
                media_url = (
                    post.get("full_picture")
                    or (attachment_data.get("media") or {}).get("image", {}).get("src")
                    or (subattachments.get("media") or {}).get("image", {}).get("src")
                    or attachment_data.get("url")
                )
                enriched_rows.append(
                    {
                        "id": post.get("id"),
                        "message": post.get("message"),
                        "media_url": media_url,
                        "published_at": post.get("created_time"),
                        "scheduled_for": None,
                        "total_views": stats.get("total_views"),
                        "total_likes": stats.get("total_likes"),
                        "total_comments": stats.get("total_comments"),
                        "reason": stats.get("stats_error"),
                    }
                )
            return enriched_rows
        except MetaAPIError as exc:
            logger.warning("failed to fetch facebook page posts account_id=%s error=%s", account.id, str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to fetch facebook page posts account_id=%s error=%s", account.id, str(exc))
    else:
        try:
            ig_user_id = account.ig_user_id or account.page_id
            ig_posts = client.fetch_instagram_published_posts(
                ig_user_id=ig_user_id,
                page_access_token=account.access_token,
                limit=50,
            )
            enriched_rows = []
            for post in ig_posts:
                stats = {
                    "total_views": None,
                    "total_likes": post.get("like_count"),
                    "total_comments": post.get("comments_count"),
                    "stats_error": None,
                }
                if include_post_stats and post.get("id"):
                    try:
                        stats = client.fetch_instagram_media_stats(
                            media_id=post["id"],
                            page_access_token=account.access_token,
                        )
                        # Keep already-fetched node counters if insights response omits them.
                        if stats.get("total_likes") is None:
                            stats["total_likes"] = post.get("like_count")
                        if stats.get("total_comments") is None:
                            stats["total_comments"] = post.get("comments_count")
                    except MetaAPIError as exc:
                        logger.warning(
                            "failed to fetch instagram media stats account_id=%s media_id=%s error=%s",
                            account.id,
                            post.get("id"),
                            str(exc),
                        )
                        stats["stats_error"] = str(exc)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "failed to fetch instagram media stats account_id=%s media_id=%s error=%s",
                            account.id,
                            post.get("id"),
                            str(exc),
                        )
                        stats["stats_error"] = str(exc)

                enriched_rows.append(
                    {
                        "id": post.get("id"),
                        "message": post.get("caption"),
                        "media_url": post.get("thumbnail_url") or post.get("media_url"),
                        "published_at": post.get("timestamp"),
                        "scheduled_for": None,
                        "total_views": stats.get("total_views"),
                        "total_likes": stats.get("total_likes"),
                        "total_comments": stats.get("total_comments"),
                        "reason": stats.get("stats_error"),
                    }
                )
            return enriched_rows
        except MetaAPIError as exc:
            logger.warning("failed to fetch instagram posts account_id=%s error=%s", account.id, str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to fetch instagram posts account_id=%s error=%s", account.id, str(exc))

    # Fallback to posts scheduled through this app.
    rows = list(
        ScheduledPost.objects.filter(account=account, status=POST_STATUS_PUBLISHED)
        .values("id", "message", "media_url", "external_post_id", "published_at", "scheduled_for")
        .order_by("-published_at", "-id")[:50]
    )
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
    published_posts: list[dict] | None = None,
    include_generated_post_stats: bool = True,
    total_post_share_override: int | None = None,
) -> dict:
    fb_followers = _first_metric_value(insights, ["followers_count"])
    fb_fan_count = _first_metric_value(insights, ["fan_count"])
    ig_followers = _first_metric_value(insights, ["followers_count", "follower_count"])
    total_followers = ig_followers if platform == "instagram" else (fb_followers if fb_followers is not None else fb_fan_count)
    total_following = (
        _first_metric_value(insights, ["follows_count", "following_count"])
        if platform == "instagram"
        else fb_fan_count
    )
    total_media_count = _first_metric_value(insights, ["media_count"])
    if published_posts is None:
        published_posts = _get_published_posts(account, include_post_stats=include_generated_post_stats)

    total_post_share = total_post_share_override if total_post_share_override is not None else len(published_posts)
    if platform == "instagram" and total_media_count is not None:
        total_post_share = total_media_count

    return {
        "account_id": account.id,
        "page_id": account.page_id,
        "page_name": account.page_name,
        "platform": platform,
        "insights": insights,
        "summary": {
            "total_followers": total_followers,
            "total_following": 0 if total_following is None else total_following,
            "total_post_share": total_post_share,
        },
        "published_posts": published_posts,
        "snapshot_id": snapshot_id,
        "fetched_at": fetched_at.isoformat() if fetched_at else None,
        "cached": cached,
    }


def fetch_and_store_insights(account: ConnectedAccount) -> dict:
    client = MetaClient()
    total_post_share_override = None

    if account.platform == FACEBOOK:
        insights = client.fetch_facebook_insights(account.page_id, account.access_token)
        platform = FACEBOOK
        total_post_share_override = client.fetch_facebook_published_posts_count(account.page_id, account.access_token)
    else:
        insights = client.fetch_instagram_insights(account.ig_user_id or account.page_id, account.access_token)
        platform = "instagram"

    published_posts = _get_published_posts(account)
    snapshot = InsightSnapshot.objects.create(
        account=account,
        platform=platform,
        payload={
            "insights": insights,
            "published_posts": published_posts,
            "published_posts_count": total_post_share_override,
        },
    )

    return build_insight_response(
        account=account,
        platform=platform,
        insights=insights,
        snapshot_id=snapshot.id,
        fetched_at=snapshot.fetched_at,
        cached=False,
        published_posts=published_posts,
        total_post_share_override=total_post_share_override,
    )
