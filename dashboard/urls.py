from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("accounts/", views.accounts_page, name="accounts"),
    path("scheduler/", views.scheduler_page, name="scheduler"),
    path("insights/", views.insights_page, name="insights"),
    path("public-url-status/", views.public_url_status, name="public_url_status"),
]
