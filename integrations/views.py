import logging
import secrets

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET

from core.services.meta_client import MetaClient

from .models import ConnectedAccount
from .services import upsert_connected_accounts

logger = logging.getLogger("integrations")


@require_GET
@login_required
def meta_start(request: HttpRequest) -> JsonResponse:
    state = secrets.token_urlsafe(24)
    request.session["meta_oauth_state"] = state
    client = MetaClient()
    return JsonResponse({"auth_url": client.oauth_url(state)})


@require_GET
def meta_callback(request: HttpRequest) -> HttpResponse:
    code = request.GET.get("code")
    state = request.GET.get("state")
    expected_state = request.session.get("meta_oauth_state")

    if not code or not state or state != expected_state:
        return JsonResponse({"error": "Invalid OAuth callback parameters"}, status=400)

    request.session.pop("meta_oauth_state", None)

    client = MetaClient()
    token_data = client.exchange_code_for_token(code)
    pages = client.get_managed_pages(token_data["access_token"])
    upsert_connected_accounts(pages)

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
