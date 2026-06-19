import time

from django.core.management.base import BaseCommand

from core.constants import INSTAGRAM
from core.services.meta_client import MetaClient
from integrations.models import ConnectedAccount


class Command(BaseCommand):
    help = "Backfill profile_picture_url for connected accounts (one-time, for accounts connected before pictures were stored)."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Refetch even for accounts that already have a picture.")
        parser.add_argument("--sleep", type=float, default=0.5, help="Seconds to wait between Meta calls (rate-limit safety).")

    def handle(self, *args, **options):
        client = MetaClient()
        qs = ConnectedAccount.objects.filter(is_active=True)
        if not options["all"]:
            qs = qs.filter(profile_picture_url="")

        total = qs.count()
        updated = 0
        skipped = 0
        self.stdout.write(f"Backfilling pictures for {total} account(s)...")
        for account in qs.iterator():
            token = (account.access_token or "").strip()
            if not token:
                skipped += 1
                continue
            target_id = account.ig_user_id if account.platform == INSTAGRAM else account.page_id
            url = client.fetch_profile_picture_url(account.platform, target_id, token)
            if url:
                account.profile_picture_url = url
                account.save(update_fields=["profile_picture_url", "updated_at"])
                updated += 1
            else:
                skipped += 1
            time.sleep(max(0.0, float(options["sleep"])))

        self.stdout.write(self.style.SUCCESS(f"Done. updated={updated} skipped={skipped} total={total}"))
