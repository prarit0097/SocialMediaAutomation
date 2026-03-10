from django.urls import path

from .views import AdminLoginView, logout_view

urlpatterns = [
    path("login/", AdminLoginView.as_view(), name="login"),
    path("logout/", logout_view, name="logout"),
]
