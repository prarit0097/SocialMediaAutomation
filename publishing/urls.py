from django.urls import path

from . import views

urlpatterns = [
    path("posts/schedule/", views.schedule_post, name="schedule_post"),
    path("posts/bulk-schedule/", views.bulk_schedule_post, name="bulk_schedule_post"),
    path("posts/scheduled/", views.list_scheduled_posts, name="list_scheduled_posts"),
    path("posts/publish-health-status/", views.publish_health_status, name="publish_health_status"),
    path("posts/<int:post_id>/retry/", views.retry_failed_post, name="retry_failed_post"),
    path("posts/<int:post_id>/edit/", views.edit_scheduled_post, name="edit_scheduled_post"),
    path("posts/<int:post_id>/cancel/", views.cancel_scheduled_post, name="cancel_scheduled_post"),
]
