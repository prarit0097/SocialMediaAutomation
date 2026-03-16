from django.urls import path

from .views import AdminLoginView, google_signup_callback, google_signup_start, logout_view, signup_view

urlpatterns = [
    path("login/", AdminLoginView.as_view(), name="login"),
    path("signup/", signup_view, name="signup"),
    path("signup/google/start/", google_signup_start, name="google_signup_start"),
    path("signup/google/callback/", google_signup_callback, name="google_signup_callback"),
    path("logout/", logout_view, name="logout"),
]
