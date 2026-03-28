from django.conf import settings
from django.db import models


class SubscriptionOrder(models.Model):
    STATUS_PENDING = "pending"
    STATUS_VERIFIED = "verified"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_VERIFIED, "Verified"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscription_orders")
    order_id = models.CharField(max_length=120, unique=True)
    plan_key = models.CharField(max_length=40)
    billing_cycle = models.CharField(max_length=40)
    title = models.CharField(max_length=140, blank=True)
    price_label = models.CharField(max_length=80, blank=True)
    razorpay_payment_id = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    consumed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.order_id} ({self.user_id})"
