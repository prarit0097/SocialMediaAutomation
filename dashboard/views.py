import json
import os
import re
import hmac
import hashlib
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.cache import cache
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.exceptions import MetaAPIError
from core.services.meta_client import MetaClient
from accounts.models import UserProfile
from integrations.models import ConnectedAccount
from integrations.sync_state import SYNC_FRESHNESS_WINDOW, get_recent_sync_time


def _normalize_base_url(value: str) -> str:
    return (value or "").strip().rstrip("/")


ENV_META_KEYS = ("META_APP_ID", "META_APP_SECRET", "META_REDIRECT_URI")
ENV_SIMPLE_VALUE_RE = re.compile(r"^[A-Za-z0-9_./:@+-]+$")
SUBSCRIPTION_MONTHLY_AMOUNT_PAISE = 600000
SUBSCRIPTION_YEARLY_AMOUNT_PAISE = 7000000
SUBSCRIPTION_ORDER_CACHE_TTL = 3600
SUBSCRIPTION_PLAN_CONFIG = {
    "monthly": {
        "plan_key": "monthly",
        "title": "Monthly Growth Plan",
        "profile_label": UserProfile.SUBSCRIPTION_PLAN_MONTHLY,
        "price_label": "INR 6,000 / month",
        "amount_paise": SUBSCRIPTION_MONTHLY_AMOUNT_PAISE,
        "billing_cycle": "monthly",
        "tagline": "Perfect for fast monthly execution and iteration.",
    },
    "yearly": {
        "plan_key": "yearly",
        "title": "Yearly Scale Plan",
        "profile_label": UserProfile.SUBSCRIPTION_PLAN_YEARLY,
        "price_label": "INR 70,000 / year",
        "amount_paise": SUBSCRIPTION_YEARLY_AMOUNT_PAISE,
        "billing_cycle": "yearly",
        "tagline": "Best value for long-term growth operations.",
    },
}


def _env_file_path() -> Path:
    return Path(getattr(settings, "BASE_DIR", Path.cwd())) / ".env"


def _mask_secret(secret: str) -> str:
    token = str(secret or "").strip()
    if not token:
        return ""
    if len(token) <= 4:
        return "*" * len(token)
    return f"{'*' * (len(token) - 4)}{token[-4:]}"


def _meta_config_payload() -> dict:
    app_id = str(getattr(settings, "META_APP_ID", "") or "").strip()
    app_secret = str(getattr(settings, "META_APP_SECRET", "") or "").strip()
    redirect_uri = str(getattr(settings, "META_REDIRECT_URI", "") or "").strip()

    return {
        "meta_app_id": app_id,
        "meta_redirect_uri": redirect_uri,
        "meta_app_secret_masked": _mask_secret(app_secret),
        "meta_app_secret_configured": bool(app_secret),
    }


def _env_serialize_value(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if ENV_SIMPLE_VALUE_RE.fullmatch(normalized):
        return normalized
    escaped = normalized.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _upsert_env_values(file_path: Path, updates: dict[str, str]) -> None:
    lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True) if file_path.exists() else []
    updated_lines: list[str] = []
    seen_keys: set[str] = set()

    for line in lines:
        replaced = False
        for key, value in updates.items():
            if re.match(rf"^\s*{re.escape(key)}\s*=", line):
                updated_lines.append(f"{key}={_env_serialize_value(value)}\n")
                seen_keys.add(key)
                replaced = True
                break
        if not replaced:
            updated_lines.append(line if line.endswith("\n") else f"{line}\n")

    for key, value in updates.items():
        if key in seen_keys:
            continue
        if updated_lines and updated_lines[-1].strip():
            updated_lines.append("\n")
        updated_lines.append(f"{key}={_env_serialize_value(value)}\n")

    file_path.write_text("".join(updated_lines), encoding="utf-8")


def _apply_meta_runtime_settings(updates: dict[str, str]) -> None:
    for key in ENV_META_KEYS:
        value = str(updates.get(key) or "").strip()
        setattr(settings, key, value)
        os.environ[key] = value


