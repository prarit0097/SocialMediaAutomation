import logging
import os
from datetime import timedelta
import time
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.utils import timezone

from core.constants import META_SCOPES
from core.exceptions import MetaAPIError, MetaPermanentError, MetaTransientError

logger = logging.getLogger("meta_client")

TRANSIENT_GRAPH_ERROR_CODES = {2, 4, 17, 32, 613}
TRANSIENT_GRAPH_ERROR_SUBCODES = {2207003, 2207027, 2207051}
MAX_PAGING_REQUESTS = 100

_MIME_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".m4v": "video/x-m4v", ".avi": "video/x-msvideo",
}


def _mime_type_for_extension(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    return _MIME_TYPES.get(ext, "application/octet-stream")


def _shared_session() -> requests.Session:
    """Return a module-level session with connection pooling.

    Re-uses TCP connections across MetaClient instances instead of
    opening a new connection per API call.  Thread-safe by default.
    """
    global _session
    if _session is None:
        s = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=20,
            pool_maxsize=50,
            max_retries=0,  # We handle retries ourselves.
        )
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        _session = s
    return _session


_session: requests.Session | None = None


class MetaClient:
    def __init__(self):
        self.base_url = f"https://graph.facebook.com/{settings.META_GRAPH_VERSION}"
        self._session = _shared_session()

    def _last_7_day_window(self) -> dict:
        until = timezone.now().date()
        since = until - timedelta(days=7)
        return {
            "since": since.isoformat(),
            "until": until.isoformat(),
        }

    def oauth_url(self, state: str, redirect_uri: str | None = None) -> str:
        target_redirect_uri = redirect_uri or settings.META_REDIRECT_URI
        params = urlencode(
            {
                "client_id": settings.META_APP_ID,
                "redirect_uri": target_redirect_uri,
                "state": state,
                "scope": ",".join(META_SCOPES),
                "response_type": "code",
                "auth_type": "rerequest",
            }
        )
        return f"https://www.facebook.com/{settings.META_GRAPH_VERSION}/dialog/oauth?{params}"

    def exchange_code_for_token(self, code: str, redirect_uri: str | None = None) -> dict:
        target_redirect_uri = redirect_uri or settings.META_REDIRECT_URI
        return self._get(
            "/oauth/access_token",
            {
                "client_id": settings.META_APP_ID,
                "client_secret": settings.META_APP_SECRET,
                "redirect_uri": target_redirect_uri,
                "code": code,
            },
        )

    def get_managed_pages(self, user_access_token: str) -> list[dict]:
        pages: list[dict] = []
        params = {
            "access_token": user_access_token,
            "fields": "id,name,access_token,instagram_business_account",
            "limit": 100,
        }
        response = self._get("/me/accounts", params)
        pages.extend(response.get("data", []))

        next_url = (response.get("paging") or {}).get("next")
        while next_url:
            response = self._get_by_url(next_url)
            pages.extend(response.get("data", []))
            next_url = (response.get("paging") or {}).get("next")

        return pages

    def fetch_facebook_published_posts(self, page_id: str, page_access_token: str, limit: int = 50) -> list[dict]:
        # Some pages reject richer field sets depending on role/permissions.
        # Try progressively smaller field sets so we still return FB posts
        # instead of showing a blank table when count endpoint succeeds.
        field_attempts = [
            "id,message,created_time,permalink_url,full_picture,attachments{media_type,media,url,subattachments}",
            "id,message,created_time,permalink_url,full_picture",
            "id,created_time,permalink_url",
        ]
        last_error: MetaAPIError | None = None

        for fields in field_attempts:
            try:
                posts: list[dict] = []
                params = {
                    "access_token": page_access_token,
                    "fields": fields,
                    "limit": min(limit, 100),
                }
                response = self._get(f"/{page_id}/published_posts", params)
                posts.extend(response.get("data", []))

                next_url = (response.get("paging") or {}).get("next")
                while next_url and len(posts) < limit:
                    response = self._get_by_url(next_url)
                    posts.extend(response.get("data", []))
                    next_url = (response.get("paging") or {}).get("next")

                return posts[:limit]
            except MetaAPIError as exc:
                last_error = exc
                continue

        if last_error:
            raise last_error
        return []

    def fetch_facebook_published_posts_count(self, page_id: str, page_access_token: str) -> int | None:
        response = self._get(
            f"/{page_id}/published_posts",
            {
                "access_token": page_access_token,
                "fields": "id",
                "limit": 100,
            },
        )
        count = len(response.get("data", []))
        next_url = (response.get("paging") or {}).get("next")
        pages_seen = 1

        while next_url and pages_seen < MAX_PAGING_REQUESTS:
            response = self._get_by_url(next_url)
            count += len(response.get("data", []))
            next_url = (response.get("paging") or {}).get("next")
            pages_seen += 1

        return count

    def publish_facebook_post(self, page_id: str, page_access_token: str, message: str) -> dict:
        return self._post(
            f"/{page_id}/feed",
            {
                "access_token": page_access_token,
                "message": message,
            },
        )

    def publish_facebook_photo(
        self,
        page_id: str,
        page_access_token: str,
        image_url: str | None = None,
        caption: str | None = None,
        source_bytes: bytes | None = None,
        source_filename: str | None = None,
    ) -> dict:
        payload = {
            "access_token": page_access_token,
            "published": "true",
        }
        if caption:
            # Graph API /{page}/photos uses "message" for the feed story text,
            # NOT "caption".  Using the wrong key causes the post text / hashtags
            # to be silently dropped, destroying reach and engagement.
            payload["message"] = caption

        files = None
        if source_bytes:
            # Direct multipart upload — same as native FB app.  Gives Meta the
            # original file at full quality instead of making their CDN re-fetch
            # from our server (which adds latency and can degrade quality).
            fname = source_filename or "photo.jpg"
            ct = _mime_type_for_extension(fname)
            files = {"source": (fname, source_bytes, ct)}
        elif image_url:
            payload["url"] = image_url

        return self._post(
            f"/{page_id}/photos",
            payload,
            files=files,
        )

    def publish_facebook_video(
        self,
        page_id: str,
        page_access_token: str,
        video_url: str | None = None,
        description: str | None = None,
        title: str | None = None,
        source_bytes: bytes | None = None,
        source_filename: str | None = None,
    ) -> dict:
        payload = {"access_token": page_access_token}
        if description:
            payload["description"] = description
        if title:
            payload["title"] = title

        files = None
        if source_bytes:
            fname = source_filename or "video.mp4"
            ct = _mime_type_for_extension(fname)
            files = {"source": (fname, source_bytes, ct)}
        elif video_url:
            payload["file_url"] = video_url

        return self._post(
            f"/{page_id}/videos",
            payload,
            timeout=120,
            files=files,
        )

    def create_instagram_media(
        self,
        ig_user_id: str,
        page_access_token: str,
        media_url: str,
        caption: str,
        media_kind: str = "image",
        source_bytes: bytes | None = None,
        source_filename: str | None = None,
    ) -> dict:
        payload = {
            "access_token": page_access_token,
            "caption": caption,
        }
        if media_kind == "video":
            # IG Graph now requires REELS for feed video publishing.
            payload["media_type"] = "REELS"
            payload["share_to_feed"] = "true"
            # Let Meta auto-select the best thumbnail frame (2000ms in).
            # This gives the algorithm a clean cover to distribute to Explore/Reels.
            payload["thumb_offset"] = "2000"

            if source_bytes:
                # Resumable upload: Meta downloads from its own infra instead
                # of fetching from our server — avoids Content-Type / SSL /
                # redirect failures that cause status_code=ERROR.
                try:
                    resumable_payload = {**payload, "upload_type": "resumable"}
                    container = self._post(f"/{ig_user_id}/media", resumable_payload)
                    upload_uri = container.get("uri")
                    if not upload_uri:
                        raise MetaTransientError(
                            f"IG resumable container missing upload URI. Response: {container}"
                        )
                    self._upload_ig_resumable_video(
                        upload_uri, page_access_token, source_bytes, source_filename,
                    )
                    return container
                except (MetaTransientError, MetaPermanentError) as resumable_exc:
                    # Resumable upload failed — fall back to video_url so
                    # the publish still has a chance to succeed.
                    logger.warning(
                        "IG resumable upload failed, falling back to video_url: %s",
                        resumable_exc,
                    )
                    payload["video_url"] = media_url
            else:
                payload["video_url"] = media_url
        else:
            payload["image_url"] = media_url
        return self._post(f"/{ig_user_id}/media", payload)

    def _upload_ig_resumable_video(
        self,
        upload_uri: str,
        access_token: str,
        source_bytes: bytes,
        source_filename: str | None = None,
    ) -> None:
        """Upload raw video bytes to an IG resumable-upload URI."""
        fname = source_filename or "video.mp4"
        content_type = _mime_type_for_extension(fname)
        headers = {
            "Authorization": f"OAuth {access_token}",
            "offset": "0",
            "file_size": str(len(source_bytes)),
            "Content-Type": content_type,
        }
        attempts = self._retry_attempts()
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                resp = requests.post(
                    upload_uri,
                    data=source_bytes,
                    headers=headers,
                    timeout=300,
                )
                if resp.status_code >= 400:
                    body = resp.text[:500]
                    raise MetaTransientError(
                        f"IG resumable upload HTTP {resp.status_code}: {body}"
                    )
                return
            except requests.RequestException as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(2.0 * attempt)
                    continue
        raise MetaTransientError(
            f"IG resumable upload failed after {attempts} attempts: {last_error}"
        )

    def publish_instagram_media(self, ig_user_id: str, page_access_token: str, creation_id: str) -> dict:
        return self._post(
            f"/{ig_user_id}/media_publish",
            {
                "access_token": page_access_token,
                "creation_id": creation_id,
            },
        )

    def check_ig_publishing_limit(
        self, ig_user_id: str, page_access_token: str,
    ) -> dict:
        """Check IG content publishing quota via official endpoint.

        Returns dict with keys: quota_usage, quota_total, quota_remaining.
        Raises MetaTransientError if the endpoint is unreachable.
        """
        try:
            data = self._get(
                f"/{ig_user_id}/content_publishing_limit",
                {
                    "access_token": page_access_token,
                    "fields": "config,quota_usage",
                },
            )
        except MetaPermanentError:
            # Some older accounts / permission sets don't expose this endpoint.
            # Treat as "unknown" — don't block publishing, let the actual
            # publish call fail if over quota.
            return {"quota_usage": 0, "quota_total": 25, "quota_remaining": 25}

        config = data.get("config") or {}
        quota_total = int(config.get("quota_total", 25))
        quota_usage = int(data.get("quota_usage", 0))
        return {
            "quota_usage": quota_usage,
            "quota_total": quota_total,
            "quota_remaining": max(0, quota_total - quota_usage),
        }

    def wait_for_instagram_media_ready(
        self,
        creation_id: str,
        page_access_token: str,
        timeout: int | None = None,
        poll_interval: int | None = None,
    ) -> dict:
        import random

        timeout = timeout if isinstance(timeout, int) and timeout > 0 else max(120, int(getattr(settings, "META_IG_READY_TIMEOUT", 300)))
        poll_interval = (
            poll_interval
            if isinstance(poll_interval, int) and poll_interval > 0
            else max(5, int(getattr(settings, "META_IG_READY_POLL_INTERVAL", 12)))
        )
        started = time.monotonic()
        latest_payload = {}
        latest_transient_error: MetaTransientError | None = None
        consecutive_rate_limits = 0
        # Brief initial pause + random jitter so parallel tasks don't all
        # fire their first poll at the exact same instant.
        initial_wait = min(8, poll_interval, timeout // 2) + random.uniform(0, 4)
        time.sleep(initial_wait)
        while time.monotonic() - started < timeout:
            try:
                latest_payload = self._get(
                    f"/{creation_id}",
                    {
                        "access_token": page_access_token,
                        "fields": "status,status_code,error_message",
                    },
                )
                latest_transient_error = None
                consecutive_rate_limits = 0
            except MetaTransientError as exc:
                latest_transient_error = exc
                consecutive_rate_limits += 1
                # Progressive backoff on rate limits: the more consecutive
                # failures, the longer we wait — gives other tasks room.
                rl_backoff = min(60, (poll_interval + 5) * consecutive_rate_limits) + random.uniform(0, 8)
                time.sleep(rl_backoff)
                continue
            status_code = str(latest_payload.get("status_code") or "").upper()
            status = str(latest_payload.get("status") or "").upper()
            if status_code in {"FINISHED", "PUBLISHED"} or status in {"FINISHED", "PUBLISHED"}:
                return latest_payload
            if status_code == "EXPIRED" or status == "EXPIRED":
                logger.warning("ig container expired creation_id=%s payload=%s", creation_id, latest_payload)
                raise MetaTransientError(
                    f"Instagram media container expired during processing. "
                    f"A fresh container will be created on retry. status_code={status_code or 'unknown'}"
                )
            if status_code in {"ERROR", "FAILED"} or status in {"ERROR", "FAILED"}:
                error_message = str(latest_payload.get("error_message") or "").strip()
                logger.warning(
                    "ig container processing error creation_id=%s status_code=%s status=%s error_message=%s payload=%s",
                    creation_id, status_code, status, error_message, latest_payload,
                )
                detail = f" Meta says: {error_message}" if error_message else ""
                raise MetaTransientError(
                    f"Instagram media processing returned {status_code or status}. "
                    f"Container will be recreated on retry.{detail} status_code={status_code or 'unknown'}"
                )
            # Jitter on normal polls too — prevents synchronized polling waves
            time.sleep(poll_interval + random.uniform(0, 5))

        if latest_transient_error:
            raise MetaTransientError(
                f"Instagram media status checks were rate-limited/transiently failing. Last error: {latest_transient_error}"
            )
        raise MetaTransientError("Instagram media processing did not finish in time. Retry will try again.")

    def fetch_facebook_insights(self, page_id: str, page_access_token: str) -> list[dict]:
        metrics = [
            "page_impressions_unique",
            "page_posts_impressions",
            "page_post_engagements",
            "page_actions_post_reactions_like_total",
            "page_views_total",
            "page_follows",
        ]
        insights: list[dict] = []
        params_window = self._last_7_day_window()

        for metric in metrics:
            try:
                data = self._get(
                    f"/{page_id}/insights",
                    {
                        "access_token": page_access_token,
                        "metric": metric,
                        "period": "day",
                        **params_window,
                    },
                )
                insights.extend(data.get("data", []))
            except MetaPermanentError as exc:
                message = str(exc).lower()
                if "valid insights metric" in message or "not available" in message:
                    continue
                raise

        page_data = self._get(
            f"/{page_id}",
            {
                "access_token": page_access_token,
                "fields": "fan_count,followers_count",
            },
        )

        def _append_counter_metric(metric_name: str, field_name: str) -> None:
            if field_name not in page_data:
                return
            if any(metric.get("name") == metric_name for metric in insights):
                return
            insights.append(
                {
                    "name": metric_name,
                    "values": [{"value": page_data.get(field_name)}],
                    "period": "lifetime",
                }
            )

        _append_counter_metric("fan_count", "fan_count")
        _append_counter_metric("followers_count", "followers_count")
        return insights

    def fetch_instagram_insights(self, ig_user_id: str, page_access_token: str) -> list[dict]:
        # Query metrics one-by-one so unsupported metrics do not fail the whole response.
        metrics = [
            "reach",
            "follower_count",
            "profile_views",
            "website_clicks",
            "accounts_engaged",
            "total_interactions",
            "likes",
            "comments",
            "shares",
            "saves",
            "views",
        ]
        insights: list[dict] = []
        total_value_metrics = {
            "profile_views",
            "website_clicks",
            "accounts_engaged",
            "total_interactions",
            "likes",
            "comments",
            "shares",
            "saves",
            "views",
        }
        lifetime_metrics = {
            "profile_views",
            "website_clicks",
            "accounts_engaged",
            "total_interactions",
            "likes",
            "comments",
            "shares",
            "saves",
            "views",
        }
        params_window = self._last_7_day_window()

        for metric in metrics:
            base_params = {
                "access_token": page_access_token,
                "metric": metric,
                **params_window,
            }
            attempt_params = [
                {**base_params, "period": "day"},
            ]
            if metric in total_value_metrics:
                attempt_params.append({**base_params, "period": "day", "metric_type": "total_value"})
                if metric in lifetime_metrics:
                    attempt_params.append({**base_params, "period": "lifetime", "metric_type": "total_value"})
                # Some Graph versions accept total_value metric_type without period.
                attempt_params.append({**base_params, "metric_type": "total_value"})

            last_message = ""
            for params in attempt_params:
                try:
                    data = self._get(f"/{ig_user_id}/insights", params)
                    insights.extend(data.get("data", []))
                    last_message = ""
                    break
                except MetaPermanentError as exc:
                    last_message = str(exc).lower()
                    if (
                        "must be one of the following values" in last_message
                        or "not available for this" in last_message
                        or "metric_type=total_value" in last_message
                    ):
                        continue
                    raise
            if last_message:
                continue

        # Always merge profile-level counters because follower_count is often day-delta style.
        profile_data = self._get(
            f"/{ig_user_id}",
            {
                "access_token": page_access_token,
                "fields": "followers_count,follows_count,media_count",
            },
        )

        def _append_counter_metric(metric_name: str, field_name: str) -> None:
            if field_name not in profile_data:
                return
            if any(m.get("name") == metric_name for m in insights):
                return
            insights.append(
                {
                    "name": metric_name,
                    "values": [{"value": profile_data.get(field_name)}],
                    "period": "lifetime",
                }
            )

        _append_counter_metric("followers_count", "followers_count")
        _append_counter_metric("follows_count", "follows_count")
        _append_counter_metric("media_count", "media_count")
        return insights

    def fetch_instagram_published_posts(self, ig_user_id: str, page_access_token: str, limit: int = 50) -> list[dict]:
        media: list[dict] = []
        params = {
            "access_token": page_access_token,
            "fields": "id,caption,media_type,media_product_type,media_url,thumbnail_url,timestamp,permalink,like_count,comments_count",
            "limit": min(limit, 100),
        }
        response = self._get(f"/{ig_user_id}/media", params)
        media.extend(response.get("data", []))

        next_url = (response.get("paging") or {}).get("next")
        while next_url and len(media) < limit:
            response = self._get_by_url(next_url)
            media.extend(response.get("data", []))
            next_url = (response.get("paging") or {}).get("next")

        return media[:limit]

    def fetch_instagram_media_stats(self, media_id: str, page_access_token: str) -> dict:
        likes_count = None
        comments_count = None
        views_count = None
        shares_count = None
        saves_count = None
        errors: list[str] = []
        stats_timeout = max(8, int(getattr(settings, "META_POST_STATS_TIMEOUT", 12)))

        try:
            media_data = self._get_with_transient_retry(
                f"/{media_id}",
                {
                    "access_token": page_access_token,
                    "fields": "like_count,comments_count",
                },
                stats_timeout,
            )
            likes_count = media_data.get("like_count")
            comments_count = media_data.get("comments_count")
        except MetaAPIError as exc:
            errors.append(f"media node: {exc}")

        # Instagram media insights metrics vary by media type and API version.
        # Query one-by-one with fallback params to maximize compatibility.
        metrics = ["views", "impressions", "reach", "likes", "comments", "shares", "saved"]
        total_value_metrics = {"views", "impressions", "reach", "likes", "comments", "shares", "saved"}

        for metric in metrics:
            params_list = [{"access_token": page_access_token, "metric": metric}]
            if metric in total_value_metrics:
                params_list.append({"access_token": page_access_token, "metric": metric, "metric_type": "total_value"})
                params_list.append(
                    {
                        "access_token": page_access_token,
                        "metric": metric,
                        "period": "lifetime",
                        "metric_type": "total_value",
                    }
                )

            metric_value = None
            for params in params_list:
                try:
                    insight_data = self._get_with_transient_retry(f"/{media_id}/insights", params, stats_timeout)
                    items = insight_data.get("data", [])
                    if not items:
                        continue
                    item = items[0]
                    if isinstance(item.get("total_value"), dict):
                        metric_value = item["total_value"].get("value")
                    if metric_value is None:
                        values = item.get("values") or []
                        if values and isinstance(values[0], dict):
                            metric_value = values[0].get("value")
                    if metric_value is not None:
                        break
                except MetaAPIError as exc:
                    message = str(exc).lower()
                    if (
                        "must be one of the following values" in message
                        or "not available for this media type" in message
                        or "metric_type=total_value" in message
                    ):
                        continue
                    errors.append(f"{metric}: {exc}")
                    break

            if metric_value is None:
                continue

            if metric == "views" and views_count is None:
                views_count = metric_value
            elif metric == "impressions" and views_count is None:
                # Fallback so rows are not blank when `views` is unavailable.
                views_count = metric_value
            elif metric == "likes" and likes_count is None:
                likes_count = metric_value
            elif metric == "comments" and comments_count is None:
                comments_count = metric_value
            elif metric == "shares" and shares_count is None:
                shares_count = metric_value
            elif metric == "saved" and saves_count is None:
                saves_count = metric_value

        stats_error = None
        if likes_count is None and comments_count is None and views_count is None and shares_count is None and saves_count is None and errors:
            stats_error = " | ".join(errors)

        return {
            "total_likes": likes_count,
            "total_comments": comments_count,
            "total_views": views_count,
            "total_shares": shares_count,
            "total_saves": saves_count,
            "stats_error": stats_error,
        }

    def fetch_facebook_post_stats(self, post_id: str, page_access_token: str) -> dict:
        likes_count = None
        comments_count = None
        views_count = None
        shares_count = None
        errors: list[str] = []
        stats_timeout = max(8, int(getattr(settings, "META_POST_STATS_TIMEOUT", 12)))
        target_post_id = str(post_id or "")

        # Facebook video publishing can return a video node id (numeric) while post insights are tied
        # to a post id (pageId_postId). Resolve once so stats lookups hit the right node.
        if "_" not in target_post_id:
            try:
                resolved = self._get_with_transient_retry(
                    f"/{target_post_id}",
                    {
                        "access_token": page_access_token,
                        "fields": "post_id",
                    },
                    stats_timeout,
                )
                resolved_post_id = str(resolved.get("post_id") or "").strip()
                if resolved_post_id:
                    target_post_id = resolved_post_id
            except MetaAPIError:
                # Keep original id if resolution is not available for this node.
                pass

        # Secondary fallback.
        try:
            post_data = self._get_with_transient_retry(
                f"/{target_post_id}",
                {
                    "access_token": page_access_token,
                    "fields": "reactions.summary(total_count).limit(0),comments.summary(total_count).limit(0)",
                },
                stats_timeout,
            )
            if likes_count is None:
                likes_count = (post_data.get("reactions") or {}).get("summary", {}).get("total_count")
            if comments_count is None:
                comments_count = (post_data.get("comments") or {}).get("summary", {}).get("total_count")
        except MetaAPIError as exc:
            errors.append(f"reactions/comments: {exc}")

        # Per-metric fallback to avoid failing on unsupported metric names.
        insight_metrics = [
            "post_impressions_unique",
            "post_reactions_by_type_total",
            "post_reactions_like_total",
            "post_activity_by_action_type",
        ]
        for metric in insight_metrics:
            try:
                insight_data = self._get_with_transient_retry(
                    f"/{target_post_id}/insights",
                    {
                        "access_token": page_access_token,
                        "metric": metric,
                    },
                    stats_timeout,
                )
                insights = insight_data.get("data", [])
                if not insights:
                    continue
                values = insights[0].get("values") or []
                if not values or not isinstance(values[0], dict):
                    continue
                metric_value = values[0].get("value")

                if metric == "post_impressions_unique" and views_count is None:
                    views_count = metric_value
                elif metric == "post_reactions_by_type_total" and likes_count is None and isinstance(metric_value, dict):
                    likes_count = sum(v for v in metric_value.values() if isinstance(v, int))
                elif metric == "post_reactions_like_total" and likes_count is None:
                    likes_count = metric_value
                elif metric == "post_activity_by_action_type" and comments_count is None and isinstance(metric_value, dict):
                    comments_count = metric_value.get("comment")
                    if shares_count is None:
                        shares_count = metric_value.get("share")
            except MetaAPIError as exc:
                errors.append(f"{metric}: {exc}")

        stats_error = None
        if likes_count is None and comments_count is None and views_count is None and shares_count is None:
            stats_error = " | ".join(errors) if errors else "Meta did not return post engagement metrics."
            try:
                token_debug = self.debug_token(page_access_token).get("data", {})
                granted_scopes = set(token_debug.get("scopes") or [])
                required_scopes = {"pages_read_engagement", "pages_read_user_content", "read_insights"}
                missing_scopes = sorted(required_scopes - granted_scopes)
                if missing_scopes:
                    stats_error = f"{stats_error} | missing_scopes: {', '.join(missing_scopes)}"
            except MetaAPIError:
                # Keep original stats_error if token debug fails.
                pass

        return {
            "total_likes": likes_count,
            "total_comments": comments_count,
            "total_views": views_count,
            "total_shares": shares_count,
            "total_saves": None,
            "stats_error": stats_error,
        }

    def debug_token(self, input_token: str) -> dict:
        app_access_token = f"{settings.META_APP_ID}|{settings.META_APP_SECRET}"
        return self._get(
            "/debug_token",
            {
                "input_token": input_token,
                "access_token": app_access_token,
            },
        )

    def _retry_attempts(self) -> int:
        try:
            configured = int(getattr(settings, "META_REQUEST_RETRY_ATTEMPTS", 2))
        except (TypeError, ValueError):
            configured = 2
        return 1 if configured < 1 else configured

    def _request_with_retry(self, method: str, url: str, *, timeout: int, params: dict | None = None, data: dict | None = None, files: dict | None = None):
        attempts = self._retry_attempts()
        last_error: Exception | None = None
        session = self._session
        for attempt in range(1, attempts + 1):
            try:
                if method == "GET":
                    return session.get(url, params=params, timeout=timeout)
                return session.post(url, data=data, files=files, timeout=timeout)
            except requests.RequestException as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(1.0 + attempt * 0.5)
                    continue
                raise MetaTransientError(
                    f"Meta API network error after {attempts} attempts: {exc}"
                ) from exc

        # Safety fallback; execution should have already raised above.
        raise MetaTransientError(f"Meta API network error: {last_error}")

    def _transient_retry_attempts(self) -> int:
        try:
            configured = int(getattr(settings, "META_POST_STATS_RETRIES", 2))
        except (TypeError, ValueError):
            configured = 2
        return 1 if configured < 1 else configured

    def _get_with_transient_retry(self, path: str, params: dict, timeout: int) -> dict:
        attempts = self._transient_retry_attempts()
        for attempt in range(1, attempts + 1):
            try:
                return self._get(path, params, timeout=timeout)
            except MetaTransientError:
                if attempt < attempts:
                    time.sleep(1.0 + attempt * 0.5)
                    continue
                raise

    def _get(self, path: str, params: dict, timeout: int = 20) -> dict:
        response = self._request_with_retry(
            "GET",
            f"{self.base_url}{path}",
            params=params,
            timeout=timeout,
        )
        return self._handle_response(response)

    def _get_by_url(self, url: str, timeout: int = 20) -> dict:
        response = self._request_with_retry("GET", url, timeout=timeout)
        return self._handle_response(response)

    def _post(self, path: str, data: dict, timeout: int = 60, files: dict | None = None) -> dict:
        response = self._request_with_retry(
            "POST",
            f"{self.base_url}{path}",
            data=data,
            timeout=timeout,
            files=files,
        )
        return self._handle_response(response)

    # ------------------------------------------------------------------ #
    # X-App-Usage / X-Page-Usage proactive throttle
    # ------------------------------------------------------------------ #
    # Meta returns these headers on every Graph API response.  When any
    # metric exceeds ~80 % we inject a short sleep so the next call doesn't
    # push us into a rate-limit error.  At >90 % we sleep longer.  This is
    # per Meta's best-practice guidance (see Executive Summary §5).
    _APP_USAGE_THROTTLE_PCT = 75   # start slowing down
    _APP_USAGE_HARD_PCT = 90       # aggressive backoff

    def _check_usage_headers(self, response: requests.Response) -> None:
        """Read X-App-Usage / X-Page-Usage and cache (non-blocking).

        Publishing tasks check the cached usage and self-throttle if needed.
        Web requests are NOT blocked — they just update the usage cache.
        """
        import json as _json
        from django.core.cache import cache as _cache

        for header in ("X-App-Usage", "X-Business-Use-Case-Usage", "X-Page-Usage"):
            raw = response.headers.get(header)
            if not raw:
                continue
            try:
                usage = _json.loads(raw)
            except (ValueError, TypeError):
                continue
            # X-App-Usage is {"call_count":N, "total_cputime":N, "total_time":N}
            # X-Page-Usage has the same shape but may be nested per page-id.
            pcts: list[float] = []
            if isinstance(usage, dict):
                for v in usage.values():
                    if isinstance(v, (int, float)):
                        pcts.append(float(v))
                    elif isinstance(v, list):
                        for item in v:
                            if isinstance(item, dict):
                                for vv in item.values():
                                    if isinstance(vv, (int, float)):
                                        pcts.append(float(vv))
            if not pcts:
                continue
            peak = max(pcts)
            # Just cache the peak usage; publishing tasks will check it
            # and throttle themselves. Don't block the web request.
            _cache.set(f"meta_usage:{header}", peak, timeout=60)
            if peak >= self._APP_USAGE_HARD_PCT:
                logger.warning("Meta %s at %.0f%% (cached for publishing throttle)", header, peak)
            elif peak >= self._APP_USAGE_THROTTLE_PCT:
                logger.info("Meta %s at %.0f%% (cached for publishing throttle)", header, peak)

    def _handle_response(self, response: requests.Response) -> dict:
        # Track X-App-Usage to proactively throttle when approaching limits.
        self._check_usage_headers(response)

        if response.status_code < 400:
            try:
                return response.json()
            except (ValueError, requests.exceptions.JSONDecodeError):
                raise MetaTransientError(
                    f"Meta returned HTTP {response.status_code} with non-JSON body",
                    status_code=response.status_code,
                )

        payload = {}
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text}

        error = payload.get("error", {})
        message = error.get("message", "Meta API request failed")
        code = error.get("code")
        subcode = error.get("error_subcode")
        user_msg = error.get("error_user_msg")
        user_title = error.get("error_user_title")
        details = []
        if code is not None:
            details.append(f"code={code}")
        if subcode is not None:
            details.append(f"subcode={subcode}")
        if user_title:
            details.append(f"title={user_title}")
        if user_msg:
            details.append(f"user_msg={user_msg}")
        if details:
            message = f"{message} ({', '.join(details)})"
        lower_msg = (message or "").lower()
        lower_user_msg = (user_msg or "").lower()

        # --- IG-specific permanent errors that should NOT be retried ---
        # Code 9 + subcode 2207042: 24-hour publishing limit exceeded.
        if code == 9 and subcode == 2207042:
            raise MetaPermanentError(
                f"Instagram 24-hour publishing limit reached for this account. "
                f"Remaining posts will be queued for tomorrow. ({message})",
                status_code=response.status_code, payload=payload,
            )
        # Code 368: duplicate/repeated content flagged as spam.
        if code == 368:
            raise MetaPermanentError(
                f"Instagram rejected this post as duplicate or repeated content. "
                f"Change the caption/hashtags and try again. ({message})",
                status_code=response.status_code, payload=payload,
            )
        # Code 25 + subcode 2207050: account restricted/inactive.
        if code == 25 and subcode == 2207050:
            raise MetaPermanentError(
                f"This Instagram account is restricted or inactive. "
                f"Log into the Instagram app and check for any restrictions. ({message})",
                status_code=response.status_code, payload=payload,
            )
        # Code 24 + subcode 2207006: container expired / media not found.
        if code == 24 and subcode == 2207006:
            raise MetaTransientError(
                f"Instagram media container expired or not found. "
                f"A fresh container will be created on retry. ({message})",
                status_code=response.status_code, payload=payload,
            )
        # Code 36003: invalid aspect ratio.
        if code == 36003:
            raise MetaPermanentError(
                f"Instagram rejected the media due to invalid aspect ratio. "
                f"Supported: 1:1, 4:5, 1.91:1 (images) or 9:16 (Reels). ({message})",
                status_code=response.status_code, payload=payload,
            )
        # "Media posted before" — duplicate detection, not retryable.
        if "media posted before" in lower_msg or "media has already been posted" in lower_msg:
            raise MetaPermanentError(
                f"Instagram flagged this as duplicate content. Change the media or caption. ({message})",
                status_code=response.status_code, payload=payload,
            )
        # Code 9 (general) without specific subcode: often transient server-side.
        if code == 9 and subcode not in {2207042, 2207050}:
            raise MetaTransientError(
                f"Instagram returned a server error. Auto-retry will attempt again. ({message})",
                status_code=response.status_code, payload=payload,
            )

        # --- General transient errors (retryable) ---
        if (
            code == -2
            or code in TRANSIENT_GRAPH_ERROR_CODES
            or subcode in TRANSIENT_GRAPH_ERROR_SUBCODES
            or "timeout" in (user_title or "").lower()
            or code == 9007
            or "media is not ready" in lower_user_msg
            or "media is not ready" in lower_msg
            or "try again later" in lower_msg
            or "rate limit" in lower_msg
            or "too many requests" in lower_msg
            or "temporarily unavailable" in lower_msg
            or "unknown error" in lower_msg
            or "an unknown error" in lower_msg
            or "request timed out" in lower_msg
            or "service temporarily" in lower_msg
            or "could not fetch" in lower_msg
            or "failed to fetch" in lower_msg
            or "connection reset" in lower_msg
            or "server error" in lower_msg
        ):
            raise MetaTransientError(message, status_code=response.status_code, payload=payload)
        if response.status_code >= 500 or response.status_code == 429:
            raise MetaTransientError(message, status_code=response.status_code, payload=payload)

        raise MetaPermanentError(message, status_code=response.status_code, payload=payload)
