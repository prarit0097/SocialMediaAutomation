from django.core.exceptions import ValidationError
from django.db import models

from core.constants import (
    FACEBOOK,
    INSTAGRAM,
    PLATFORM_CHOICES,
    POST_STATUS_CHOICES,
    POST_STATUS_PENDING,
)
from integrations.models import ConnectedAccount


class ScheduledPost(models.Model):
    account = models.ForeignKey(ConnectedAccount, on_delete=models.CASCADE, related_name="scheduled_posts")
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    message = models.TextField(blank=True, null=True)
    media_url = models.URLField(blank=True, null=True)
    scheduled_for = models.DateTimeField()
    status = models.CharField(max_length=20, choices=POST_STATUS_CHOICES, default=POST_STATUS_PENDING)
    external_post_id = models.CharField(max_length=200, blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    published_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "scheduled_for"]),
            models.Index(fields=["account", "scheduled_for"]),
        ]
        ordering = ["-scheduled_for"]

    def clean(self):
        has_message = bool((self.message or "").strip())
        has_media = bool(self.media_url)

        if self.account_id and self.account and self.platform != self.account.platform:
            raise ValidationError({"platform": "Selected account and platform do not match."})

        if self.platform == FACEBOOK and not (has_message or has_media):
            raise ValidationError({"message": "Facebook posts require message or media_url."})

        if self.platform == INSTAGRAM and not self.media_url:
            raise ValidationError({"media_url": "Instagram posts require media_url."})

    def save(self, *args, **kwargs):
        # Skip full validation on partial updates (status transitions, error
        # messages, etc.) — running full_clean() here would reject the save
        # if any *unrelated* field fails validation, leaving the post stuck
        # in a stale state.  Full validation still runs for new rows and
        # full saves (no update_fields).
        if not kwargs.get("update_fields"):
            self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.platform}#{self.id} [{self.status}]"
