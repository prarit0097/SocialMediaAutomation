from django.urls import path

from . import assistant_views, blog
from .views import (
    AdminLoginView,
    data_deletion_view,
    google_signup_callback,
    google_signup_start,
    help_view,
    logout_view,
    pricing_view,
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
    path("pricing/", pricing_view, name="pricing"),
    path("privacy-policy/", privacy_policy_view, name="privacy_policy"),
    path("terms/", terms_view, name="terms"),
    path("data-deletion/", data_deletion_view, name="data_deletion"),
    path("help/", help_view, name="help"),
    path("blog/", blog.blog_index, name="blog_index"),
    path("blog/<slug:slug>/", blog.blog_article, name="blog_article"),
    path("assistant/ask/", assistant_views.ask, name="homepage_assistant_ask"),
    path("assistant/follow-up/", assistant_views.follow_up, name="homepage_assistant_follow_up"),
]
