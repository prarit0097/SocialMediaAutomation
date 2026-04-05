import logging
import math
from datetime import datetime, timedelta, timezone as dt_timezone
import re

from core.constants import FACEBOOK, POST_STATUS_PUBLISHED
from core.exceptions import MetaAPIError
from core.services.meta_client import MetaClient
from django.utils import timezone
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


def _coerce_numeric_value(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return None
        return int(value) if float(value).is_integer() else value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            parsed = float(raw)
        except ValueError:
            return None
        if not math.isfinite(parsed):
            return None
        return int(parsed) if parsed.is_integer() else parsed
    if isinstance(value, dict):
        total = 0
        found = False
        for item in value.values():
            parsed = _coerce_numeric_value(item)
            if parsed is None:
                continue
            total += parsed
            found = True
        return total if found else None
    return None


def _matching_metric(insights: list[dict], names: list[str]) -> dict | None:
    for target_name in names:
        for metric in insights:
            if metric.get("name") == target_name:
                return metric
    return None


def _metric_series(metric: dict) -> list[int | float]:
    total_value = metric.get("total_value")
    if isinstance(total_value, dict):
        value = _coerce_numeric_value(total_value.get("value"))
        return [] if value is None else [value]

    series = []
    for item in metric.get("values") or []:
        if not isinstance(item, dict):
            continue
        value = _coerce_numeric_value(item.get("value"))
        if value is None:
            continue
        series.append(value)
    return series


def _metric_value(insights: list[dict], names: list[str], strategy: str = "auto"):
    metric = _matching_metric(insights, names)
    if not metric:
        return None

    series = _metric_series(metric)
    if not series:
        return None

    if strategy == "sum":
        return sum(series[-7:])
    if strategy == "last":
        return series[-1]
    if strategy == "delta":
        if len(series) < 2:
            return 0
        return series[-1] - series[0]

    if metric.get("total_value") is not None:
        return series[-1]
    if str(metric.get("period") or "").lower() == "day":
        return sum(series[-7:])
    return series[-1]


def _parse_metric_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt_timezone.utc)
    if isinstance(value, str):
        normalized = value.strip()
        normalized = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", normalized)
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_timezone.utc)
    return None


def _aggregate_recent_post_metric(posts: list[dict], platform: str, field_name: str, days: int = 7):
    cutoff = timezone.now() - timedelta(days=days)
    total = 0
    found = False
    for row in posts or []:
        row_platform = str(row.get("platform") or "").lower()
        if row_platform and row_platform != platform:
            continue
        published_at = _parse_metric_datetime(row.get("published_at")) or _parse_metric_datetime(row.get("scheduled_for"))
        if not published_at or published_at < cutoff:
            continue
        value = _coerce_numeric_value(row.get(field_name))
        if value is None:
            continue
        total += value
        found = True
    return total if found else None


def _comparison_display_value(value):
    return "N/A" if value is None else value


def build_post_stats_summary(published_posts: list[dict] | None) -> dict:
    rows = published_posts or []
    total = len(rows)
    live_count = 0
    cached_fallback_count = 0
    missing_count = 0

    for row in rows:
        reason = str(row.get("reason") or "").lower()
        metrics = [
            row.get("total_views"),
            row.get("total_likes"),
            row.get("total_comments"),
            row.get("total_shares"),
            row.get("total_saves"),
        ]
        has_any_metric = any(value is not None and value != "" for value in metrics)

        if "showing last cached stats" in reason:
            cached_fallback_count += 1
            continue
        if has_any_metric:
            live_count += 1
            continue
        missing_count += 1

    return {
        "total_posts": total,
        "live_stats_posts": live_count,
        "cached_fallback_posts": cached_fallback_count,
        "missing_stats_posts": missing_count,
    }


def _latest_cached_post_stats_map(account: ConnectedAccount) -> dict[str, dict]:
    latest = InsightSnapshot.objects.filter(account=account).order_by("-fetched_at").only("payload").first()
    if not latest:
        return {}

    rows = ((latest.payload or {}).get("published_posts") or [])
    stats_by_id: dict[str, dict] = {}
    for row in rows:
        post_id = str(row.get("id") or "").strip()
        if not post_id:
            continue
        stats_by_id[post_id] = {
            "total_views": row.get("total_views"),
            "total_likes": row.get("total_likes"),
            "total_comments": row.get("total_comments"),
            "total_shares": row.get("total_shares"),
            "total_saves": row.get("total_saves"),
        }
    return stats_by_id


def _hydrate_missing_stats_from_cache(stats: dict, cached_stats: dict | None) -> bool:
    if not cached_stats:
        return False
    hydrated = False
    for key in ("total_views", "total_likes", "total_comments", "total_shares", "total_saves"):
        if stats.get(key) is None and cached_stats.get(key) is not None:
            stats[key] = cached_stats.get(key)
            hydrated = True
    if hydrated and stats.get("stats_error"):
        stats["stats_error"] = "Live post stats timed out; showing last cached stats."
    return hydrated


