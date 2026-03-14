from urllib.parse import urlencode

from datetime import timedelta
import time

import requests
from django.conf import settings
from django.utils import timezone

from core.constants import META_SCOPES
from core.exceptions import MetaAPIError, MetaPermanentError, MetaTransientError

TRANSIENT_GRAPH_ERROR_CODES = {4, 17, 32, 613}
TRANSIENT_GRAPH_ERROR_SUBCODES = {2207003, 2207027}


class MetaClient:
    def __init__(self):
        self.base_url = f"https://graph.facebook.com/{settings.META_GRAPH_VERSION}"

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
        posts: list[dict] = []
        params = {
            "access_token": page_access_token,
            "fields": "id,message,created_time,permalink_url,full_picture,attachments{media_type,media,url,subattachments}",
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

        while next_url:
            response = self._get_by_url(next_url)
            count += len(response.get("data", []))
            next_url = (response.get("paging") or {}).get("next")

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
        image_url: str,
        caption: str | None = None,
    ) -> dict:
        payload = {
            "access_token": page_access_token,
            "url": image_url,
            "published": "true",
        }
        if caption:
            payload["caption"] = caption

        return self._post(
            f"/{page_id}/photos",
            payload,
        )

    def publish_facebook_video(
        self,
        page_id: str,
        page_access_token: str,
        video_url: str,
        description: str | None = None,
    ) -> dict:
        payload = {
            "access_token": page_access_token,
            "file_url": video_url,
        }
        if description:
            payload["description"] = description

        return self._post(
            f"/{page_id}/videos",
            payload,
            timeout=120,
        )

    def create_instagram_media(
        self,
        ig_user_id: str,
        page_access_token: str,
        media_url: str,
        caption: str,
        media_kind: str = "image",
    ) -> dict:
        payload = {
            "access_token": page_access_token,
            "caption": caption,
        }
        if media_kind == "video":
            # IG Graph now requires REELS for feed video publishing.
            payload["media_type"] = "REELS"
            payload["video_url"] = media_url
        else:
            payload["image_url"] = media_url
        return self._post(f"/{ig_user_id}/media", payload)

    def publish_instagram_media(self, ig_user_id: str, page_access_token: str, creation_id: str) -> dict:
        return self._post(
            f"/{ig_user_id}/media_publish",
            {
                "access_token": page_access_token,
                "creation_id": creation_id,
            },
        )

    def wait_for_instagram_media_ready(
        self,
        creation_id: str,
        page_access_token: str,
        timeout: int = 180,
        poll_interval: int = 5,
    ) -> dict:
        started = time.monotonic()
        latest_payload = {}
        latest_transient_error: MetaTransientError | None = None
        while time.monotonic() - started < timeout:
            try:
                latest_payload = self._get(
                    f"/{creation_id}",
                    {
                        "access_token": page_access_token,
                        "fields": "status,status_code",
                    },
                )
                latest_transient_error = None
            except MetaTransientError as exc:
                latest_transient_error = exc
                time.sleep(min(max(poll_interval + 1, 3), 12))
                continue
            status_code = str(latest_payload.get("status_code") or "").upper()
            status = str(latest_payload.get("status") or "").upper()
            if status_code in {"FINISHED", "PUBLISHED"} or status in {"FINISHED", "PUBLISHED"}:
                return latest_payload
            if status_code in {"ERROR", "EXPIRED", "FAILED"} or status in {"ERROR", "EXPIRED", "FAILED"}:
                raise MetaPermanentError(
                    f"Instagram media processing failed before publish. status_code={status_code or 'unknown'}"
                )
            time.sleep(poll_interval)

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

        # Secondary fallback.
        try:
            post_data = self._get_with_transient_retry(
                f"/{post_id}",
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
                    f"/{post_id}/insights",
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

    def _request_with_retry(self, method: str, url: str, *, timeout: int, params: dict | None = None, data: dict | None = None):
        attempts = self._retry_attempts()
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                if method == "GET":
                    return requests.get(url, params=params, timeout=timeout)
                return requests.post(url, data=data, timeout=timeout)
            except requests.RequestException as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(0.35 * attempt)
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
                    time.sleep(0.45 * attempt)
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

    def _post(self, path: str, data: dict, timeout: int = 60) -> dict:
        response = self._request_with_retry(
            "POST",
            f"{self.base_url}{path}",
            data=data,
            timeout=timeout,
        )
        return self._handle_response(response)

    def _handle_response(self, response: requests.Response) -> dict:
        if response.status_code < 400:
            return response.json()

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
        if (
            code == -2
            or code in TRANSIENT_GRAPH_ERROR_CODES
            or subcode in TRANSIENT_GRAPH_ERROR_SUBCODES
            or (user_title or "").lower() == "timeout"
            or code == 9007
            or "media is not ready" in (user_msg or "").lower()
        ):
            raise MetaTransientError(message, status_code=response.status_code, payload=payload)
        if response.status_code >= 500 or response.status_code == 429:
            raise MetaTransientError(message, status_code=response.status_code, payload=payload)

        raise MetaPermanentError(message, status_code=response.status_code, payload=payload)
