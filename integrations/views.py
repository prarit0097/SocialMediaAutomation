import logging
import secrets

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.conf import settings
from django.utils import timezone
from django.views.decorators.http import require_GET

from core.exceptions import MetaAPIError
from core.services.meta_client import MetaClient

from .models import ConnectedAccount
from .services import upsert_connected_accounts

logger = logging.getLogger("integrations")


@require_GET
@login_required
def meta_start(request: HttpRequest) -> JsonResponse:
    state = secrets.token_urlsafe(24)
    cache.set(f"meta_oauth_state:{state}", {"user_id": request.user.id}, timeout=600)
    redirect_uri = settings.META_REDIRECT_URI

    client = MetaClient()
    return JsonResponse({"auth_url": client.oauth_url(state, redirect_uri=redirect_uri)})


@require_GET
def meta_callback(request: HttpRequest) -> HttpResponse:
    oauth_error = request.GET.get("error")
    if oauth_error:
        description = request.GET.get("error_description") or request.GET.get("error_reason") or oauth_error
        return JsonResponse({"error": "Meta OAuth failed", "details": description}, status=400)

    code = request.GET.get("code")
    state = request.GET.get("state")
    state_data = cache.get(f"meta_oauth_state:{state}") if state else None

    if not code or not state or not state_data:
        return JsonResponse({"error": "Invalid OAuth callback parameters"}, status=400)

    redirect_uri = settings.META_REDIRECT_URI
    cache.delete(f"meta_oauth_state:{state}")
    user_id = state_data.get("user_id") if isinstance(state_data, dict) else None

    client = MetaClient()
    token_data = client.exchange_code_for_token(code, redirect_uri=redirect_uri)
    pages = client.get_managed_pages(token_data["access_token"])
    upsert_connected_accounts(pages)

    target_ids_count = None
    sync_warning = None
    if pages:
        try:
            debug_data = client.debug_token(pages[0]["access_token"]).get("data", {})
            target_ids: set[str] = set()
            for scope_item in (debug_data.get("granular_scopes") or []):
                for target_id in (scope_item.get("target_ids") or []):
                    target_ids.add(str(target_id))
            target_ids_count = len(target_ids)
            if target_ids_count and len(pages) < target_ids_count:
                sync_warning = (
                    "Meta returned fewer pages than token target_ids. Reconnect and allow access to all pages."
                )
        except MetaAPIError:
            target_ids_count = None

    if user_id:
        cache.set(
            f"meta_last_sync:{user_id}",
            {
                "meta_pages_synced": len(pages),
                "facebook_connected_total": ConnectedAccount.objects.filter(platform="facebook").count(),
                "instagram_connected_total": ConnectedAccount.objects.filter(platform="instagram").count(),
                "token_target_ids_count": target_ids_count,
                "warning": sync_warning,
                "synced_at": timezone.now().isoformat(),
            },
            timeout=60 * 60 * 12,
        )

    logger.info("Meta accounts connected. total_pages=%s", len(pages))
    return redirect("dashboard:accounts")


@require_GET
@login_required
def list_accounts(_request: HttpRequest) -> JsonResponse:
    rows = list(
        ConnectedAccount.objects.values(
            "id",
            "platform",
            "page_id",
            "page_name",
            "ig_user_id",
            "created_at",
            "updated_at",
        )
    )
    return JsonResponse(rows, safe=False)


@require_GET
@login_required
def accounts_sync_status(request: HttpRequest) -> JsonResponse:
    data = cache.get(f"meta_last_sync:{request.user.id}") or {}
    if not data:
        data = {
            "meta_pages_synced": None,
            "facebook_connected_total": ConnectedAccount.objects.filter(platform="facebook").count(),
            "instagram_connected_total": ConnectedAccount.objects.filter(platform="instagram").count(),
            "token_target_ids_count": None,
            "warning": None,
            "synced_at": None,
        }
    return JsonResponse(data)