def build_comparison_rows(accounts: list[dict], published_posts: list[dict]) -> list[dict]:
    fb = next((row for row in accounts if row.get("platform") == FACEBOOK), {})
    ig = next((row for row in accounts if row.get("platform") == "instagram"), {})

    fb_summary = fb.get("summary", {}) or {}
    ig_summary = ig.get("summary", {}) or {}
    fb_insights = fb.get("insights", []) or []
    ig_insights = ig.get("insights", []) or []

    fb_recent_likes = _aggregate_recent_post_metric(published_posts, "facebook", "total_likes")
    fb_recent_comments = _aggregate_recent_post_metric(published_posts, "facebook", "total_comments")
    fb_recent_shares = _aggregate_recent_post_metric(published_posts, "facebook", "total_shares")

    rows = [
        {"metric": "Followers", "facebook": fb_summary.get("total_followers"), "instagram": ig_summary.get("total_followers"), "window": "Current", "group": "profile"},
        {"metric": "Following", "facebook": fb_summary.get("total_following"), "instagram": ig_summary.get("total_following"), "window": "Current", "group": "profile"},
        {"metric": "Media / Posts", "facebook": fb_summary.get("total_post_share"), "instagram": _metric_value(ig_insights, ["media_count"], strategy="last"), "window": "Current", "group": "profile"},
        {"metric": "Reach", "facebook": _metric_value(fb_insights, ["page_impressions_unique"], strategy="sum"), "instagram": _metric_value(ig_insights, ["reach"], strategy="sum"), "window": "Last 7 days", "group": "engagement"},
        {"metric": "Profile Views", "facebook": _metric_value(fb_insights, ["page_views_total"], strategy="sum"), "instagram": _metric_value(ig_insights, ["profile_views"], strategy="sum"), "window": "Last 7 days", "group": "engagement"},
        {"metric": "Accounts Engaged", "facebook": _metric_value(fb_insights, ["page_engaged_users"], strategy="sum"), "instagram": _metric_value(ig_insights, ["accounts_engaged"], strategy="sum"), "window": "Last 7 days", "group": "engagement"},
        {"metric": "Interactions", "facebook": _metric_value(fb_insights, ["page_post_engagements"], strategy="sum"), "instagram": _metric_value(ig_insights, ["total_interactions"], strategy="sum"), "window": "Last 7 days", "group": "engagement"},
        {"metric": "Views", "facebook": _metric_value(fb_insights, ["page_posts_impressions"], strategy="sum"), "instagram": _metric_value(ig_insights, ["views"], strategy="sum"), "window": "Last 7 days", "group": "performance"},
        {"metric": "Likes", "facebook": _metric_value(fb_insights, ["page_actions_post_reactions_like_total"], strategy="sum") or fb_recent_likes, "instagram": _metric_value(ig_insights, ["likes"], strategy="sum"), "window": "Last 7 days", "group": "performance"},
        {"metric": "Comments", "facebook": fb_recent_comments, "instagram": _metric_value(ig_insights, ["comments"], strategy="sum"), "window": "Last 7 days", "group": "performance"},
        {"metric": "Shares", "facebook": fb_recent_shares, "instagram": _metric_value(ig_insights, ["shares"], strategy="sum"), "window": "Last 7 days", "group": "performance"},
        {"metric": "Saves", "facebook": None, "instagram": _metric_value(ig_insights, ["saves"], strategy="sum"), "window": "Last 7 days", "group": "performance"},
        {"metric": "New Followers", "facebook": _metric_value(fb_insights, ["page_follows"], strategy="delta"), "instagram": _metric_value(ig_insights, ["follower_count"], strategy="sum"), "window": "Last 7 days", "group": "growth"},
        {"metric": "New Follows", "facebook": _metric_value(fb_insights, ["page_follows"], strategy="last"), "instagram": _metric_value(ig_insights, ["follows_count"], strategy="last"), "window": "Current", "group": "growth"},
    ]

    return [
        {
            "metric": row["metric"],
            "facebook": _comparison_display_value(row["facebook"]),
            "instagram": _comparison_display_value(row["instagram"]),
            "window": row["window"],
            "group": row["group"],
        }
        for row in rows
    ]


