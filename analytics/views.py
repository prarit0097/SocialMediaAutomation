import logging

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from core.exceptions import MetaAPIError
from integrations.models import ConnectedAccount

from .models import InsightSnapshot
from .services import build_insight_response, fetch_and_store_insights

logger = logging.getLogger("analytics")


@require_GET
@login_required
def account_insights(request: HttpRequest, account_id: int) -> JsonResponse:
    account = ConnectedAccount.objects.filter(id=account_id).first()
    if not account:
        return JsonResponse({"error": "Connected account not found"}, status=404)

    force_refresh = request.GET.get("refresh") == "1"
    throttle_key = f"insight-refresh-{request.user.id}-{account_id}"

    if force_refresh:
        if not cache.add(throttle_key, 1, timeout=30):
            return JsonResponse({"error": "Too many refresh requests"}, status=429)

        try:
            data = fetch_and_store_insights(account)
        except MetaAPIError as exc:
            logger.warning("insights fetch failed account_id=%s error=%s", account_id, exc)
            return JsonResponse(
                {
                    "error": "Failed to fetch insights from Meta",
                    "details": str(exc),
                },
                status=502,
            )
        logger.info("insights refreshed account_id=%s user_id=%s", account_id, request.user.id)
        return JsonResponse(data)

    latest = InsightSnapshot.objects.filter(account=account).order_by("-fetched_at").first()
    if latest:
        data = build_insight_response(
            account=account,
            platform=latest.platform,
            insights=latest.payload.get("insights", []),
            snapshot_id=latest.id,
            fetched_at=latest.fetched_at,
            cached=True,
            published_posts=latest.payload.get("published_posts", []),
        )
        return JsonResponse(data)

    try:
        data = fetch_and_store_insights(account)
    except MetaAPIError as exc:
        logger.warning("insights fetch failed account_id=%s error=%s", account_id, exc)
        return JsonResponse(
            {
                "error": "Failed to fetch insights from Meta",
                "details": str(exc),
            },
            status=502,
        )
    return JsonResponse(data)