def _validate_meta_config(app_id: str, app_secret: str, redirect_uri: str) -> list[str]:
    errors: list[str] = []
    if not app_id:
        errors.append("META_APP_ID is required.")
    if not app_secret:
        errors.append("META_APP_SECRET is required.")
    if not redirect_uri:
        errors.append("META_REDIRECT_URI is required.")

    for key, value in {
        "META_APP_ID": app_id,
        "META_APP_SECRET": app_secret,
        "META_REDIRECT_URI": redirect_uri,
    }.items():
        if "\n" in value or "\r" in value:
            errors.append(f"{key} cannot contain newline characters.")

    if redirect_uri:
        parts = urlparse(redirect_uri)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            errors.append("META_REDIRECT_URI must be a valid absolute http/https URL.")
    return errors


def _profile_payload(user) -> dict:
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.refresh_subscription_state()
    return {
        "email": user.email,
        "first_name": profile.resolved_first_name,
        "last_name": profile.resolved_last_name,
        "profile_picture_url": profile.profile_picture_url,
        "subscription_plan": profile.subscription_plan,
        "subscription_status": profile.subscription_status,
        "subscription_expires_on": profile.subscription_expires_on.isoformat() if profile.subscription_expires_on else None,
    }


def _subscription_page_payload(user) -> dict:
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.refresh_subscription_state()
    return {
        "razorpay_key_id": str(getattr(settings, "RAZORPAY_KEY_ID", "") or "").strip(),
        "currency": str(getattr(settings, "RAZORPAY_CURRENCY", "INR") or "INR").strip().upper(),
        "plans": SUBSCRIPTION_PLAN_CONFIG,
        "current_plan": profile.subscription_plan,
        "current_status": profile.subscription_status,
        "current_expiry": profile.subscription_expires_on.isoformat() if profile.subscription_expires_on else None,
        "is_locked": not profile.is_subscription_active,
        "feature_groups": [
            {
                "title": "Operations Core",
                "items": [
                    "Connected account control (FB + IG)",
                    "Reliable local-time post scheduling",
                    "Queue monitoring with retry support",
                ],
            },
            {
                "title": "Performance & Insights",
                "items": [
                    "Cross-platform insights snapshots",
                    "Published post performance table",
                    "Daily heavy auto-refresh automation",
                ],
            },
            {
                "title": "AI + Planning",
                "items": [
                    "AI profile insights and action plans",
                    "Monthly planning board with drag/drop",
                    "Growth-focused workflow for teams",
                ],
            },
        ],
}


def _subscription_order_cache_key(order_id: str) -> str:
    return f"subscription_order:{order_id}"


def _is_razorpay_configured() -> bool:
    key_id = str(getattr(settings, "RAZORPAY_KEY_ID", "") or "").strip()
    key_secret = str(getattr(settings, "RAZORPAY_KEY_SECRET", "") or "").strip()
    return bool(key_id and key_secret)


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


TOKEN_HEALTH_CACHE_KEY_PREFIX = "meta_token_health_summary_v1"
TOKEN_HEALTH_CACHE_TTL = 300


def _account_label(account: ConnectedAccount) -> str:
    return f"{account.page_name} ({account.platform})"


def _sync_scoped_accounts(user) -> tuple[list[ConnectedAccount], str]:
    recent_sync_time = get_recent_sync_time(getattr(user, "id", None))
    if recent_sync_time:
        window_start = recent_sync_time - SYNC_FRESHNESS_WINDOW
        scoped = list(ConnectedAccount.objects.filter(is_active=True, updated_at__gte=window_start).order_by("id"))
        if scoped:
            return scoped, "recent_sync"
    return list(ConnectedAccount.objects.filter(is_active=True).order_by("id")), "all_connected"


def _stale_connected_accounts(accounts: list[ConnectedAccount], user) -> list[ConnectedAccount]:
    recent_sync_time = get_recent_sync_time(getattr(user, "id", None))
    if not recent_sync_time:
        return []
    window_start = recent_sync_time - SYNC_FRESHNESS_WINDOW
    return [account for account in accounts if account.updated_at < window_start]


