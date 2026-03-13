from django.conf import settings
from django.core.management.base import BaseCommand

from core.services.meta_client import MetaClient
from integrations.models import ConnectedAccount


def _safe_console_text(value: str) -> str:
    return str(value).encode("cp1252", errors="replace").decode("cp1252")


class Command(BaseCommand):
    help = "Validate stored Meta tokens using debug_token endpoint"

    def handle(self, *args, **options):
        if not settings.META_APP_ID or not settings.META_APP_SECRET:
            self.stdout.write(self.style.WARNING("META_APP_ID / META_APP_SECRET missing"))
            return

        client = MetaClient()
        accounts = ConnectedAccount.objects.all().order_by("id")

        if not accounts.exists():
            self.stdout.write("No connected accounts found")
            return

        for account in accounts:
            try:
                data = client.debug_token(account.access_token).get("data", {})
                active = data.get("is_valid", False)
                expires_at = data.get("expires_at")
                self.stdout.write(
                    _safe_console_text(
                        f"[{account.id}] {account.page_name} ({account.platform}) active={active} expires_at={expires_at}"
                    )
                )
            except Exception as exc:  # noqa: BLE001
                self.stdout.write(self.style.ERROR(_safe_console_text(f"[{account.id}] failed: {exc}")))
