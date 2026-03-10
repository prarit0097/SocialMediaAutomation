from django.contrib import admin

from .models import InsightSnapshot


@admin.register(InsightSnapshot)
class InsightSnapshotAdmin(admin.ModelAdmin):
    list_display = ("id", "account", "platform", "fetched_at")
    list_filter = ("platform",)
    readonly_fields = ("fetched_at",)
