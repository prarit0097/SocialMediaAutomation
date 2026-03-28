from django.conf import settings
from django.db import models

from core.constants import PLATFORM_CHOICES
from core.fields import EncryptedTextField


class ConnectedAccount(models.Model):
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    page_id = models.CharField(max_length=100)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name='connected_accounts')
    page_name = models.CharField(max_length=255)
    ig_user_id = models.CharField(max_length=100, blank=True, null=True)
    access_token = EncryptedTextField()
    is_active = models.BooleanField(default=True)
    token_expires_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "platform", "page_id"], name="uniq_user_platform_page"),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.page_name} ({self.platform})"


class MetaUserToken(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="meta_user_token")
    access_token = EncryptedTextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"Meta token for user {self.user_id}"
