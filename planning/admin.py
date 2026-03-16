from django.contrib import admin

from .models import CalendarContentItem, ContentTag


@admin.register(ContentTag)
class ContentTagAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "category", "owner", "updated_at")
    search_fields = ("name", "slug")
    list_filter = ("category",)


@admin.register(CalendarContentItem)
class CalendarContentItemAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "owner", "platform", "status", "start_at")
    list_filter = ("platform", "status")
    search_fields = ("title", "caption", "notes")
