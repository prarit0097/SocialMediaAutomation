from django.contrib import admin

from .models import ScheduledPost


@admin.register(ScheduledPost)
class ScheduledPostAdmin(admin.ModelAdmin):
    list_display = ("id", "platform", "account", "scheduled_for", "status", "stage", "blocked_reason", "published_at")
    list_filter = ("platform", "status", "stage")
    search_fields = ("external_post_id", "provider_upload_id", "ig_container_id", "account__page_name")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
