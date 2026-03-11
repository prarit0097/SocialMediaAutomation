import logging
import secrets

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.conf import settings
from django.db.models import Max
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
    fb_total = ConnectedAccount.objects.filter(platform="facebook").count()
    ig_total = ConnectedAccount.objects.filter(platform="instagram").count()
    latest_updated = ConnectedAccount.objects.aggregate(latest=Max("updated_at")).get("latest")
    data = {
        "meta_pages_synced": data.get("meta_pages_synced") or fb_total,
        "facebook_connected_total": data.get("facebook_connected_total") or fb_total,
        "instagram_connected_total": data.get("instagram_connected_total") or ig_total,
        "token_target_ids_count": data.get("token_target_ids_count") or None,
        "warning": data.get("warning"),
        "synced_at": data.get("synced_at") or (latest_updated.isoformat() if latest_updated else None),
    }
    return JsonResponse(data)


@require_GET
@login_required
def meta_pages_catalog(request: HttpRequest) -> JsonResponse:
    force_refresh = request.GET.get("refresh") == "1"
    cache_key = f"meta_pages_catalog:{request.user.id}"
    cached = None if force_refresh else cache.get(cache_key)
    if cached:
        return JsonResponse(cached)

    accounts = list(ConnectedAccount.objects.filter(platform="facebook").order_by("-updated_at"))
    if not accounts:
        payload = {"total_pages": 0, "connected_pages": 0, "rows": []}
        cache.set(cache_key, payload, timeout=300)
        return JsonResponse(payload)

    seed_account = accounts[0]
    connected_ids = {str(a.page_id) for a in accounts}
    client = MetaClient()

    rows: list[dict] = []
    seen_ids: set[str] = set()

    for account in accounts:
        page_id = str(account.page_id)
        if page_id in seen_ids:
            continue
        seen_ids.add(page_id)
        rows.append(
            {
                "page_id": page_id,
                "page_name": account.page_name,
                "status": "connected",
                "connectability": "connected",
                "reason": "Page access token is synced in app.",
            }
        )

    try:
        debug_data = client.debug_token(seed_account.access_token).get("data", {})
        target_ids: list[str] = []
        for scope_item in (debug_data.get("granular_scopes") or []):
            for target_id in (scope_item.get("target_ids") or []):
                sid = str(target_id)
                if sid not in target_ids:
                    target_ids.append(sid)

        for target_id in target_ids:
            if target_id in seen_ids:
                continue
            page_name = None
            reason = "Page is visible in token target_ids but not returned by /me/accounts."
            connectability = "not_connectable"
            try:
                page_data = client._get(
                    f"/{target_id}",
                    {
                        "access_token": seed_account.access_token,
                        "fields": "id,name,access_token",
                    },
                )
                page_name = page_data.get("name")
                if page_data.get("access_token"):
                    reason = "Page token is available from page node; reconnect to sync it in app."
                    connectability = "connectable"
                else:
                    reason = (
                        "Meta did not return page access token for this page. "
                        "Check page admin/task access and Business Integration page selection."
                    )
                    connectability = "not_connectable"
            except MetaAPIError:
                page_name = None
                reason = (
                    "Unable to read page details with current token. "
                    "Check that this user has admin/full control on this page."
                )
                connectability = "not_connectable"

            rows.append(
                {
                    "page_id": target_id,
                    "page_name": page_name or "(name unavailable)",
                    "status": "catalog-only",
                    "connectability": connectability,
                    "reason": reason,
                }
            )
            seen_ids.add(target_id)
    except MetaAPIError:
        pass

    rows.sort(key=lambda r: (0 if r["status"] == "connected" else 1, (r.get("page_name") or "").lower()))
    payload = {
        "total_pages": len(rows),
        "connected_pages": len(connected_ids),
        "rows": rows,
    }
    cache.set(cache_key, payload, timeout=300)
    return JsonResponse(payload)
