from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse

from accounts.models import UserProfile


class SubscriptionAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            return self.get_response(request)

        if request.path.startswith("/static/") or request.path.startswith("/media/"):
            return self.get_response(request)

        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        profile.refresh_subscription_state()
        request.subscription_profile = profile
        if profile.subscription_status == UserProfile.SUBSCRIPTION_STATUS_ACTIVE:
            return self.get_response(request)

        allowed_paths = {
            reverse("dashboard:subscription"),
            reverse("dashboard:subscription_expired"),
            reverse("dashboard:subscription_create_order"),
            reverse("dashboard:subscription_verify_payment"),
            reverse("logout"),
        }
        if request.path in allowed_paths:
            return self.get_response(request)

        if request.path.startswith("/dashboard/") and request.method == "GET":
            return redirect("dashboard:subscription_expired")

        return JsonResponse(
            {
                "error": "Your app access has expired. Complete payment to continue.",
                "code": "subscription_expired",
                "redirect_url": reverse("dashboard:subscription"),
            },
            status=402,
        )
