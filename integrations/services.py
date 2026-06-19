from django.db import transaction

from core.constants import FACEBOOK, INSTAGRAM
from .models import ConnectedAccount


def _facebook_picture_url(page: dict) -> str:
    return ((page.get("picture") or {}).get("data") or {}).get("url") or ""


def upsert_connected_accounts(pages: list[dict], user) -> None:
    with transaction.atomic():
        for page in pages:
            ig_data = page.get("instagram_business_account") or {}
            fb_defaults = {
                "page_name": page["name"],
                "ig_user_id": ig_data.get("id"),
                "access_token": page["access_token"],
                "is_active": True,
            }
            fb_picture = _facebook_picture_url(page)
            if fb_picture:
                fb_defaults["profile_picture_url"] = fb_picture
            ConnectedAccount.objects.update_or_create(
                user=user,
                platform=FACEBOOK,
                page_id=page["id"],
                defaults=fb_defaults,
            )

            ig_id = ig_data.get("id")
            if ig_id:
                ig_defaults = {
                    "page_name": f"{page['name']} (IG)",
                    "ig_user_id": ig_id,
                    "access_token": page["access_token"],
                    "is_active": True,
                }
                ig_picture = ig_data.get("profile_picture_url") or ""
                if ig_picture:
                    ig_defaults["profile_picture_url"] = ig_picture
                ConnectedAccount.objects.update_or_create(
                    user=user,
                    platform=INSTAGRAM,
                    page_id=ig_id,
                    defaults=ig_defaults,
                )
