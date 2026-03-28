import json
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from analytics.ai_service import AIInsightsError, generate_content_calendar_plan
from analytics.models import InsightSnapshot
from analytics.views import _next_post_recommendation, _normalize_posts_for_ai
from integrations.models import ConnectedAccount

from .models import CalendarContentItem, ContentTag


def _bad_request(message: str) -> JsonResponse:
    return JsonResponse({"error": message}, status=400)


def _parse_json(request: HttpRequest):
    try:
        return json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return None


def _parse_iso_datetime(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def _parse_month_window(month_value: str | None):
    now = timezone.localtime()
    if month_value:
        try:
            year_str, month_str = month_value.split("-")
            year = int(year_str)
            month = int(month_str)
            start = timezone.make_aware(datetime(year, month, 1))
        except Exception:  # noqa: BLE001
            return None, None
    else:
        start = timezone.make_aware(datetime(now.year, now.month, 1))

    if start.month == 12:
        end = timezone.make_aware(datetime(start.year + 1, 1, 1))
    else:
        end = timezone.make_aware(datetime(start.year, start.month + 1, 1))
    return start, end


def _serialize_tag(tag: ContentTag) -> dict:
    return {
        "id": tag.id,
        "name": tag.name,
        "slug": tag.slug,
        "category": tag.category,
        "color": tag.color,
    }


def _serialize_item(item: CalendarContentItem) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "caption": item.caption,
        "start_at": item.start_at.isoformat(),
        "end_at": item.end_at.isoformat() if item.end_at else None,
        "platform": item.platform,
        "status": item.status,
        "notes": item.notes,
        "connected_account_id": item.connected_account_id,
        "connected_account_name": item.connected_account.page_name if item.connected_account else "",
        "tags": [_serialize_tag(tag) for tag in item.tags.all()],
    }


def _planner_account_context(_user, account_id: int | None) -> tuple[dict, dict]:
    if not account_id:
        return {}, {}
    account = ConnectedAccount.objects.filter(id=account_id, user=_user, is_active=True).first()
    if not account:
        return {}, {}
    snapshot = InsightSnapshot.objects.filter(account=account).order_by("-fetched_at", "-id").first()
    if not snapshot:
        return {
            "account_id": account.id,
            "page_name": account.page_name,
            "platform": account.platform,
        }, {}
    payload = snapshot.payload if isinstance(snapshot.payload, dict) else {}
    normalized_posts = _normalize_posts_for_ai(
        {
            "platform": account.platform,
            "published_posts": payload.get("published_posts") or [],
        }
    )
    return (
        {
            "account_id": account.id,
            "page_name": account.page_name,
            "platform": account.platform,
            "fetched_at": snapshot.fetched_at.isoformat() if snapshot.fetched_at else None,
            "summary": payload.get("summary") or {},
        },
        _next_post_recommendation(normalized_posts, {"platform": account.platform}) if normalized_posts else {},
    )


def _resolve_owned_connected_account(user, account_id) -> ConnectedAccount | None:
    if not account_id:
        return None
    return ConnectedAccount.objects.filter(id=account_id, user=user, is_active=True).first()


@require_GET
@login_required
def planning_tags(request: HttpRequest) -> JsonResponse:
    category = (request.GET.get("category") or "").strip()
    qs = ContentTag.objects.filter(owner=request.user)
    if category:
        qs = qs.filter(category=category)
    return JsonResponse({"tags": [_serialize_tag(tag) for tag in qs.order_by("category", "name")]})


@require_http_methods(["POST"])
@login_required
def create_planning_tag(request: HttpRequest) -> JsonResponse:
    payload = _parse_json(request)
    if payload is None:
        return _bad_request("Invalid JSON body")

    name = str(payload.get("name") or "").strip()
    category = str(payload.get("category") or ContentTag.CATEGORY_TAG).strip()
    color = str(payload.get("color") or "#1f6feb").strip()

    if not name:
        return _bad_request("name is required")
    if category not in {ContentTag.CATEGORY_PILLAR, ContentTag.CATEGORY_TAG}:
        return _bad_request("category must be pillar or tag")

    tag = ContentTag(owner=request.user, name=name, category=category, color=color)
    tag.save()
    return JsonResponse(_serialize_tag(tag), status=201)


@require_GET
@login_required
def calendar_items(request: HttpRequest) -> JsonResponse:
    start, end = _parse_month_window(request.GET.get("month"))
    if not start or not end:
        return _bad_request("month must be YYYY-MM")

    qs = (
        CalendarContentItem.objects.filter(owner=request.user, start_at__gte=start, start_at__lt=end)
        .select_related("connected_account")
        .prefetch_related("tags")
        .order_by("start_at", "id")
    )
    return JsonResponse({"items": [_serialize_item(item) for item in qs], "month_start": start.isoformat()})