def _get_published_posts(
    account: ConnectedAccount,
    include_post_stats: bool = True,
    limit: int = 50,
    stats_limit: int | None = None,
) -> list[dict]:
    client = MetaClient()
    cached_stats_map = _latest_cached_post_stats_map(account)

    if account.platform == FACEBOOK:
        try:
            page_posts = client.fetch_facebook_published_posts(
                page_id=account.page_id,
                page_access_token=account.access_token,
                limit=limit,
            )
            enriched_rows = []
            for index, post in enumerate(page_posts):
                stats = {
                    "total_views": None,
                    "total_likes": None,
                    "total_comments": None,
                    "total_shares": None,
                    "total_saves": None,
                    "stats_error": None,
                }
                should_fetch_stats = include_post_stats and post.get("id")
                if stats_limit is not None and index >= stats_limit:
                    should_fetch_stats = False
                if should_fetch_stats:
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
                    finally:
                        cached_stats = cached_stats_map.get(str(post.get("id") or ""))
                        _hydrate_missing_stats_from_cache(stats, cached_stats)
                else:
                    cached_stats = cached_stats_map.get(str(post.get("id") or ""))
                    _hydrate_missing_stats_from_cache(stats, cached_stats)

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
                        "total_shares": stats.get("total_shares"),
                        "total_saves": stats.get("total_saves"),
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
                limit=limit,
            )
            enriched_rows = []
            for index, post in enumerate(ig_posts):
                stats = {
                    "total_views": None,
                    "total_likes": post.get("like_count"),
                    "total_comments": post.get("comments_count"),
                    "total_shares": None,
                    "total_saves": None,
                    "stats_error": None,
                }
                should_fetch_stats = include_post_stats and post.get("id")
                if stats_limit is not None and index >= stats_limit:
                    should_fetch_stats = False
                if should_fetch_stats:
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
                    finally:
                        cached_stats = cached_stats_map.get(str(post.get("id") or ""))
                        _hydrate_missing_stats_from_cache(stats, cached_stats)
                else:
                    cached_stats = cached_stats_map.get(str(post.get("id") or ""))
                    _hydrate_missing_stats_from_cache(stats, cached_stats)

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
                        "total_shares": stats.get("total_shares"),
                        "total_saves": stats.get("total_saves"),
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
        .order_by("-published_at", "-id")[:limit]
    )
    enriched_rows = []
    for index, row in enumerate(rows):
        stats = {
            "total_views": None,
            "total_likes": None,
            "total_comments": None,
            "total_shares": None,
            "total_saves": None,
        }
        should_fetch_stats = account.platform == FACEBOOK and row.get("external_post_id")
        if stats_limit is not None and index >= stats_limit:
            should_fetch_stats = False
        if should_fetch_stats:
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
            finally:
                cached_stats = cached_stats_map.get(str(row.get("external_post_id") or ""))
                _hydrate_missing_stats_from_cache(stats, cached_stats)
        else:
            cached_stats = cached_stats_map.get(str(row.get("external_post_id") or ""))
            _hydrate_missing_stats_from_cache(stats, cached_stats)

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
                "total_shares": stats.get("total_shares"),
                "total_saves": stats.get("total_saves"),
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
    total_following = _first_metric_value(insights, ["follows_count", "following_count"]) if platform == "instagram" else 0
    total_media_count = _first_metric_value(insights, ["media_count"])
    if published_posts is None:
        published_posts = _get_published_posts(account, include_post_stats=include_generated_post_stats)

    total_post_share = total_post_share_override if total_post_share_override is not None else len(published_posts)
    if platform == "instagram" and total_media_count is not None:
        total_post_share = total_media_count

    response = {
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
        "post_stats_summary": build_post_stats_summary(published_posts),
    }
    response["comparison_rows"] = build_comparison_rows(
        [response],
        [{**row, "platform": platform} for row in published_posts],
    )
    return response


def fetch_and_store_insights(
    account: ConnectedAccount,
    include_post_stats: bool = True,
    post_limit: int = 50,
    post_stats_limit: int | None = None,
    payload_metadata: dict | None = None,
) -> dict:
    client = MetaClient()
    total_post_share_override = None

    if account.platform == FACEBOOK:
        insights = client.fetch_facebook_insights(account.page_id, account.access_token)
        platform = FACEBOOK
        total_post_share_override = client.fetch_facebook_published_posts_count(account.page_id, account.access_token)
    else:
        insights = client.fetch_instagram_insights(account.ig_user_id or account.page_id, account.access_token)
        platform = "instagram"

    published_posts = _get_published_posts(
        account,
        include_post_stats=include_post_stats,
        limit=post_limit,
        stats_limit=post_stats_limit,
    )
    if not published_posts:
        logger.warning(
            "fetch_and_store_insights: published_posts empty account_id=%s platform=%s — "
            "Meta API may have failed or page has no posts",
            account.id, account.platform,
        )
    snapshot = InsightSnapshot.objects.create(
        account=account,
        platform=platform,
        payload={
            "insights": insights,
            "published_posts": published_posts,
            "published_posts_count": total_post_share_override,
            "metadata": payload_metadata or {},
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
