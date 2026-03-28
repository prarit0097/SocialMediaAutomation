from django.db import transaction

from core.constants import FACEBOOK, INSTAGRAM
from .models import ConnectedAccount


def upsert_connected_accounts(pages: list[dict], user) -> None:
    with transaction.atomic():
        for page in pages:
            ConnectedAccount.objects.update_or_create(
                user=user,
                platform=FACEBOOK,
                page_id=page["id"],
                defaults={
                    "page_name": page["name"],
                    "ig_user_id": (page.get("instagram_business_account") or {}).get("id"),
                    "access_token": page["access_token"],
                    "is_active": True,
                },
            )

            ig_data = page.get("instagram_business_account") or {}
            ig_id = ig_data.get("id")
            if ig_id:
                ConnectedAccount.objects.update_or_create(
                    user=user,
                    platform=INSTAGRAM,
                    page_id=ig_id,
                    defaults={
                        "page_name": f"{page['name']} (IG)",
                        "ig_user_id": ig_id,
                        "access_token": page["access_token"],
                        "is_active": True,
                    },
                )
