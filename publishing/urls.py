from django.urls import path

from . import views

urlpatterns = [
    path("posts/schedule/", views.schedule_post, name="schedule_post"),
    path("posts/scheduled/", views.list_scheduled_posts, name="list_scheduled_posts"),
    path("posts/<int:post_id>/retry/", views.retry_failed_post, name="retry_failed_post"),
    path("ai/generate-image/", views.generate_ai_image, name="generate_ai_image"),
]
