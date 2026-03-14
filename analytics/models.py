from django.contrib.auth import get_user_model
from django.db import models

from core.constants import PLATFORM_CHOICES
from integrations.models import ConnectedAccount


class InsightSnapshot(models.Model):
    account = models.ForeignKey(ConnectedAccount, on_delete=models.CASCADE, related_name="insight_snapshots")
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    payload = models.JSONField()
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fetched_at"]

    def __str__(self) -> str:
        return f"{self.account_id}-{self.platform}-{self.fetched_at.isoformat()}"


class BulkInsightRefreshRun(models.Model):
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_COMPLETED_WITH_ERRORS = "completed_with_errors"
    STATUS_CHOICES = [
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_COMPLETED_WITH_ERRORS, "Completed with errors"),
    ]

    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name="bulk_insight_refresh_runs")
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    total_accounts = models.PositiveIntegerField(default=0)
    queued_count = models.PositiveIntegerField(default=0)
    skipped_no_token = models.PositiveIntegerField(default=0)
    enqueue_failed = models.PositiveIntegerField(default=0)
    completed_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"refresh-run-{self.id}-user-{self.user_id}-{self.status}"
