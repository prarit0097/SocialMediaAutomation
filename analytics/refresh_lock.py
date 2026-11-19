from datetime import timedelta
from uuid import UUID, uuid4

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from integrations.models import ConnectedAccount


def acquire_insight_refresh(account_id: int, user_id: int) -> UUID | None:
    owner = uuid4()
    now = timezone.now()
    duration = max(20 * 60, settings.CELERY_TASK_TIME_LIMIT + 60)
    acquired = (
        ConnectedAccount.objects.filter(pk=account_id, user_id=user_id, is_active=True)
        .filter(Q(insight_refresh_expires_at__isnull=True) | Q(insight_refresh_expires_at__lte=now))
        .update(insight_refresh_owner=owner, insight_refresh_expires_at=now + timedelta(seconds=duration))
    )
    return owner if acquired else None


def release_insight_refresh(account_id: int, owner: UUID) -> None:
    ConnectedAccount.objects.filter(pk=account_id, insight_refresh_owner=owner).update(
        insight_refresh_owner=None, insight_refresh_expires_at=None,
    )
