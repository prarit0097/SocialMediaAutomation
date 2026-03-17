from urllib.parse import urlencode
import secrets

import requests
from django.conf import settings
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.shortcuts import redirect, render

from .models import UserProfile


class AdminLoginView(LoginView):
    template_name = "accounts/login.html"
    redirect_authenticated_user = True


def landing_page(request):
    if request.user.is_authenticated:
        return redirect("dashboard:home")
    return render(request, "accounts/landing.html")


def _google_signup_config() -> dict:
    return {
        "client_id": str(getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "") or "").strip(),
        "client_secret": str(getattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", "") or "").strip(),
        "redirect_uri": str(getattr(settings, "GOOGLE_OAUTH_REDIRECT_URI", "") or "").strip(),
    }


def _google_signup_ready() -> bool:
    cfg = _google_signup_config()
    return bool(cfg["client_id"] and cfg["client_secret"] and cfg["redirect_uri"])


def _build_unique_username_from_email(email: str) -> str:
    user_model = get_user_model()
    base = (email or "").split("@")[0].strip() or "google_user"
    candidate = base[:150]
    suffix = 1
    while user_model.objects.filter(username=candidate).exists():
        token = f"{base[:130]}_{suffix}"
        candidate = token[:150]
        suffix += 1
    return candidate


def signup_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard:home")

    return render(
        request,
        "accounts/signup.html",
        {
            "google_signup_ready": _google_signup_ready(),
            "error": (request.GET.get("error") or "").strip(),
        },
    )


def google_signup_start(request):
    if request.user.is_authenticated:
        return redirect("dashboard:home")

    cfg = _google_signup_config()
    if not (cfg["client_id"] and cfg["client_secret"] and cfg["redirect_uri"]):
        return redirect("/signup/?error=Google+signup+is+not+configured")

    state = secrets.token_urlsafe(24)
    cache.set(f"google_oauth_state:{state}", {"issued": True}, timeout=600)
    query = urlencode(
        {
            "client_id": cfg["client_id"],
            "redirect_uri": cfg["redirect_uri"],
            "response_type": "code",
            "scope": "openid email profile",
            "access_type": "online",
            "prompt": "select_account",
            "state": state,
        }
    )
    return redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{query}")


def google_signup_callback(request):
    if request.user.is_authenticated:
        return redirect("dashboard:home")

    code = (request.GET.get("code") or "").strip()
    state = (request.GET.get("state") or "").strip()
    if not code or not state:
        return redirect("/signup/?error=Google+signup+failed%3A+missing+code+or+state")

    cache_key = f"google_oauth_state:{state}"
    cached_state = cache.get(cache_key)
    cache.delete(cache_key)
    if not cached_state:
        return redirect("/signup/?error=Google+signup+failed%3A+invalid+or+expired+state")

    cfg = _google_signup_config()
    if not (cfg["client_id"] and cfg["client_secret"] and cfg["redirect_uri"]):
        return redirect("/signup/?error=Google+signup+is+not+configured")

    try:
        token_response = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "redirect_uri": cfg["redirect_uri"],
                "grant_type": "authorization_code",
            },
            timeout=20,
        )
    except requests.RequestException:
        return redirect("/signup/?error=Google+signup+failed%3A+token+exchange+error")

    if token_response.status_code >= 400:
        return redirect("/signup/?error=Google+signup+failed%3A+token+exchange+rejected")

    token_payload = token_response.json() if token_response.content else {}
    access_token = (token_payload.get("access_token") or "").strip()
    if not access_token:
        return redirect("/signup/?error=Google+signup+failed%3A+missing+access+token")

    try:
        userinfo_response = requests.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
    except requests.RequestException:
        return redirect("/signup/?error=Google+signup+failed%3A+unable+to+load+profile")

    if userinfo_response.status_code >= 400:
        return redirect("/signup/?error=Google+signup+failed%3A+unable+to+read+profile")

    profile = userinfo_response.json() if userinfo_response.content else {}
    email = str(profile.get("email") or "").strip().lower()
    email_verified = bool(profile.get("email_verified"))
    if not email or not email_verified:
        return redirect("/signup/?error=Google+signup+requires+a+verified+email")

    user_model = get_user_model()
    user = user_model.objects.filter(email__iexact=email).first() or user_model.objects.filter(username=email).first()
    first_name = str(profile.get("given_name") or "").strip()
    last_name = str(profile.get("family_name") or "").strip()
    profile_picture_url = str(profile.get("picture") or "").strip()
    if not user:
        username = email
        if user_model.objects.filter(username=username).exists():
            username = _build_unique_username_from_email(email)
        user = user_model(username=username, email=email, first_name=first_name, last_name=last_name)
        user.set_unusable_password()
        user.save()
    else:
        changed = False
        if first_name and user.first_name != first_name:
            user.first_name = first_name
            changed = True
        if last_name and user.last_name != last_name:
            user.last_name = last_name
            changed = True
        if changed:
            user.save(update_fields=["first_name", "last_name"])

    user_profile, _ = UserProfile.objects.get_or_create(user=user)
    profile_changed = False
    if first_name and user_profile.first_name != first_name:
        user_profile.first_name = first_name
        profile_changed = True
    if last_name and user_profile.last_name != last_name:
        user_profile.last_name = last_name
        profile_changed = True
    if profile_picture_url and user_profile.profile_picture_url != profile_picture_url:
        user_profile.profile_picture_url = profile_picture_url
        profile_changed = True
    if profile_changed:
        user_profile.save(update_fields=["first_name", "last_name", "profile_picture_url", "updated_at"])

    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect("dashboard:home")


def logout_view(request):
    logout(request)
    return redirect("login")