def _token_health_payload(user):
    cache_key = f"{TOKEN_HEALTH_CACHE_KEY_PREFIX}:{getattr(user, 'id', 'anon')}"
    cached = cache.get(cache_key)
    if cached:
        return {**cached, "cached": True}

    accounts, scope = _sync_scoped_accounts(user)
    if not accounts:
        payload = {
            "ok": False,
            "level": "bad",
            "label": "Connect required",
            "summary": "No connected Meta accounts found.",
            "reason": "Connect Facebook and Instagram first. Health stays red until at least one account is connected.",
            "next_steps": ["Open Accounts and click Connect Facebook + Instagram to start token monitoring."],
            "checked_accounts": 0,
            "checked_tokens": 0,
            "scope": scope,
            "invalid_accounts": [],
            "validation_error": None,
        }
        cache.set(cache_key, payload, TOKEN_HEALTH_CACHE_TTL)
        return {**payload, "cached": False}

    token_groups: dict[str, list[ConnectedAccount]] = {}
    for account in accounts:
        token_groups.setdefault(account.access_token, []).append(account)

    client = MetaClient()
    invalid_accounts: list[dict] = []
    validation_error = None
    stale_accounts = _stale_connected_accounts(
        list(ConnectedAccount.objects.filter(is_active=True).order_by("id")),
        user,
    )

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

    validation_error_text = str(validation_error or "")
    is_rate_limited = "code=4" in validation_error_text.lower()

    if invalid_accounts:
        summary = "One or more Meta tokens are invalid or expired."
        reason = invalid_accounts[0]["reason"]
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
            "scope": scope,
            "invalid_accounts": invalid_accounts[:6],
            "stale_accounts": [],
            "validation_error": validation_error,
        }
    elif stale_accounts:
        payload = {
            "ok": False,
            "level": "bad",
            "label": "Needs reconnect",
            "summary": "Some connected profiles were not refreshed in the latest Meta reconnect.",
            "reason": (
                f"{len(stale_accounts)} stored account row(s) are older than the latest reconnect window. "
                "Scheduling from those rows can fail until they are refreshed."
            ),
            "next_steps": [
                "Open Accounts and click Connect Facebook + Instagram.",
                "Reconnect the missing profiles so fresh page tokens are stored.",
                "Click Refresh List and use the current synced rows for scheduling.",
            ],
            "checked_accounts": len(accounts),
            "checked_tokens": len(token_groups),
            "scope": scope,
            "invalid_accounts": [],
            "stale_accounts": [
                {"account_id": account.id, "page_name": account.page_name, "platform": account.platform}
                for account in stale_accounts[:6]
            ],
            "validation_error": validation_error,
        }
    elif is_rate_limited:
        payload = {
            "ok": True,
            "level": "ok",
            "label": "Healthy",
            "summary": "Recent reconnect completed. Meta health check hit app rate limit before any invalid token was confirmed.",
            "reason": validation_error_text,
            "next_steps": [
                "Wait 1-2 minutes and refresh once if you want a fresh validation pass.",
                "If publishing still fails, reconnect the affected profile from Accounts.",
            ],
            "checked_accounts": len(accounts),
            "checked_tokens": len(token_groups),
            "scope": scope,
            "invalid_accounts": [],
            "stale_accounts": [],
            "validation_error": validation_error,
        }
    elif not validation_error:
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
            "scope": scope,
            "invalid_accounts": [],
            "stale_accounts": [],
            "validation_error": None,
        }
    else:
        payload = {
            "ok": False,
            "level": "bad",
            "label": "Needs reconnect",
            "summary": "Meta token health could not be fully validated right now.",
            "reason": validation_error,
            "next_steps": [
                "Open Accounts and click Connect Facebook + Instagram.",
                "Complete Meta reconnect so fresh page tokens are stored.",
                "Click Refresh List after reconnect.",
                "Retry failed posts or run insights refresh again.",
            ],
            "checked_accounts": len(accounts),
            "checked_tokens": len(token_groups),
            "scope": scope,
            "invalid_accounts": [],
            "stale_accounts": [],
            "validation_error": validation_error,
        }

    cache.set(cache_key, payload, TOKEN_HEALTH_CACHE_TTL)
    return {**payload, "cached": False}


