import logging
import json
import re
from datetime import datetime, timedelta, timezone as dt_timezone
from collections import defaultdict
from numbers import Number

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import OperationalError, transaction
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from core.constants import FACEBOOK, INSTAGRAM
from core.exceptions import MetaAPIError
from integrations.models import ConnectedAccount
from publishing.models import ScheduledPost

from .ai_service import AIInsightsError, generate_profile_ai_insights
from .models import BulkInsightRefreshRun, InsightSnapshot
from .services import build_comparison_rows, build_insight_response, build_post_stats_summary, fetch_and_store_insights
from .tasks import refresh_account_insights_snapshot

logger = logging.getLogger("analytics")
ENGAGEMENT_SCORE_WEIGHTS = {
    "views": 0.03,
    "likes": 1.0,
    "comments": 1.4,
    "shares": 1.8,
    "saves": 1.6,
}


def _force_refresh_guard_payload(user=None) -> dict | None:
    try:
        horizon_minutes = max(5, int(getattr(settings, "FORCE_REFRESH_IG_GUARD_MINUTES", 20)))
    except (TypeError, ValueError):
        horizon_minutes = 20
    try:
        due_limit = max(1, int(getattr(settings, "FORCE_REFRESH_IG_GUARD_DUE_LIMIT", 1)))
    except (TypeError, ValueError):
        due_limit = 1

    now = timezone.now()
    ig_due_qs = ScheduledPost.objects.filter(
        platform="instagram",
        status="pending",
        scheduled_for__lte=now + timedelta(minutes=horizon_minutes),
    )
    ig_processing_qs = ScheduledPost.objects.filter(platform="instagram", status="processing")
    if user is not None:
        ig_due_qs = ig_due_qs.filter(account__user=user)
        ig_processing_qs = ig_processing_qs.filter(account__user=user)
    ig_due_soon = ig_due_qs.count()
    ig_processing = ig_processing_qs.count()
    if ig_due_soon < due_limit and ig_processing == 0:
        return None
    return {
        "error": "Instagram publishing window is busy",
        "details": (
            f"Force refresh is temporarily blocked because {ig_due_soon} Instagram post(s) are due within "
            f"{horizon_minutes} minute(s) and {ig_processing} are currently processing. "
            "Wait for the scheduler queue to clear, then retry force refresh."
        ),
        "ig_due_soon": ig_due_soon,
        "ig_processing": ig_processing,
        "guard_window_minutes": horizon_minutes,
    }


def _insight_cache_ttl() -> int:
    try:
        return max(5, int(getattr(settings, "INSIGHTS_RESPONSE_CACHE_TTL", 90)))
    except (TypeError, ValueError):
        return 90


def _queue_background_insight_refresh(account: ConnectedAccount) -> None:
    if not account.access_token:
        return
    lock_key = f"insight-background-refresh:{account.id}"
    if not cache.add(lock_key, 1, timeout=180):
        return
    try:
        refresh_account_insights_snapshot.apply_async(
            args=[account.id],
            kwargs={"force": False, "bulk_run_id": None},
            priority=2,
        )
        logger.info("background insight refresh queued account_id=%s", account.id)
    except Exception as exc:  # noqa: BLE001
        cache.delete(lock_key)
        logger.warning("background insight refresh enqueue failed account_id=%s error=%s", account.id, str(exc))


def _empty_insight_placeholder(account: ConnectedAccount) -> dict:
    platform = FACEBOOK if account.platform == FACEBOOK else INSTAGRAM
    payload = build_insight_response(
        account=account,
        platform=platform,
        insights=[],
        snapshot_id=None,
        fetched_at=None,
        cached=False,
        published_posts=[],
    )
    payload["pending_refresh"] = True
    payload["warning"] = (
        "Insights are being prepared in the background. Refresh again in a few seconds."
        if account.access_token
        else "No active token is available for this profile right now."
    )
    return payload


def _serialize_bulk_run(run: BulkInsightRefreshRun | None) -> dict:
    if not run:
        return {
            "has_active_run": False,
            "status": "idle",
            "progress_percent": 0,
        }

    completed = int(run.completed_count or 0)
    failed = int(run.failed_count or 0)
    queued = int(run.queued_count or 0)
    skipped = int(run.skipped_no_token or 0)
    enqueue_failed = int(run.enqueue_failed or 0)
    total = int(run.total_accounts or 0)
    processed = completed + failed + skipped + enqueue_failed
    denominator = total if total > 0 else max(queued, 1)
    progress_percent = min(100, int(round((processed / max(denominator, 1)) * 100)))
    in_progress = run.status == BulkInsightRefreshRun.STATUS_RUNNING

    return {
        "run_id": run.id,
        "has_active_run": in_progress,
        "status": run.status,
        "total_accounts": total,
        "queued_count": queued,
        "skipped_no_token": skipped,
        "enqueue_failed": enqueue_failed,
        "completed_count": completed,
        "failed_count": failed,
        "processed_count": processed,
        "progress_percent": progress_percent,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "auto_reconciled": bool(getattr(run, "_auto_reconciled", False)),
        "auto_reconcile_reason": getattr(run, "_auto_reconcile_reason", None),
    }


