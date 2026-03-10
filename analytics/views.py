import logging

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from integrations.models import ConnectedAccount

from .models import InsightSnapshot
from .services import fetch_and_store_insights

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

        data = fetch_and_store_insights(account)
        logger.info("insights refreshed account_id=%s user_id=%s", account_id, request.user.id)
        return JsonResponse(data)

    latest = InsightSnapshot.objects.filter(account=account).order_by("-fetched_at").first()
    if latest:
        return JsonResponse(
            {
                "platform": latest.platform,
                "insights": latest.payload.get("insights", []),
                "snapshot_id": latest.id,
                "fetched_at": latest.fetched_at.isoformat(),
                "cached": True,
            }
        )

    data = fetch_and_store_insights(account)
    data["cached"] = False
    return JsonResponse(data)
