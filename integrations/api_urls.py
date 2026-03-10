from django.urls import path

from . import views

urlpatterns = [
    path("accounts/", views.list_accounts, name="list_accounts"),
]
