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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["google_signup_ready"] = _google_signup_ready()
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        _set_persistent_session(self.request)
        return response


def landing_page(request):
    if request.user.is_authenticated:
        return redirect("dashboard:home")
    return render(request, "accounts/landing.html")


def _legal_page_context(page_key: str) -> dict:
    pages = {
        "privacy_policy": {
            "eyebrow": "Legal | Privacy",
            "title": "Privacy Policy",
            "intro": "This policy explains how Postzyo collects, uses, stores, and protects account, scheduling, and analytics data when users access the platform.",
            "sections": [
                {
                    "heading": "Information We Collect",
                    "points": [
                        "Account identity data such as name, email address, and Google profile picture used for login and workspace identity.",
                        "Connected Meta asset data such as Facebook Page IDs, Instagram Business account IDs, page names, token metadata, and encrypted access tokens.",
                        "Operator-generated content such as scheduled post captions, media URLs, uploaded media files, planning items, and workspace settings.",
                        "Analytics and automation records such as insight snapshots, publishing logs, retry states, token health states, and queue execution metadata.",
                    ],
                },
                {
                    "heading": "How We Use Information",
                    "points": [
                        "To authenticate users and maintain secure access to the Postzyo workspace.",
                        "To connect Meta assets, publish scheduled content, store insights snapshots, and generate profile-specific recommendations.",
                        "To monitor system health, diagnose failures, prevent duplicate tasks, and improve automation reliability.",
                        "To comply with platform, security, legal, and audit requirements where applicable.",
                    ],
                },
                {
                    "heading": "How We Protect Information",
                    "points": [
                        "Sensitive Meta access tokens are stored in encrypted form inside the application data layer.",
                        "Production deployments are intended to run behind HTTPS with secure cookie, HSTS, and reverse-proxy protections enabled.",
                        "Operational access is restricted to authorized users and infrastructure administrators responsible for support, reliability, and security.",
                    ],
                },
                {
                    "heading": "Data Sharing",
                    "points": [
                        "Postzyo does not sell operator or customer data.",
                        "Data may be processed through service providers required for app operation, including hosting, database, queueing, authentication, payment, AI, and Meta platform integrations.",
                        "Data may be disclosed if required by law, legal process, or a legitimate platform security obligation.",
                    ],
                },
                {
                    "heading": "Data Retention",
                    "points": [
                        "User account data, scheduled content, insight snapshots, and automation records are retained for as long as required to operate the service, troubleshoot issues, maintain historical analytics, and satisfy legal obligations.",
                        "Users may request deletion of account-linked data through the Data Deletion page and support contact listed below.",
                    ],
                },
            ],
        },
        "terms": {
            "eyebrow": "Legal | Terms",
            "title": "Terms of Service",
            "intro": "These terms govern access to Postzyo, including Meta connection workflows, social publishing automation, analytics storage, and AI-assisted recommendations.",
            "sections": [
                {
                    "heading": "Service Scope",
                    "points": [
                        "Postzyo provides tools for connecting Meta assets, scheduling and publishing social media posts, storing analytics snapshots, and generating operational recommendations.",
                        "Features may evolve over time, and some capabilities depend on third-party platform availability, permissions, rate limits, and policy approval.",
                    ],
                },
                {
                    "heading": "User Responsibilities",
                    "points": [
                        "Users must provide accurate account information and maintain control over connected Meta and Google accounts.",
                        "Users are responsible for ensuring they have rights to publish uploaded media, captions, and campaign content.",
                        "Users must not use Postzyo for unlawful, fraudulent, abusive, spam, or policy-violating automation.",
                    ],
                },
                {
                    "heading": "Third-Party Platforms",
                    "points": [
                        "Postzyo depends on third-party services such as Meta, Google, hosting providers, payment providers, and AI providers.",
                        "Feature availability, performance, or limits may change when upstream providers change APIs, rate limits, permissions, or policies.",
                    ],
                },
                {
                    "heading": "Availability and Limits",
                    "points": [
                        "The service is provided on a commercially reasonable basis, but uninterrupted availability is not guaranteed.",
                        "Temporary delays can occur due to upstream API throttling, token expiry, infrastructure maintenance, or scheduled deployments.",
                    ],
                },
                {
                    "heading": "Termination",
                    "points": [
                        "Access may be suspended or terminated if a user violates these terms, abuses the service, or creates security, legal, or platform risk.",
                        "Users may stop using the service at any time and may request account-related data deletion through the published deletion workflow.",
                    ],
                },
            ],
        },
        "data_deletion": {
            "eyebrow": "Legal | Data Deletion",
            "title": "User Data Deletion",
            "intro": "Postzyo provides a documented process for requesting deletion of user-linked account data, Meta connection records, and related workspace information.",
            "sections": [
                {
                    "heading": "How to Request Deletion",
                    "points": [
                        "Send a deletion request from the email address associated with your Postzyo account to 1995postzyo@gmail.com.",
                        "Use the subject line: Postzyo Data Deletion Request.",
                        "Include your account email address and any connected workspace identifiers that help us locate the correct account.",
                    ],
                },
                {
                    "heading": "What We Delete",
                    "points": [
                        "User profile details stored in Postzyo, including Google-linked profile metadata kept by the app.",
                        "Connected Meta account records, encrypted token records, scheduling data, planning items, and stored insight snapshots associated with the account when deletion is approved.",
                        "Operational logs may be retained where required for fraud prevention, security analysis, legal compliance, or financial record-keeping.",
                    ],
                },
                {
                    "heading": "Processing Window",
                    "points": [
                        "Deletion requests are reviewed manually for identity verification and safety.",
                        "Approved requests are generally processed within 7 to 15 business days, depending on system scope and legal retention requirements.",
                    ],
                },
                {
                    "heading": "Important Notes",
                    "points": [
                        "Deleting Postzyo data does not automatically delete data held directly by Meta, Google, or other third-party providers.",
                        "Users may also need to remove Postzyo access from connected Meta or Google account settings separately.",
                    ],
                },
            ],
        },
    }
    return pages[page_key]


def privacy_policy_view(request):
    return render(request, "accounts/legal_page.html", _legal_page_context("privacy_policy"))


def terms_view(request):
    return render(request, "accounts/legal_page.html", _legal_page_context("terms"))


def data_deletion_view(request):
    return render(request, "accounts/legal_page.html", _legal_page_context("data_deletion"))


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


def _set_persistent_session(request) -> None:
    request.session.set_expiry(int(getattr(settings, "SESSION_COOKIE_AGE", 1209600)))
    request.session.modified = True


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

    user_profile, created = UserProfile.objects.get_or_create(user=user)
    if created:
        user_profile.activate_trial(commit=False)
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
    user_profile.refresh_subscription_state(commit=False)
    if profile_changed or created:
        user_profile.save()

    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    _set_persistent_session(request)
    return redirect("dashboard:home")


def logout_view(request):
    logout(request)
    return redirect("login")
