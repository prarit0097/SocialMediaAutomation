from urllib.parse import urlparse

from django.conf import settings
from django.core.cache import cache
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render

from core.exceptions import MetaAPIError
from core.services.meta_client import MetaClient
from integrations.models import ConnectedAccount


def _normalize_base_url(value: str) -> str:
    return (value or "").strip().rstrip("/")


def _public_url_status_payload(request):
    public_base_url = _normalize_base_url(settings.PUBLIC_BASE_URL)
    meta_redirect_uri = _normalize_base_url(settings.META_REDIRECT_URI)
    request_base_url = _normalize_base_url(request.build_absolute_uri("/"))

    warnings = []
    notes = []

    public_parts = urlparse(public_base_url) if public_base_url else None
    redirect_parts = urlparse(meta_redirect_uri) if meta_redirect_uri else None
    request_parts = urlparse(request_base_url) if request_base_url else None

    if not public_base_url:
        warnings.append("PUBLIC_BASE_URL is empty. Meta media fetches need a public HTTPS base URL.")
    elif public_parts and public_parts.scheme != "https":
        warnings.append("PUBLIC_BASE_URL must use HTTPS for Meta media fetches.")

    if meta_redirect_uri and redirect_parts and redirect_parts.scheme != "https":
        warnings.append("META_REDIRECT_URI should use HTTPS when testing through a public tunnel/domain.")

    if public_parts and redirect_parts and public_parts.netloc and redirect_parts.netloc:
        if public_parts.netloc != redirect_parts.netloc:
            warnings.append(
                "PUBLIC_BASE_URL and META_REDIRECT_URI point to different hosts. Update them together after tunnel/domain changes."
            )

    if public_parts and request_parts and public_parts.netloc and request_parts.netloc:
        if public_parts.netloc != request_parts.netloc:
            warnings.append(
                f"You are browsing via {request_parts.netloc}, but PUBLIC_BASE_URL points to {public_parts.netloc}."
            )

    if public_parts and public_parts.netloc.endswith("ngrok-free.app"):
        notes.append("Ngrok free domains can rotate or go offline. Update .env after each ngrok restart.")

    return {
        "ok": not warnings,
        "warnings": warnings,
        "notes": notes,
        "public_base_url": public_base_url,
        "meta_redirect_uri": meta_redirect_uri,
        "request_base_url": request_base_url,
    }


TOKEN_HEALTH_CACHE_KEY = "meta_token_health_summary_v1"
TOKEN_HEALTH_CACHE_TTL = 300


def _account_label(account: ConnectedAccount) -> str:
    return f"{account.page_name} ({account.platform})"


def _token_health_payload():
    cached = cache.get(TOKEN_HEALTH_CACHE_KEY)
    if cached:
        return {**cached, "cached": True}

    accounts = list(ConnectedAccount.objects.exclude(access_token="").order_by("id"))
    if not accounts:
        payload = {
            "ok": True,
            "level": "ok",
            "label": "Healthy",
            "summary": "No connected Meta accounts found.",
            "reason": "There are no stored page tokens to validate yet.",
            "next_steps": ["Connect Facebook + Instagram from the Accounts page to start token monitoring."],
            "checked_accounts": 0,
            "checked_tokens": 0,
            "invalid_accounts": [],
            "validation_error": None,
        }
        cache.set(TOKEN_HEALTH_CACHE_KEY, payload, TOKEN_HEALTH_CACHE_TTL)
        return {**payload, "cached": False}

    token_groups: dict[str, list[ConnectedAccount]] = {}
    for account in accounts:
        token_groups.setdefault(account.access_token, []).append(account)

    client = MetaClient()
    invalid_accounts: list[dict] = []
    validation_error = None

    for token, grouped_accounts in token_groups.items():
        try:
            data = client.debug_token(token).get("data", {})
        except MetaAPIError as exc:
            validation_error = str(exc)
            break

        if data.get("is_valid"):
            continue

        invalid_reason = str(data.get("error", {}).get("message") or "Meta marked this access token as invalid.")
        for account in grouped_accounts:
            invalid_accounts.append(
                {
                    "account_id": account.id,
                    "page_name": account.page_name,
                    "platform": account.platform,
                    "reason": invalid_reason,
                }
            )

    if not validation_error and not invalid_accounts:
        payload = {
            "ok": True,
            "level": "ok",
            "label": "Healthy",
            "summary": "Your Meta tokens are connected and currently valid.",
            "reason": (
                f"Validated {len(token_groups)} unique token(s) across {len(accounts)} connected account(s). "
                "Publishing, refresh, and insights calls can continue using the stored tokens."
            ),
            "next_steps": [
                "No action needed right now.",
                "If publishing fails later, reconnect the profile from Accounts and refresh the list.",
            ],
            "checked_accounts": len(accounts),
            "checked_tokens": len(token_groups),
            "invalid_accounts": [],
            "validation_error": None,
        }
    else:
        if validation_error and not invalid_accounts:
            summary = "Meta token health could not be fully validated right now."
            reason = validation_error
        else:
            summary = "One or more Meta tokens are invalid or expired."
            reason = invalid_accounts[0]["reason"] if invalid_accounts else validation_error
        payload = {
            "ok": False,
            "level": "bad",
            "label": "Needs reconnect",
            "summary": summary,
            "reason": reason,
            "next_steps": [
                "Open Accounts and click Connect Facebook + Instagram.",
                "Complete Meta reconnect so fresh page tokens are stored.",
                "Click Refresh List after reconnect.",
                "Retry failed posts or run insights refresh again.",
            ],
            "checked_accounts": len(accounts),
            "checked_tokens": len(token_groups),
            "invalid_accounts": invalid_accounts[:6],
            "validation_error": validation_error,
        }

    cache.set(TOKEN_HEALTH_CACHE_KEY, payload, TOKEN_HEALTH_CACHE_TTL)
    return {**payload, "cached": False}


@login_required
def home(request):
    return render(request, "dashboard/home.html")


@login_required
def accounts_page(request):
    return render(request, "dashboard/accounts.html")


@login_required
def scheduler_page(request):
    return render(request, "dashboard/scheduler.html")


@login_required
def insights_page(request):
    return render(request, "dashboard/insights.html")


@login_required
def public_url_status(request):
    return JsonResponse(_public_url_status_payload(request))


@login_required
def token_health_status(_request):
    return JsonResponse(_token_health_payload())
