import calendar
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


def _default_subscription_expiry():
    return timezone.now().date() + timedelta(days=1)


class UserProfile(models.Model):
    SUBSCRIPTION_PLAN_TRIAL = "Trial"
    SUBSCRIPTION_PLAN_MONTHLY = "Monthly"
    SUBSCRIPTION_PLAN_YEARLY = "Yearly"
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
    subscription_plan = models.CharField(max_length=120, default=SUBSCRIPTION_PLAN_TRIAL)
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

    @staticmethod
    def _add_months(base_date, months: int):
        month_index = base_date.month - 1 + months
        year = base_date.year + month_index // 12
        month = month_index % 12 + 1
        day = min(base_date.day, calendar.monthrange(year, month)[1])
        return base_date.replace(year=year, month=month, day=day)

    @staticmethod
    def _add_years(base_date, years: int):
        month = base_date.month
        day = base_date.day
        max_day = calendar.monthrange(base_date.year + years, month)[1]
        return base_date.replace(year=base_date.year + years, day=min(day, max_day))

    @property
    def is_subscription_active(self) -> bool:
        self.refresh_subscription_state(commit=False)
        return self.subscription_status == self.SUBSCRIPTION_STATUS_ACTIVE

    def refresh_subscription_state(self, commit: bool = True) -> bool:
        today = timezone.now().date()
        normalized_plan = (self.subscription_plan or "").strip() or self.SUBSCRIPTION_PLAN_TRIAL
        if normalized_plan.lower() == "starter":
            normalized_plan = self.SUBSCRIPTION_PLAN_TRIAL

        expires_on = self.subscription_expires_on or today
        status = self.SUBSCRIPTION_STATUS_ACTIVE if expires_on >= today else self.SUBSCRIPTION_STATUS_EXPIRED

        changed = False
        if self.subscription_plan != normalized_plan:
            self.subscription_plan = normalized_plan
            changed = True
        if self.subscription_expires_on != expires_on:
            self.subscription_expires_on = expires_on
            changed = True
        if self.subscription_status != status:
            self.subscription_status = status
            changed = True

        if changed and commit:
            self.save(update_fields=["subscription_plan", "subscription_status", "subscription_expires_on", "updated_at"])
        return changed

    def activate_trial(self, commit: bool = True):
        self.subscription_plan = self.SUBSCRIPTION_PLAN_TRIAL
        self.subscription_expires_on = timezone.now().date() + timedelta(days=1)
        self.subscription_status = self.SUBSCRIPTION_STATUS_ACTIVE
        if commit:
            self.save(update_fields=["subscription_plan", "subscription_status", "subscription_expires_on", "updated_at"])

    def activate_paid_plan(self, billing_cycle: str, commit: bool = True):
        cycle = str(billing_cycle or "").strip().lower()
        today = timezone.now().date()
        base_date = max(today, self.subscription_expires_on or today)

        if cycle == "monthly":
            self.subscription_plan = self.SUBSCRIPTION_PLAN_MONTHLY
            self.subscription_expires_on = self._add_months(base_date, 1)
        elif cycle == "yearly":
            self.subscription_plan = self.SUBSCRIPTION_PLAN_YEARLY
            self.subscription_expires_on = self._add_years(base_date, 1)
        else:
            raise ValueError("Unsupported billing cycle.")

        self.subscription_status = self.SUBSCRIPTION_STATUS_ACTIVE
        if commit:
            self.save(update_fields=["subscription_plan", "subscription_status", "subscription_expires_on", "updated_at"])
