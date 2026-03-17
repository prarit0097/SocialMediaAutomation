from django.contrib import admin

from .models import UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "first_name",
        "last_name",
        "subscription_plan",
        "subscription_status",
        "subscription_expires_on",
        "updated_at",
    )
    search_fields = ("user__username", "user__email", "first_name", "last_name", "subscription_plan")
    list_filter = ("subscription_status", "subscription_plan")