@login_required
def home(request):
    return render(
        request,
        "dashboard/home.html",
        {
            "meta_config": _meta_config_payload(),
        },
    )


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
def ai_insights_page(request):
    return render(request, "dashboard/ai_insights.html")


@login_required
def planning_page(request):
    return render(request, "dashboard/planning.html")


@login_required
def profile_page(request):
    return render(request, "dashboard/profile.html")


@login_required
def subscription_page(request):
    return render(request, "dashboard/subscription.html", {"subscription": _subscription_page_payload(request.user)})


@login_required
def subscription_expired_page(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    profile.refresh_subscription_state()
    return render(
        request,
        "dashboard/subscription_expired.html",
        {
            "subscription_plan": profile.subscription_plan,
            "subscription_status": profile.subscription_status,
            "subscription_expires_on": profile.subscription_expires_on.isoformat() if profile.subscription_expires_on else None,
        },
    )


@login_required
def public_url_status(request):
    return JsonResponse(_public_url_status_payload(request))


@login_required
def token_health_status(request):
    return JsonResponse(_token_health_payload(request.user))


@login_required
@require_http_methods(["GET", "POST"])
def meta_app_config(request):
    if request.method == "GET":
        return JsonResponse(_meta_config_payload())

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    meta_app_id = str(payload.get("meta_app_id") or "").strip()
    meta_redirect_uri = str(payload.get("meta_redirect_uri") or "").strip()
    submitted_secret = str(payload.get("meta_app_secret") or "").strip()
    current_secret = str(getattr(settings, "META_APP_SECRET", "") or "").strip()
    final_secret = submitted_secret or current_secret

    errors = _validate_meta_config(meta_app_id, final_secret, meta_redirect_uri)
    if errors:
        return JsonResponse({"error": "Validation failed.", "details": " ".join(errors)}, status=400)

    updates = {
        "META_APP_ID": meta_app_id,
        "META_APP_SECRET": final_secret,
        "META_REDIRECT_URI": meta_redirect_uri,
    }

    try:
        _upsert_env_values(_env_file_path(), updates)
    except OSError as exc:
        return JsonResponse({"error": "Unable to update .env", "details": str(exc)}, status=500)

    _apply_meta_runtime_settings(updates)
    cache.clear()

    response = _meta_config_payload()
    response["ok"] = True
    response["message"] = "Meta app configuration saved successfully."
    if "/auth/meta/callback" not in meta_redirect_uri:
        response["warning"] = "META_REDIRECT_URI usually ends with /auth/meta/callback."
    return JsonResponse(response)


@login_required
@require_http_methods(["GET", "POST"])
def profile_data(request):
    if request.method == "GET":
        return JsonResponse(_profile_payload(request.user))

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    first_name = str(payload.get("first_name") or "").strip()
    last_name = str(payload.get("last_name") or "").strip()

    errors = []
    if len(first_name) > 150:
        errors.append("First name should be 150 characters or less.")
    if len(last_name) > 150:
        errors.append("Last name should be 150 characters or less.")

    if errors:
        return JsonResponse({"error": "Validation failed.", "details": " ".join(errors)}, status=400)

    user = request.user
    user.first_name = first_name
    user.last_name = last_name
    user.save(update_fields=["first_name", "last_name"])

    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.first_name = first_name
    profile.last_name = last_name
    profile.save(
        update_fields=[
            "first_name",
            "last_name",
            "updated_at",
        ]
    )

    response = _profile_payload(user)
    response["ok"] = True
    response["message"] = "Profile updated successfully."
    return JsonResponse(response)


@login_required
@require_http_methods(["POST"])
def subscription_create_order(request):
    if not _is_razorpay_configured():
        return JsonResponse(
            {"error": "Razorpay is not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in .env."},
            status=400,
        )

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    plan_key = str(payload.get("plan") or "").strip().lower()
    if plan_key not in SUBSCRIPTION_PLAN_CONFIG:
        return JsonResponse({"error": "Invalid plan selected."}, status=400)

    plan = SUBSCRIPTION_PLAN_CONFIG[plan_key]
    currency = str(getattr(settings, "RAZORPAY_CURRENCY", "INR") or "INR").strip().upper()
    key_id = str(getattr(settings, "RAZORPAY_KEY_ID", "") or "").strip()
    key_secret = str(getattr(settings, "RAZORPAY_KEY_SECRET", "") or "").strip()

    order_payload = {
        "amount": int(plan["amount_paise"]),
        "currency": currency,
        "receipt": f"subs_{plan_key}_{uuid.uuid4().hex[:16]}",
        "notes": {
            "plan_key": plan_key,
            "billing_cycle": plan["billing_cycle"],
            "user_id": str(request.user.id),
            "username": str(request.user.username),
        },
    }

    try:
        response = requests.post(
            "https://api.razorpay.com/v1/orders",
            auth=(key_id, key_secret),
            json=order_payload,
            timeout=20,
        )
    except requests.RequestException as exc:
        return JsonResponse({"error": f"Unable to reach Razorpay: {exc}"}, status=502)

    if response.status_code >= 400:
        detail = ""
        try:
            body = response.json()
            detail = str(body.get("error", {}).get("description") or body.get("error", {}).get("reason") or "").strip()
        except ValueError:
            detail = response.text[:300]
        return JsonResponse({"error": detail or "Razorpay order creation failed."}, status=502)

    order = response.json()
    order_id = str(order.get("id") or "").strip()
    if order_id:
        cache.set(
            _subscription_order_cache_key(order_id),
            {
                "user_id": request.user.id,
                "plan_key": plan_key,
                "billing_cycle": plan["billing_cycle"],
                "price_label": plan["price_label"],
                "title": plan["title"],
            },
            SUBSCRIPTION_ORDER_CACHE_TTL,
        )
    return JsonResponse(
        {
            "ok": True,
            "order_id": order_id,
            "amount": order.get("amount"),
            "currency": order.get("currency"),
            "plan": plan_key,
            "razorpay_key_id": key_id,
            "plan_title": plan["title"],
            "price_label": plan["price_label"],
            "prefill": {
                "name": f"{request.user.first_name} {request.user.last_name}".strip() or request.user.username,
                "email": request.user.email,
            },
        }
    )


@login_required
@require_http_methods(["POST"])
def subscription_verify_payment(request):
    if not _is_razorpay_configured():
        return JsonResponse(
            {"error": "Razorpay is not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in .env."},
            status=400,
        )

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    order_id = str(payload.get("razorpay_order_id") or "").strip()
    payment_id = str(payload.get("razorpay_payment_id") or "").strip()
    signature = str(payload.get("razorpay_signature") or "").strip()

    if not order_id or not payment_id or not signature:
        return JsonResponse({"error": "Missing Razorpay verification fields."}, status=400)

    key_secret = str(getattr(settings, "RAZORPAY_KEY_SECRET", "") or "").strip()
    signed_payload = f"{order_id}|{payment_id}".encode("utf-8")
    expected_signature = hmac.new(key_secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected_signature, signature):
        return JsonResponse({"error": "Payment signature verification failed."}, status=400)

    cached_order = cache.get(_subscription_order_cache_key(order_id)) or {}
    if int(cached_order.get("user_id") or 0) != int(request.user.id):
        return JsonResponse({"error": "Payment verification context expired. Please start checkout again."}, status=400)

    billing_cycle = str(cached_order.get("billing_cycle") or "").strip().lower()
    if billing_cycle not in SUBSCRIPTION_PLAN_CONFIG:
        return JsonResponse({"error": "Payment verification context is incomplete. Please retry checkout."}, status=400)

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    try:
        profile.activate_paid_plan(billing_cycle)
    except ValueError:
        return JsonResponse({"error": "Unsupported billing cycle returned by payment verification."}, status=400)

    cache.delete(_subscription_order_cache_key(order_id))

    return JsonResponse(
        {
            "ok": True,
            "message": (
                f"Payment verified successfully. Your {profile.subscription_plan} plan is active until "
                f"{profile.subscription_expires_on.isoformat()}."
            ),
            "payment_id": payment_id,
            "order_id": order_id,
            "subscription": _profile_payload(request.user),
        }
    )
