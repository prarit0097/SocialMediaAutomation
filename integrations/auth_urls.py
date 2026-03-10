from django.urls import path

from . import views

urlpatterns = [
    path("meta/start", views.meta_start, name="meta_start"),
    path("meta/callback", views.meta_callback, name="meta_callback"),
]
