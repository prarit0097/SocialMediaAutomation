import logging
import json
import re
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from core.exceptions import MetaAPIError
from integrations.models import ConnectedAccount

from .models import InsightSnapshot
from .services import build_insight_response, fetch_and_store_insights

logger = logging.getLogger("analytics")


def _sanitize_error_text(value: str | None, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback

    compact = re.sub(r"\s+", " ", text)
    lowered = compact.lower()
    if "<!doctype html" in lowered or "<html" in lowered or "</html>" in lowered:
        if "err_ngrok_3004" in lowered:
            return "Public media URL is unavailable through ngrok right now. Restart ngrok and refresh again."
        return fallback

    if "err_ngrok_3004" in lowered:
        return "Public media URL is unavailable through ngrok right now. Restart ngrok and refresh again."

    if len(compact) > 240:
        return f"{compact[:237]}..."
    return compact


def _extract_error_message(error_response: JsonResponse, fallback: str) -> str:
    try:
        raw = error_response.content.decode("utf-8")
    except Exception:  # noqa: BLE001
        return fallback

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # Do not leak HTML error pages from upstream proxies like ngrok into the UI.
        return fallback

    if isinstance(payload, dict):
        details = payload.get("details")
        error = payload.get("error")
        if details:
            return _sanitize_error_text(details, fallback)
        if error:
            return _sanitize_error_text(error, fallback)
    return fallback


def _load_single_account_insights(
    request: HttpRequest,
    account: ConnectedAccount,
    force_refresh: bool,
    throttle_suffix: str,
):
    throttle_key = f"insight-refresh-{request.user.id}-{throttle_suffix}"
    if force_refresh:
        if not cache.add(throttle_key, 1, timeout=30):
            return None, JsonResponse({"error": "Too many refresh requests"}, status=429)
        try:
            data = fetch_and_store_insights(
                account,
                include_post_stats=True,
                post_limit=20,
                post_stats_limit=5,
            )
        except MetaAPIError as exc:
            logger.warning("insights fetch failed account_id=%s error=%s", account.id, exc)
            return None, JsonResponse(
                {
                    "error": "Failed to fetch insights from Meta",
                    "details": _sanitize_error_text(str(exc), "Meta temporarily returned an unreadable error response."),
                },
                status=502,
            )
        logger.info("insights refreshed account_id=%s user_id=%s", account.id, request.user.id)
        return data, None

    latest = InsightSnapshot.objects.filter(account=account).order_by("-fetched_at").first()
    if latest:
        payload = latest.payload or {}
        published_posts = payload.get("published_posts") if "published_posts" in payload else None
        if published_posts == []:
            published_posts = None
        data = build_insight_response(
            account=account,
            platform=latest.platform,
            insights=payload.get("insights", []),
            snapshot_id=latest.id,
            fetched_at=latest.fetched_at,
            cached=True,
            published_posts=published_posts,
            include_generated_post_stats=False,
            total_post_share_override=payload.get("published_posts_count"),
        )
        return data, None

    try:
        data = fetch_and_store_insights(account)
    except MetaAPIError as exc:
        logger.warning("insights fetch failed account_id=%s error=%s", account.id, exc)
        return None, JsonResponse(
            {
                "error": "Failed to fetch insights from Meta",
                "details": _sanitize_error_text(str(exc), "Meta temporarily returned an unreadable error response."),
            },
            status=502,
        )
    return data, None


def _resolve_linked_account(account: ConnectedAccount):
    if account.platform == "facebook" and account.ig_user_id:
        return ConnectedAccount.objects.filter(platform="instagram", page_id=account.ig_user_id).first()
    if account.platform == "instagram":
        return ConnectedAccount.objects.filter(platform="facebook", ig_user_id=account.page_id).order_by("-updated_at").first()
    return None


def _parse_iso(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _published_post_sort_key(post: dict):
    return _parse_iso(post.get("published_at")) or _parse_iso(post.get("scheduled_for")) or datetime.min


def _build_combined_response(primary: dict, secondary: dict) -> dict:
    accounts = [primary, secondary]
    accounts.sort(key=lambda row: 0 if row.get("platform") == "facebook" else 1)
    fb = next((row for row in accounts if row.get("platform") == "facebook"), None)
    ig = next((row for row in accounts if row.get("platform") == "instagram"), None)

    published_posts = []
    for row in accounts:
        for post in row.get("published_posts", []) or []:
            enriched = dict(post)
            enriched["platform"] = row.get("platform")
            enriched["account_id"] = row.get("account_id")
            enriched["source_page_name"] = row.get("page_name")
            published_posts.append(enriched)

    published_posts.sort(key=_published_post_sort_key, reverse=True)

    merged_metrics = []
    for row in accounts:
        for metric in row.get("insights", []) or []:
            enriched = dict(metric)
            enriched["platform"] = row.get("platform")
            merged_metrics.append(enriched)

    latest_fetched = max([_parse_iso(primary.get("fetched_at")), _parse_iso(secondary.get("fetched_at"))], default=None)

    fb_summary = (fb or {}).get("summary", {})
    ig_summary = (ig or {}).get("summary", {})

    return {
        "combined": True,
        "platform": "facebook+instagram",
        "account_id": primary.get("account_id"),
        "page_id": primary.get("page_id"),
        "page_name": primary.get("page_name"),
        "accounts": accounts,
        "insights": merged_metrics,
        "published_posts": published_posts,
        "summary": {
            "total_followers": (fb_summary.get("total_followers") or 0) + (ig_summary.get("total_followers") or 0),
            "total_following": (fb_summary.get("total_following") or 0) + (ig_summary.get("total_following") or 0),
            "total_post_share": (fb_summary.get("total_post_share") or 0) + (ig_summary.get("total_post_share") or 0),
            "facebook": fb_summary,
            "instagram": ig_summary,
        },
        "snapshot_id": f"{primary.get('snapshot_id')},{secondary.get('snapshot_id')}",
        "fetched_at": latest_fetched.isoformat() if latest_fetched else primary.get("fetched_at") or secondary.get("fetched_at"),
        "cached": bool(primary.get("cached")) and bool(secondary.get("cached")),
    }


@require_GET
@login_required
def account_insights(request: HttpRequest, account_id: int) -> JsonResponse:
    account = ConnectedAccount.objects.filter(id=account_id).first()
    if not account:
        return JsonResponse({"error": "Connected account not found"}, status=404)

    force_refresh = request.GET.get("refresh") == "1"
    primary_data, error_response = _load_single_account_insights(request, account, force_refresh, str(account_id))
    if error_response:
        return error_response

    linked_account = _resolve_linked_account(account)
    if not linked_account or linked_account.id == account.id:
        return JsonResponse(primary_data)

    secondary_data, error_response = _load_single_account_insights(
        request, linked_account, force_refresh, f"{account_id}:{linked_account.id}"
    )
    if error_response:
        primary_data["combined_partial"] = True
        secondary_error = _extract_error_message(
            error_response,
            f"Linked {linked_account.platform} insights unavailable.",
        )
        primary_data["warning"] = f"Linked {linked_account.platform} insights unavailable: {secondary_error}"
        return JsonResponse(primary_data)

    combined = _build_combined_response(primary_data, secondary_data)
    return JsonResponse(combined)