def _bulk_refresh_stale_minutes() -> int:
    try:
        return max(10, int(getattr(settings, "BULK_REFRESH_STALE_MINUTES", 45)))
    except (TypeError, ValueError):
        return 45


def _reconcile_bulk_run_progress(run: BulkInsightRefreshRun | None) -> BulkInsightRefreshRun | None:
    if not run or run.status != BulkInsightRefreshRun.STATUS_RUNNING:
        return run

    with transaction.atomic():
        locked = BulkInsightRefreshRun.objects.select_for_update().filter(id=run.id).first()
        if not locked or locked.status != BulkInsightRefreshRun.STATUS_RUNNING:
            return locked

        processed = int(locked.completed_count or 0) + int(locked.failed_count or 0) + int(locked.skipped_no_token or 0) + int(
            locked.enqueue_failed or 0
        )
        queued = int(locked.queued_count or 0)
        total = int(locked.total_accounts or 0)
        now = timezone.now()
        stale_cutoff = timedelta(minutes=_bulk_refresh_stale_minutes())

        # Fast-path finalize when counters already indicate completion.
        if queued > 0 and processed >= queued:
            locked.status = (
                BulkInsightRefreshRun.STATUS_COMPLETED_WITH_ERRORS
                if locked.failed_count > 0 or locked.enqueue_failed > 0
                else BulkInsightRefreshRun.STATUS_COMPLETED
            )
            locked.finished_at = now
            locked.save(update_fields=["status", "finished_at", "updated_at"])
            locked._auto_reconciled = True
            locked._auto_reconcile_reason = "counter_completion"
            return locked

        # Reconcile from persisted snapshots if a task result callback was lost.
        if locked.started_at:
            refreshed_accounts = (
                InsightSnapshot.objects.filter(fetched_at__gte=locked.started_at, account__is_active=True)
                .values("account_id")
                .distinct()
                .count()
            )
            inferred_completed = min(max(queued, 0), refreshed_accounts)
            minimum_processed = min(max(total, 0), inferred_completed + int(locked.skipped_no_token or 0) + int(locked.enqueue_failed or 0))
            if minimum_processed > processed:
                delta = minimum_processed - processed
                locked.completed_count = int(locked.completed_count or 0) + delta
                processed = minimum_processed
                locked.save(update_fields=["completed_count", "updated_at"])
                locked._auto_reconciled = True
                locked._auto_reconcile_reason = "snapshot_counter_repair"

        # Finalize long-stale runs to avoid indefinite "running" UI state.
        age = (now - locked.started_at) if locked.started_at else timedelta(0)
        since_update = (now - locked.updated_at) if locked.updated_at else timedelta(0)
        if queued > 0 and processed >= queued:
            locked.status = (
                BulkInsightRefreshRun.STATUS_COMPLETED_WITH_ERRORS
                if locked.failed_count > 0 or locked.enqueue_failed > 0
                else BulkInsightRefreshRun.STATUS_COMPLETED
            )
            locked.finished_at = now
            locked.save(update_fields=["status", "finished_at", "updated_at"])
            locked._auto_reconciled = True
            locked._auto_reconcile_reason = "counter_completion"
        elif queued > 0 and age >= stale_cutoff and since_update >= stale_cutoff:
            remaining = max(0, queued - processed)
            if remaining:
                locked.failed_count = int(locked.failed_count or 0) + remaining
            locked.status = BulkInsightRefreshRun.STATUS_COMPLETED_WITH_ERRORS
            locked.finished_at = now
            locked.save(update_fields=["failed_count", "status", "finished_at", "updated_at"])
            locked._auto_reconciled = True
            locked._auto_reconcile_reason = "stale_timeout_finalize"

        return locked


def _safe_reconcile_bulk_run_progress(run: BulkInsightRefreshRun | None) -> BulkInsightRefreshRun | None:
    try:
        return _reconcile_bulk_run_progress(run)
    except OperationalError as exc:
        logger.warning("bulk run reconcile skipped due to database lock run_id=%s error=%s", getattr(run, "id", None), exc)
        if run:
            run._db_lock_contention = True
        return run


