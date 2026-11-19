from django.core.exceptions import ValidationError
from django.conf import settings
from django.db import models
from django.db.models import Q

from core.constants import (
    FACEBOOK,
    INSTAGRAM,
    PLATFORM_CHOICES,
    POST_STATUS_CHOICES,
    POST_STATUS_PENDING,
    POST_STATUS_PROCESSING,
)
from integrations.models import ConnectedAccount


RETRY_WAIT = "retry_wait"
BLOCKED = "blocked"
OUTCOME_UNKNOWN = "outcome_unknown"
CANCELLED = "cancelled"
WAITING_STATES = [POST_STATUS_PENDING, RETRY_WAIT, BLOCKED]
DELIVERY_STATUSES = POST_STATUS_CHOICES + [
    (RETRY_WAIT, "Retry scheduled"), (BLOCKED, "Blocked"),
    (OUTCOME_UNKNOWN, "Outcome unknown"), (CANCELLED, "Cancelled"),
]


class ScheduleRequest(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    key = models.CharField(max_length=128)
    fingerprint = models.CharField(max_length=64)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "key"], name="publish_request_user_key")]


class ScheduledPost(models.Model):
    account = models.ForeignKey(ConnectedAccount, on_delete=models.CASCADE, related_name="scheduled_posts")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, editable=False)
    schedule_request = models.ForeignKey(ScheduleRequest, on_delete=models.SET_NULL, null=True, blank=True, related_name="posts")
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    message = models.TextField(blank=True, null=True)
    media_url = models.URLField(blank=True, null=True)
    scheduled_for = models.DateTimeField()
    display_timezone = models.CharField(max_length=64, default="Asia/Kolkata")
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=DELIVERY_STATUSES, default=POST_STATUS_PENDING)
    claim_generation = models.PositiveIntegerField(default=0)
    attempt_count = models.PositiveIntegerField(default=0)
    attempt_token_generation = models.UUIDField(null=True, editable=False)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    stage = models.CharField(max_length=32, blank=True)
    blocked_reason = models.CharField(max_length=40, blank=True)
    final_write_started_at = models.DateTimeField(null=True, blank=True)
    ig_container_id = models.CharField(max_length=200, blank=True)
    ig_container_created_at = models.DateTimeField(null=True, blank=True)
    provider_upload_id = models.CharField(max_length=200, blank=True)
    external_post_id = models.CharField(max_length=200, blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    published_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "scheduled_for"]),
            models.Index(fields=["status", "scheduled_for", "platform"]),
            models.Index(fields=["account", "scheduled_for"]),
            models.Index(fields=["account", "status"]),
            models.Index(fields=["platform", "status", "next_attempt_at"], name="publishing_due_platform_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["schedule_request", "account"], name="publish_request_account_leg"),
            models.UniqueConstraint(
                fields=["account"], condition=Q(platform=INSTAGRAM, status=POST_STATUS_PROCESSING),
                name="publish_one_ig_claim_per_account",
            ),
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
            if self._state.adding:
                self.owner_id = self.account.user_id if self.account_id else None
                self.next_attempt_at = self.next_attempt_at or self.scheduled_for
            self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.platform}#{self.id} [{self.status}]"


class ScheduleChange(models.Model):
    post = models.ForeignKey(ScheduledPost, on_delete=models.CASCADE, related_name="schedule_changes")
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    previous_scheduled_for = models.DateTimeField()
    scheduled_for = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
