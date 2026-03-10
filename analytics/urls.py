from django.urls import path

from . import views

urlpatterns = [
    path("insights/<int:account_id>/", views.account_insights, name="account_insights"),
]
