from django.db import models

from core.constants import PLATFORM_CHOICES
from core.fields import EncryptedTextField


class ConnectedAccount(models.Model):
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    page_id = models.CharField(max_length=100)
    page_name = models.CharField(max_length=255)
    ig_user_id = models.CharField(max_length=100, blank=True, null=True)
    access_token = EncryptedTextField()
    token_expires_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["platform", "page_id"], name="uniq_platform_page"),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.page_name} ({self.platform})"
