from django.urls import path

from . import views

urlpatterns = [
    path("planning/tags/", views.planning_tags, name="planning_tags"),
    path("planning/tags/create/", views.create_planning_tag, name="create_planning_tag"),
    path("planning/calendar/", views.calendar_items, name="planning_calendar_items"),
    path("planning/calendar/create/", views.create_calendar_item, name="create_calendar_item"),
    path("planning/calendar/<int:item_id>/", views.update_calendar_item, name="update_calendar_item"),
]
