from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("accounts/", views.accounts_page, name="accounts"),
    path("scheduler/", views.scheduler_page, name="scheduler"),
    path("insights/", views.insights_page, name="insights"),
]