def _single_insight_cache_key(account_id: int, snapshot_id: int, fetched_at_iso: str | None) -> str:
    return f"insight_response:v2:single:{account_id}:{snapshot_id}:{fetched_at_iso or 'na'}"


def _combined_insight_cache_key(
    primary_account_id: int,
    primary_snapshot_id,
    primary_fetched_at_iso: str | None,
    secondary_account_id: int,
    secondary_snapshot_id,
    secondary_fetched_at_iso: str | None,
) -> str:
    return (
        f"insight_response:v2:combined:{primary_account_id}:{primary_snapshot_id}:{primary_fetched_at_iso or 'na'}:"
        f"{secondary_account_id}:{secondary_snapshot_id}:{secondary_fetched_at_iso or 'na'}"
    )


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
        refresh_lock_key = f"insight-live-refresh-lock:{account.id}"
        if not cache.add(refresh_lock_key, request.user.id, timeout=120):
            return None, JsonResponse(
                {
                    "error": "Refresh already in progress for this profile",
                    "details": "Another refresh is currently running for this profile. Please wait a few seconds.",
                },
                status=429,
            )
        try:
            data = fetch_and_store_insights(
                account,
                include_post_stats=True,
                post_limit=20,
                post_stats_limit=20,
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
        finally:
            cache.delete(refresh_lock_key)
        logger.info("insights refreshed account_id=%s user_id=%s", account.id, request.user.id)
        return data, None

    latest = InsightSnapshot.objects.filter(account=account).order_by("-fetched_at").first()
    if latest:
        fetched_at_iso = latest.fetched_at.isoformat() if latest.fetched_at else None
        cache_key = _single_insight_cache_key(account.id, latest.id, fetched_at_iso)
        cached_response = cache.get(cache_key)
        if cached_response:
            return cached_response, None

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
        cache.set(cache_key, data, timeout=_insight_cache_ttl())
        return data, None

    if not force_refresh:
        _queue_background_insight_refresh(account)
        return _empty_insight_placeholder(account), None

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


def _resolve_linked_account(account: ConnectedAccount, user=None):
    extra = {}
    if user is not None:
        extra["user"] = user
    if account.platform == "facebook" and account.ig_user_id:
        return ConnectedAccount.objects.filter(platform="instagram", page_id=account.ig_user_id, **extra).first()
    if account.platform == "instagram":
        return ConnectedAccount.objects.filter(platform="facebook", ig_user_id=account.page_id, **extra).order_by("-updated_at").first()
    return None


def _parse_iso(value: str | None):
    if not value:
        return None
    normalized = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value.strip())
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=dt_timezone.utc)
        return parsed
    except ValueError:
        return None


def _published_post_sort_key(post: dict):
    return (
        _parse_iso(post.get("published_at"))
        or _parse_iso(post.get("scheduled_for"))
        or datetime.min.replace(tzinfo=dt_timezone.utc)
    )


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

    latest_candidates = [value for value in [_parse_iso(primary.get("fetched_at")), _parse_iso(secondary.get("fetched_at"))] if value]
    latest_fetched = max(latest_candidates, default=None)

    fb_summary = (fb or {}).get("summary", {})
    ig_summary = (ig or {}).get("summary", {})

    response = {
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
        "post_stats_summary": build_post_stats_summary(published_posts),
    }
    response["comparison_rows"] = build_comparison_rows(accounts, published_posts)
    return response


def _load_account_or_combined_insights(request: HttpRequest, account: ConnectedAccount, force_refresh: bool):
    primary_data, error_response = _load_single_account_insights(request, account, force_refresh, str(account.id))
    if error_response:
        return None, error_response

    linked_account = _resolve_linked_account(account, user=request.user)
    if not linked_account or linked_account.id == account.id:
        return primary_data, None

    secondary_data, error_response = _load_single_account_insights(
        request, linked_account, force_refresh, f"{account.id}:{linked_account.id}"
    )
    if error_response:
        primary_data["combined_partial"] = True
        secondary_error = _extract_error_message(
            error_response,
            f"Linked {linked_account.platform} insights unavailable.",
        )
        primary_data["warning"] = f"Linked {linked_account.platform} insights unavailable: {secondary_error}"
        return primary_data, None

    if not force_refresh:
        cache_key = _combined_insight_cache_key(
            account.id,
            primary_data.get("snapshot_id"),
            primary_data.get("fetched_at"),
            linked_account.id,
            secondary_data.get("snapshot_id"),
            secondary_data.get("fetched_at"),
        )
        cached_response = cache.get(cache_key)
        if cached_response:
            return cached_response, None

    combined = _build_combined_response(primary_data, secondary_data)
    if not force_refresh:
        cache.set(cache_key, combined, timeout=_insight_cache_ttl())
    return combined, None


