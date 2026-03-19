from django.urls import path

from .views import (
    AdminLoginView,
    data_deletion_view,
    google_signup_callback,
    google_signup_start,
    logout_view,
    privacy_policy_view,
    signup_view,
    terms_view,
)

urlpatterns = [
    path("login/", AdminLoginView.as_view(), name="login"),
    path("signup/", signup_view, name="signup"),
    path("signup/google/start/", google_signup_start, name="google_signup_start"),
    path("signup/google/callback/", google_signup_callback, name="google_signup_callback"),
    path("logout/", logout_view, name="logout"),
    path("privacy-policy/", privacy_policy_view, name="privacy_policy"),
    path("terms/", terms_view, name="terms"),
    path("data-deletion/", data_deletion_view, name="data_deletion"),
]
