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
