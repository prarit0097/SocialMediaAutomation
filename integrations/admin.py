from django.contrib import admin

from .models import ConnectedAccount


@admin.register(ConnectedAccount)
class ConnectedAccountAdmin(admin.ModelAdmin):
    list_display = ("id", "platform", "page_name", "page_id", "updated_at")
    search_fields = ("page_name", "page_id", "ig_user_id")
    list_filter = ("platform",)
    readonly_fields = ("created_at", "updated_at")
