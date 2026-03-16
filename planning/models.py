from django.conf import settings
from django.db import models
from django.utils.text import slugify

from core.constants import FACEBOOK, INSTAGRAM
from integrations.models import ConnectedAccount


class ContentTag(models.Model):
    CATEGORY_PILLAR = "pillar"
    CATEGORY_TAG = "tag"
    CATEGORY_CHOICES = [
        (CATEGORY_PILLAR, "Content Pillar"),
        (CATEGORY_TAG, "Tag"),
    ]

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="content_tags")
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=90)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default=CATEGORY_TAG)
    color = models.CharField(max_length=16, default="#1f6feb")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("owner", "slug", "category")]
        ordering = ["category", "name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:90]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_category_display()}: {self.name}"


class CalendarContentItem(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_REVIEW = "review"
    STATUS_APPROVED = "approved"
    STATUS_SCHEDULED = "scheduled"
    STATUS_PUBLISHED = "published"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_REVIEW, "Review"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_SCHEDULED, "Scheduled"),
        (STATUS_PUBLISHED, "Published"),
    ]

    PLATFORM_BOTH = "both"
    PLATFORM_CHOICES = [
        (FACEBOOK, "Facebook"),
        (INSTAGRAM, "Instagram"),
        (PLATFORM_BOTH, "Facebook + Instagram"),
    ]

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calendar_items")
    connected_account = models.ForeignKey(
        ConnectedAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="planning_items",
    )
    title = models.CharField(max_length=140)
    caption = models.TextField(blank=True)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField(null=True, blank=True)
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES, default=PLATFORM_BOTH)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    tags = models.ManyToManyField(ContentTag, blank=True, related_name="items")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_at", "id"]

    def __str__(self):
        return f"{self.title} ({self.start_at.isoformat()})"
