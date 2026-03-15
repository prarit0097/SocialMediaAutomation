from django.urls import path

from . import views

urlpatterns = [
    path("posts/schedule/", views.schedule_post, name="schedule_post"),
    path("posts/scheduled/", views.list_scheduled_posts, name="list_scheduled_posts"),
    path("posts/<int:post_id>/retry/", views.retry_failed_post, name="retry_failed_post"),
]
