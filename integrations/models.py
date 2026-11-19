from uuid import uuid4

from django.conf import settings
from django.db import models

from core.constants import PLATFORM_CHOICES
from core.fields import EncryptedTextField


class ConnectedAccountQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if "access_token" in kwargs:
            kwargs.setdefault("token_generation", uuid4())
        return super().update(**kwargs)


class ConnectedAccount(models.Model):
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    page_id = models.CharField(max_length=100)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name='connected_accounts')
    page_name = models.CharField(max_length=255)
    ig_user_id = models.CharField(max_length=100, blank=True, null=True)
    profile_picture_url = models.URLField(max_length=1000, blank=True, default="")
    access_token = EncryptedTextField()
    token_generation = models.UUIDField(default=uuid4, editable=False)
    publishing_permission_granted = models.BooleanField(null=True, default=None)
    insight_refresh_owner = models.UUIDField(null=True, editable=False)
    insight_refresh_expires_at = models.DateTimeField(null=True, editable=False)
    is_active = models.BooleanField(default=True)
    token_expires_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = ConnectedAccountQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "platform", "page_id"], name="uniq_user_platform_page"),
        ]
        indexes = [
            models.Index(fields=["user", "is_active"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.page_name} ({self.platform})"

    def save(self, *args, update_fields=None, **kwargs):
        if not self._state.adding and (update_fields is None or "access_token" in update_fields):
            self.token_generation = uuid4()
            if update_fields is not None:
                update_fields = {*update_fields, "token_generation"}
        return super().save(*args, update_fields=update_fields, **kwargs)


class MetaUserToken(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="meta_user_token")
    access_token = EncryptedTextField()
    generation = models.UUIDField(default=uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"Meta token for user {self.user_id}"