def _coerce_numeric(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Number):
        return float(value)
    if isinstance(value, str):
        raw = value.strip().replace(",", "")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def _short_text(value: str | None, limit: int = 180) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def _normalize_posts_for_ai(data: dict) -> list[dict]:
    rows = []
    default_platform = str(data.get("platform") or "").lower()
    for row in data.get("published_posts", []) or []:
        platform = str(row.get("platform") or default_platform or "").lower()
        published_at = _parse_iso(row.get("published_at")) or _parse_iso(row.get("scheduled_for"))
        rows.append(
            {
                "id": row.get("id"),
                "platform": platform,
                "published_at": published_at,
                "message": row.get("message"),
                "media_url": row.get("media_url"),
                "views": _coerce_numeric(row.get("total_views")),
                "likes": _coerce_numeric(row.get("total_likes")),
                "comments": _coerce_numeric(row.get("total_comments")),
                "shares": _coerce_numeric(row.get("total_shares")),
                "saves": _coerce_numeric(row.get("total_saves")),
            }
        )
    rows.sort(key=lambda row: row.get("published_at") or datetime.min.replace(tzinfo=dt_timezone.utc), reverse=True)
    return rows


def _post_metric_total(posts: list[dict], field: str, days: int = 7, platform: str | None = None):
    cutoff = timezone.now() - timedelta(days=days)
    total = 0.0
    found = False
    for row in posts:
        if platform and row.get("platform") != platform:
            continue
        published_at = row.get("published_at")
        if not published_at or published_at < cutoff:
            continue
        value = row.get(field)
        if value is None:
            continue
        total += float(value)
        found = True
    return int(total) if found else None


def _post_count(posts: list[dict], days: int = 7, platform: str | None = None) -> int:
    cutoff = timezone.now() - timedelta(days=days)
    total = 0
    for row in posts:
        if platform and row.get("platform") != platform:
            continue
        published_at = row.get("published_at")
        if not published_at or published_at < cutoff:
            continue
        total += 1
    return total


def _top_posts_snapshot(posts: list[dict], limit: int = 5) -> list[dict]:
    scored = []
    for row in posts:
        score = 0.0
        for key, weight in ENGAGEMENT_SCORE_WEIGHTS.items():
            value = row.get(key)
            if value is None:
                continue
            score += float(value) * weight
        scored.append((score, row))
    scored.sort(key=lambda entry: entry[0], reverse=True)

    top_rows = []
    for score, row in scored[:limit]:
        top_rows.append(
            {
                "id": row.get("id"),
                "platform": row.get("platform"),
                "published_at": row.get("published_at").isoformat() if row.get("published_at") else None,
                "message_preview": _short_text(row.get("message")),
                "media_url": row.get("media_url"),
                "views": row.get("views"),
                "likes": row.get("likes"),
                "comments": row.get("comments"),
                "shares": row.get("shares"),
                "saves": row.get("saves"),
                "engagement_score": round(score, 2),
            }
        )
    return top_rows


