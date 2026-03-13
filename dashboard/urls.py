from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("accounts/", views.accounts_page, name="accounts"),
    path("scheduler/", views.scheduler_page, name="scheduler"),
    path("insights/", views.insights_page, name="insights"),
    path("ai-insights/", views.ai_insights_page, name="ai_insights"),
    path("meta-app-config/", views.meta_app_config, name="meta_app_config"),
    path("public-url-status/", views.public_url_status, name="public_url_status"),
    path("token-health-status/", views.token_health_status, name="token_health_status"),
]
