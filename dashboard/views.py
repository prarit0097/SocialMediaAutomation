from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render


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
