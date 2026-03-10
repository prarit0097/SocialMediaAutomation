from django.contrib import admin

from .models import ScheduledPost


@admin.register(ScheduledPost)
class ScheduledPostAdmin(admin.ModelAdmin):
    list_display = ("id", "platform", "account", "scheduled_for", "status", "published_at")
    list_filter = ("platform", "status")
    search_fields = ("message", "external_post_id", "account__page_name")
    readonly_fields = ("created_at", "updated_at")