def _infer_post_format(post: dict) -> str:
    media_url = str(post.get("media_url") or "").lower()
    message = str(post.get("message") or "").strip()
    if any(media_url.endswith(ext) for ext in (".mp4", ".mov", ".avi", ".mkv", ".webm")):
        return "reel"
    if any(media_url.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
        return "image quote" if len(message) <= 120 else "carousel"
    if len(message) >= 180:
        return "educational post"
    if "?" in message or "comment" in message.lower():
        return "CTA post"
    return "educational post"


def _suggested_topic(posts: list[dict]) -> str:
    for post in posts:
        message = str(post.get("message") or post.get("message_preview") or "").strip()
        if not message:
            continue
        hashtags = [word.lstrip("#") for word in message.split() if word.startswith("#") and len(word) > 2]
        if hashtags:
            return f"{hashtags[0].replace('_', ' ')}"
        words = [word.strip(".,!?") for word in message.split() if len(word.strip(".,!?")) > 3]
        if words:
            return " ".join(words[:6])
    return "educational pain-point breakdown"


def _best_format(posts: list[dict], platform: str, days: int = 30) -> dict:
    cutoff = timezone.now() - timedelta(days=days)
    grouped: dict[str, dict[str, float]] = defaultdict(lambda: {"score": 0.0, "count": 0})
    for post in posts:
        if post.get("platform") != platform:
            continue
        published_at = post.get("published_at")
        if not published_at or published_at < cutoff:
            continue
        key = _infer_post_format(post)
        grouped[key]["score"] += _engagement_score(post)
        grouped[key]["count"] += 1

    ranked = []
    for format_name, data in grouped.items():
        if data["count"] <= 0:
            continue
        ranked.append(
            {
                "format": format_name,
                "avg_score": round(data["score"] / data["count"], 2),
                "sample_posts": int(data["count"]),
            }
        )
    ranked.sort(key=lambda row: (row["avg_score"], row["sample_posts"]), reverse=True)
    return ranked[0] if ranked else {"format": "reel", "avg_score": None, "sample_posts": 0}


def _next_post_recommendation(posts: list[dict], data: dict) -> dict:
    platforms = [platform for platform in ("instagram", "facebook") if any(p.get("platform") == platform for p in posts)]
    if not platforms:
        raw_platform = str(data.get("platform") or "").lower()
        platforms = ["instagram", "facebook"] if raw_platform == "facebook+instagram" else ([raw_platform] if raw_platform else ["instagram"])

    ranked_platforms = []
    for platform in platforms:
        top_slot_rows = _best_time_slots(posts, platform=platform, limit=1)
        top_slot = top_slot_rows[0] if top_slot_rows else {}
        best_format = _best_format(posts, platform=platform)
        ranked_platforms.append(
            {
                "platform": platform,
                "best_time_window": top_slot.get("label") or "Not enough data",
                "slot_score": float(top_slot.get("avg_score") or 0),
                "best_format": best_format.get("format") or "reel",
                "sample_posts": int(best_format.get("sample_posts") or 0),
            }
        )
    ranked_platforms.sort(key=lambda row: (row["slot_score"], row["sample_posts"]), reverse=True)
    selected = ranked_platforms[0] if ranked_platforms else {
        "platform": "instagram",
        "best_time_window": "Not enough data",
        "best_format": "reel",
    }
    topic = _suggested_topic(_top_posts_snapshot(posts, limit=3))
    return {
        "platform_focus": selected["platform"],
        "best_time_window": selected["best_time_window"],
        "best_format": selected["best_format"],
        "suggested_topic": topic,
        "reasoning": (
            f"{selected['platform'].title()} has the strongest recent slot/format signal, so the next post should be a "
            f"{selected['best_format']} about {topic} in the {selected['best_time_window']} window."
        ),
    }


def _ai_context_payload(data: dict, focus: str):
    posts = _normalize_posts_for_ai(data)
    fb_posts_7 = _post_count(posts, days=7, platform="facebook")
    ig_posts_7 = _post_count(posts, days=7, platform="instagram")
    total_posts_7 = _post_count(posts, days=7)
    total_posts_30 = _post_count(posts, days=30)
    fb_avg_posts_7 = round(fb_posts_7 / 7, 2)
    ig_avg_posts_7 = round(ig_posts_7 / 7, 2)

    comparison_rows = data.get("comparison_rows") if isinstance(data.get("comparison_rows"), list) else []
    condensed_comparison = comparison_rows[:15]

    context = {
        "focus": focus or "general profile growth",
        "profile": {
            "page_name": data.get("page_name"),
            "account_id": data.get("account_id"),
            "platform": data.get("platform"),
            "combined": bool(data.get("combined")),
            "cached": bool(data.get("cached")),
            "fetched_at": data.get("fetched_at"),
            "warning": data.get("warning") if data.get("warning") else "",
        },
        "summary": data.get("summary") or {},
        "posting_cadence": {
            "posts_last_24h": _post_count(posts, days=1),
            "posts_last_7d": total_posts_7,
            "posts_last_30d": total_posts_30,
            "avg_posts_per_day_last_7d": round(total_posts_7 / 7, 2),
            "avg_posts_per_day_last_30d": round(total_posts_30 / 30, 2),
            "facebook_posts_last_7d": fb_posts_7,
            "instagram_posts_last_7d": ig_posts_7,
            "facebook_avg_posts_per_day_last_7d": fb_avg_posts_7,
            "instagram_avg_posts_per_day_last_7d": ig_avg_posts_7,
        },
        "performance_last_7d": {
            "views": _post_metric_total(posts, "views", days=7),
            "likes": _post_metric_total(posts, "likes", days=7),
            "comments": _post_metric_total(posts, "comments", days=7),
            "shares": _post_metric_total(posts, "shares", days=7),
            "saves": _post_metric_total(posts, "saves", days=7),
        },
        "comparison_rows": condensed_comparison,
        "top_posts": _top_posts_snapshot(posts, limit=6),
        "historical_recommendations": _next_post_recommendation(posts, data),
    }
    return context


def _engagement_score(post: dict) -> float:
    return sum(float(post.get(metric) or 0) * weight for metric, weight in ENGAGEMENT_SCORE_WEIGHTS.items())


def _median(values: list[float]) -> float | None:
    numbers = sorted(float(v) for v in values if v is not None)
    if not numbers:
        return None
    mid = len(numbers) // 2
    if len(numbers) % 2:
        return numbers[mid]
    return (numbers[mid - 1] + numbers[mid]) / 2


def _weekday_slot(dt: datetime) -> tuple[str, str]:
    weekday = dt.strftime("%a")
    start_hour = (dt.hour // 2) * 2
    end_hour = (start_hour + 2) % 24
    return weekday, f"{start_hour:02d}:00-{end_hour:02d}:00"


def _best_time_slots(posts: list[dict], platform: str, days: int = 30, limit: int = 3) -> list[dict]:
    cutoff = timezone.now() - timedelta(days=days)
    buckets: dict[tuple[str, str], dict] = defaultdict(lambda: {"scores": [], "views": [], "count": 0})
    for post in posts:
        if post.get("platform") != platform:
            continue
        published_at = post.get("published_at")
        if not published_at or published_at < cutoff:
            continue
        key = _weekday_slot(published_at)
        buckets[key]["count"] += 1
        buckets[key]["scores"].append(_engagement_score(post))
        if post.get("views") is not None:
            buckets[key]["views"].append(float(post.get("views")))

    ranked = []
    for (weekday, slot), data in buckets.items():
        count = data["count"]
        if count <= 0:
            continue
        avg_score = sum(data["scores"]) / count
        avg_views = sum(data["views"]) / len(data["views"]) if data["views"] else None
        ranked.append(
            {
                "label": f"{weekday} {slot}",
                "avg_score": round(avg_score, 2),
                "avg_views": round(avg_views, 1) if avg_views is not None else None,
                "sample_posts": count,
            }
        )

    ranked.sort(key=lambda row: (row["avg_score"], row["sample_posts"]), reverse=True)
    return ranked[:limit]


def _caption_bucket(text: str | None) -> str:
    length = len((text or "").strip())
    if length <= 90:
        return "short"
    if length <= 180:
        return "medium"
    return "long"


def _caption_ab_recommendation(posts: list[dict], platform: str, days: int = 30) -> dict:
    cutoff = timezone.now() - timedelta(days=days)
    grouped: dict[str, list[float]] = {"short": [], "medium": [], "long": []}
    for post in posts:
        if post.get("platform") != platform:
            continue
        published_at = post.get("published_at")
        if not published_at or published_at < cutoff:
            continue
        grouped[_caption_bucket(post.get("message"))].append(_engagement_score(post))

    averages = {
        name: (sum(scores) / len(scores) if scores else None)
        for name, scores in grouped.items()
    }
    best_bucket = max(
        averages.keys(),
        key=lambda name: averages[name] if averages[name] is not None else float("-inf"),
    )
    if averages[best_bucket] is None:
        return {
            "primary_test": "Short vs Medium captions",
            "reasoning": "Not enough recent history for this profile. Start with one short and one medium caption test.",
        }
    challenger = "medium" if best_bucket != "medium" else "short"
    return {
        "primary_test": f"{best_bucket.title()} vs {challenger.title()} captions",
        "reasoning": (
            f"{best_bucket.title()} captions show the strongest average engagement score in last 30 days on {platform}. "
            f"Run 4-post A/B test with same creative, only caption length changed."
        ),
    }


def _platform_cadence(posts: list[dict], platform: str) -> dict:
    count_7d = _post_count(posts, days=7, platform=platform)
    avg_day = round(count_7d / 7, 2)
    if avg_day < 0.5:
        recommended = "1 post/day minimum (7-10 per week)"
    elif avg_day < 1.2:
        recommended = "1-2 posts/day (9-12 per week)"
    else:
        recommended = "Maintain 1-2 posts/day, optimize quality and timing"
    return {
        "posts_last_7d": count_7d,
        "avg_posts_per_day_7d": avg_day,
        "recommended_cadence": recommended,
    }


def _build_scheduler_assist_payload(data: dict) -> dict:
    posts = _normalize_posts_for_ai(data)
    platforms = sorted({str(p.get("platform") or "").lower() for p in posts if p.get("platform")})
    if not platforms:
        raw_platform = str(data.get("platform") or "").lower()
        if raw_platform == "facebook+instagram":
            platforms = ["facebook", "instagram"]
        elif raw_platform:
            platforms = [raw_platform]

    platform_payload = {}
    for platform in platforms:
        slots = _best_time_slots(posts, platform=platform)
        cadence = _platform_cadence(posts, platform=platform)
        platform_payload[platform] = {
            **cadence,
            "best_time_slots": slots,
            "next_best_window": slots[0]["label"] if slots else "Not enough posting history yet",
            "best_format": _best_format(posts, platform=platform),
            "next_topic": _suggested_topic([post for post in posts if post.get("platform") == platform][:5]),
            "caption_ab_test": _caption_ab_recommendation(posts, platform=platform),
        }

    return {
        "account_id": data.get("account_id"),
        "page_name": data.get("page_name"),
        "combined": bool(data.get("combined")),
        "generated_at": timezone.now().isoformat(),
        "platforms": platform_payload,
    }


def _build_low_distribution_alerts(posts: list[dict]) -> list[dict]:
    alerts = []
    now = timezone.now()
    for platform in ("facebook", "instagram"):
        history = [
            p for p in posts
            if p.get("platform") == platform and p.get("published_at") and p.get("views") is not None and p["published_at"] < now - timedelta(hours=24)
        ]
        baseline = _median([float(p.get("views") or 0) for p in history[:30]])
        if baseline is None:
            continue
        threshold = max(5.0, baseline * 0.15)
        recent = [
            p for p in posts
            if p.get("platform") == platform and p.get("published_at") and p.get("views") is not None and p["published_at"] >= now - timedelta(hours=24)
        ]
        for post in recent:
            views = float(post.get("views") or 0)
            if views >= threshold:
                continue
            alerts.append(
                {
                    "id": post.get("id"),
                    "platform": platform,
                    "published_at": post.get("published_at").isoformat() if post.get("published_at") else None,
                    "views": int(views),
                    "baseline_views": int(round(baseline)),
                    "recommendation": (
                        "Low early distribution detected. Test a sharper hook in first 90 chars and repost in next best time window."
                    ),
                }
            )
    return alerts[:8]


def _build_early_engagement_monitor(posts: list[dict]) -> list[dict]:
    monitor = []
    now = timezone.now()
    for post in posts:
        published_at = post.get("published_at")
        if not published_at:
            continue
        age_hours = (now - published_at).total_seconds() / 3600
        if age_hours < 0 or age_hours > 6:
            continue
        score = _engagement_score(post)
        if score < 5:
            status = "weak"
        elif score < 20:
            status = "average"
        else:
            status = "strong"
        monitor.append(
            {
                "id": post.get("id"),
                "platform": post.get("platform"),
                "published_at": published_at.isoformat(),
                "hours_since_publish": round(age_hours, 2),
                "views": post.get("views"),
                "likes": post.get("likes"),
                "comments": post.get("comments"),
                "status": status,
            }
        )
    monitor.sort(key=lambda row: row["hours_since_publish"])
    return monitor[:8]


@require_GET
@login_required
def account_insights(request: HttpRequest, account_id: int) -> JsonResponse:
    account = ConnectedAccount.objects.filter(id=account_id, user=request.user).first()
    if not account:
        return JsonResponse({"error": "Connected account not found"}, status=404)

    force_refresh = request.GET.get("refresh") == "1"
    payload, error_response = _load_account_or_combined_insights(request, account, force_refresh)
    if error_response:
        return error_response
    normalized_posts = _normalize_posts_for_ai(payload)
    payload["posting_strategy_assist"] = _build_scheduler_assist_payload(payload)
    payload["low_distribution_alerts"] = _build_low_distribution_alerts(normalized_posts)
    payload["early_engagement_monitor"] = _build_early_engagement_monitor(normalized_posts)
    return JsonResponse(payload)


@require_GET
@login_required
def scheduler_assist(request: HttpRequest, account_id: int) -> JsonResponse:
    account = ConnectedAccount.objects.filter(id=account_id, user=request.user).first()
    if not account:
        return JsonResponse({"error": "Connected account not found"}, status=404)
    payload, error_response = _load_account_or_combined_insights(request, account, force_refresh=False)
    if error_response:
        return error_response
    return JsonResponse(_build_scheduler_assist_payload(payload))


@require_POST
@login_required
def ai_profile_insights(request: HttpRequest, account_id: int) -> JsonResponse:
    account = ConnectedAccount.objects.filter(id=account_id, user=request.user).first()
    if not account:
        return JsonResponse({"error": "Connected account not found"}, status=404)

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    force_refresh = bool(payload.get("force_refresh"))
    focus = str(payload.get("focus") or "").strip()[:1200]

    insight_data, error_response = _load_account_or_combined_insights(request, account, force_refresh)
    if error_response:
        return error_response

    ai_context = _ai_context_payload(insight_data, focus)
    try:
        ai_analysis = generate_profile_ai_insights(ai_context, focus=focus)
    except AIInsightsError as exc:
        return JsonResponse(
            {
                "error": "AI insights unavailable",
                "details": _sanitize_error_text(str(exc), "OpenAI insight generation failed."),
            },
            status=502,
        )

    return JsonResponse(
        {
            "account_id": insight_data.get("account_id"),
            "page_name": insight_data.get("page_name"),
            "platform": insight_data.get("platform"),
            "combined": bool(insight_data.get("combined")),
            "snapshot_id": insight_data.get("snapshot_id"),
            "fetched_at": insight_data.get("fetched_at"),
            "cached": bool(insight_data.get("cached")),
            "generated_at": timezone.now().isoformat(),
            "model": settings.OPENAI_MODEL,
            "analysis": ai_analysis,
            "source_overview": {
                "posting_cadence": ai_context.get("posting_cadence"),
                "performance_last_7d": ai_context.get("performance_last_7d"),
                "comparison_rows_count": len(ai_context.get("comparison_rows") or []),
                "top_posts_count": len(ai_context.get("top_posts") or []),
                "historical_recommendations": ai_context.get("historical_recommendations") or {},
            },
        }
    )


@require_POST
@login_required
def force_refresh_all_accounts_insights(request: HttpRequest) -> JsonResponse:
    active_run = BulkInsightRefreshRun.objects.filter(
        user=request.user,
        status=BulkInsightRefreshRun.STATUS_RUNNING,
    ).order_by("-started_at").first()
    active_run = _safe_reconcile_bulk_run_progress(active_run)
    if active_run and active_run.status == BulkInsightRefreshRun.STATUS_RUNNING:
        payload = _serialize_bulk_run(active_run)
        payload.update(
            {
                "error": "Force refresh already running",
                "details": "A force refresh-all run is already active for this user. Wait for it to complete.",
            }
        )
        return JsonResponse(payload, status=409)

    guard_payload = _force_refresh_guard_payload(user=request.user)
    if guard_payload:
        return JsonResponse(guard_payload, status=409)

    accounts = list(ConnectedAccount.objects.filter(is_active=True, user=request.user).order_by("id"))
    total_accounts = len(accounts)
    queued = 0
    skipped_no_token = 0
    enqueue_failed = 0
    run = BulkInsightRefreshRun.objects.create(
        user=request.user,
        status=BulkInsightRefreshRun.STATUS_RUNNING,
        total_accounts=total_accounts,
    )

    for account in accounts:
        if not account.access_token:
            skipped_no_token += 1
            continue
        try:
            refresh_account_insights_snapshot.apply_async(
                args=[account.id],
                kwargs={"force": True, "bulk_run_id": run.id},
                priority=1,
            )
            queued += 1
        except Exception as exc:  # noqa: BLE001
            enqueue_failed += 1
            logger.warning(
                "bulk force refresh enqueue failed account_id=%s user_id=%s error=%s",
                account.id,
                request.user.id,
                str(exc),
            )

    run.queued_count = queued
    run.skipped_no_token = skipped_no_token
    run.enqueue_failed = enqueue_failed
    if queued == 0:
        if enqueue_failed > 0:
            run.status = BulkInsightRefreshRun.STATUS_COMPLETED_WITH_ERRORS
        else:
            run.status = BulkInsightRefreshRun.STATUS_COMPLETED
        run.finished_at = timezone.now()
    run.save(
        update_fields=[
            "queued_count",
            "skipped_no_token",
            "enqueue_failed",
            "status",
            "finished_at",
            "updated_at",
        ]
    )

    logger.info(
        "bulk force refresh queued user_id=%s run_id=%s total_accounts=%s queued=%s skipped_no_token=%s enqueue_failed=%s",
        request.user.id,
        run.id,
        total_accounts,
        queued,
        skipped_no_token,
        enqueue_failed,
    )

    payload = _serialize_bulk_run(run)
    payload.update(
        {
            "status": "queued",
            "queued": queued,
            "message": (
                f"Force refresh queued for {queued}/{total_accounts} connected profiles. "
                f"Skipped (no token): {skipped_no_token}. Queue errors: {enqueue_failed}."
            ),
            "queued_at": timezone.now().isoformat(),
        }
    )
    return JsonResponse(payload)


@require_GET
@login_required
def force_refresh_all_accounts_status(request: HttpRequest) -> JsonResponse:
    run = BulkInsightRefreshRun.objects.filter(user=request.user).order_by("-started_at").first()
    run = _safe_reconcile_bulk_run_progress(run)
    payload = _serialize_bulk_run(run)
    if run and getattr(run, "_db_lock_contention", False):
        payload["db_lock_contention"] = True
    return JsonResponse(payload)
