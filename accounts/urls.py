from django.urls import path

from .views import AdminLoginView, logout_view, signup_view

urlpatterns = [
    path("login/", AdminLoginView.as_view(), name="login"),
    path("signup/", signup_view, name="signup"),
    path("logout/", logout_view, name="logout"),
]
