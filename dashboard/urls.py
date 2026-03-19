from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("accounts/", views.accounts_page, name="accounts"),
    path("scheduler/", views.scheduler_page, name="scheduler"),
    path("planning/", views.planning_page, name="planning"),
    path("insights/", views.insights_page, name="insights"),
    path("ai-insights/", views.ai_insights_page, name="ai_insights"),
    path("profile/", views.profile_page, name="profile"),
    path("subscription/", views.subscription_page, name="subscription"),
    path("subscription/expired/", views.subscription_expired_page, name="subscription_expired"),
    path("meta-app-config/", views.meta_app_config, name="meta_app_config"),
    path("profile-data/", views.profile_data, name="profile_data"),
    path("subscription/create-order/", views.subscription_create_order, name="subscription_create_order"),
    path("subscription/verify-payment/", views.subscription_verify_payment, name="subscription_verify_payment"),
    path("public-url-status/", views.public_url_status, name="public_url_status"),
    path("token-health-status/", views.token_health_status, name="token_health_status"),
]
