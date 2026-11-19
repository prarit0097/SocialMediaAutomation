from django.contrib import admin

from .models import HomepageLead, UserProfile


@admin.register(HomepageLead)
class HomepageLeadAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "created_at")
    search_fields = ("name", "email")
    date_hierarchy = "created_at"
    readonly_fields = list_display

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


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
