from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


def _default_subscription_expiry():
    return timezone.now().date() + timedelta(days=30)


class UserProfile(models.Model):
    SUBSCRIPTION_STATUS_ACTIVE = "active"
    SUBSCRIPTION_STATUS_EXPIRED = "expired"
    SUBSCRIPTION_STATUS_CHOICES = (
        (SUBSCRIPTION_STATUS_ACTIVE, "Active"),
        (SUBSCRIPTION_STATUS_EXPIRED, "Expired"),
    )

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    profile_picture_url = models.URLField(blank=True)
    subscription_plan = models.CharField(max_length=120, default="Starter")
    subscription_status = models.CharField(
        max_length=20,
        choices=SUBSCRIPTION_STATUS_CHOICES,
        default=SUBSCRIPTION_STATUS_ACTIVE,
    )
    subscription_expires_on = models.DateField(default=_default_subscription_expiry)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]

    def __str__(self):
        return f"UserProfile(user_id={self.user_id}, email={self.user.email})"

    @property
    def resolved_first_name(self) -> str:
        if self.first_name:
            return self.first_name
        if self.user.first_name:
            return self.user.first_name
        return self.user.username.split("@")[0]

    @property
    def resolved_last_name(self) -> str:
        if self.last_name:
            return self.last_name
        return self.user.last_name or ""
