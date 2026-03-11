from django.urls import path

from . import views

urlpatterns = [
    path("accounts/", views.list_accounts, name="list_accounts"),
    path("accounts/sync-status/", views.accounts_sync_status, name="accounts_sync_status"),
    path("accounts/meta-pages/", views.meta_pages_catalog, name="meta_pages_catalog"),
]