@require_http_methods(["POST"])
@login_required
def create_calendar_item(request: HttpRequest) -> JsonResponse:
    payload = _parse_json(request)
    if payload is None:
        return _bad_request("Invalid JSON body")

    title = str(payload.get("title") or "").strip()
    start_at_raw = str(payload.get("start_at") or "").strip()
    if not title or not start_at_raw:
        return _bad_request("title and start_at are required")

    start_at = _parse_iso_datetime(start_at_raw)
    if not start_at:
        return _bad_request("start_at must be valid ISO datetime")

    platform = str(payload.get("platform") or CalendarContentItem.PLATFORM_BOTH)
    if platform not in {choice[0] for choice in CalendarContentItem.PLATFORM_CHOICES}:
        return _bad_request("platform must be facebook, instagram, or both")

    status = str(payload.get("status") or CalendarContentItem.STATUS_DRAFT)
    if status not in {choice[0] for choice in CalendarContentItem.STATUS_CHOICES}:
        return _bad_request("status is invalid")

    connected_account_id = payload.get("connected_account_id")
    connected_account = _resolve_owned_connected_account(request.user, connected_account_id)
    if connected_account_id and not connected_account:
        return _bad_request("connected_account_id is invalid for this user")

    item = CalendarContentItem.objects.create(
        owner=request.user,
        title=title,
        caption=str(payload.get("caption") or ""),
        start_at=start_at,
        platform=platform,
        status=status,
        notes=str(payload.get("notes") or ""),
        connected_account=connected_account,
    )

    tag_ids = payload.get("tag_ids") or []
    if isinstance(tag_ids, list):
        tags = list(ContentTag.objects.filter(owner=request.user, id__in=tag_ids))
        if tags:
            item.tags.set(tags)

    item.refresh_from_db()
    return JsonResponse(_serialize_item(item), status=201)


@require_http_methods(["PATCH", "POST"])
@login_required
def update_calendar_item(request: HttpRequest, item_id: int) -> JsonResponse:
    payload = _parse_json(request)
    if payload is None:
        return _bad_request("Invalid JSON body")

    item = CalendarContentItem.objects.filter(owner=request.user, id=item_id).first()
    if not item:
        return JsonResponse({"error": "Item not found"}, status=404)

    for key in ["title", "caption", "notes", "platform", "status"]:
        if key in payload:
            setattr(item, key, str(payload.get(key) or "").strip())

    if "start_at" in payload:
        start_at = _parse_iso_datetime(str(payload.get("start_at") or ""))
        if not start_at:
            return _bad_request("start_at must be valid ISO datetime")
        item.start_at = start_at

    if "connected_account_id" in payload:
        connected_account_id = payload.get("connected_account_id")
        connected_account = _resolve_owned_connected_account(request.user, connected_account_id)
        if connected_account_id and not connected_account:
            return _bad_request("connected_account_id is invalid for this user")
        item.connected_account = connected_account

    if item.platform not in {choice[0] for choice in CalendarContentItem.PLATFORM_CHOICES}:
        return _bad_request("platform must be facebook, instagram, or both")
    if item.status not in {choice[0] for choice in CalendarContentItem.STATUS_CHOICES}:
        return _bad_request("status is invalid")

    item.save()

    if "tag_ids" in payload and isinstance(payload.get("tag_ids"), list):
        tags = list(ContentTag.objects.filter(owner=request.user, id__in=payload.get("tag_ids") or []))
        item.tags.set(tags)

    item.refresh_from_db()
    return JsonResponse(_serialize_item(item))


@require_http_methods(["POST"])
@login_required
def generate_ai_calendar_plan(request: HttpRequest) -> JsonResponse:
    payload = _parse_json(request)
    if payload is None:
        return _bad_request("Invalid JSON body")

    niche = str(payload.get("niche") or "").strip()
    goal = str(payload.get("goal") or "").strip()
    platform = str(payload.get("platform") or "both").strip().lower()
    duration_days = payload.get("duration_days") or 7
    account_id = payload.get("account_id")

    try:
        duration_days = int(duration_days)
    except (TypeError, ValueError):
        return _bad_request("duration_days must be 7 or 30")
    if duration_days not in {7, 30}:
        return _bad_request("duration_days must be 7 or 30")
    if platform not in {"facebook", "instagram", "both"}:
        return _bad_request("platform must be facebook, instagram, or both")
    if not niche:
        return _bad_request("niche is required")

    account_context, historical_recommendations = _planner_account_context(request.user, account_id)
    try:
        plan = generate_content_calendar_plan(
            {
                "niche": niche,
                "goal": goal,
                "platform": platform,
                "duration_days": duration_days,
                "account_context": account_context,
                "historical_recommendations": historical_recommendations,
            }
        )
    except AIInsightsError as exc:
        return JsonResponse(
            {
                "error": "AI content planner unavailable",
                "details": str(exc),
            },
            status=502,
        )

    return JsonResponse(
        {
            "generated_at": timezone.now().isoformat(),
            "account_context": account_context,
            "historical_recommendations": historical_recommendations,
            "plan": plan,
        }
    )
