from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from integrations.models import MetaUserToken
from integrations.services import resync_all_page_tokens


class Command(BaseCommand):
    help = (
        "Re-mint fresh Meta page access tokens for ALL of a user's connected accounts "
        "from their stored user token. Fixes OAuth code=190 failures on Business-Manager "
        "pages that /me/accounts does not return (so reconnect alone never refreshed them)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, help="Django user id to resync")
        parser.add_argument("--email", type=str, help="User email (alternative to --user-id)")

    def handle(self, *args, **opts):
        User = get_user_model()
        user = None
        if opts.get("user_id"):
            user = User.objects.filter(id=opts["user_id"]).first()
        elif opts.get("email"):
            user = User.objects.filter(email=opts["email"]).first()

        if not user:
            self.stderr.write(self.style.ERROR("User not found. Pass --user-id or --email."))
            return

        token = (
            MetaUserToken.objects.filter(user_id=user.id)
            .values_list("access_token", flat=True)
            .first()
        )
        token = str(token or "").strip()
        if not token:
            self.stderr.write(
                self.style.ERROR(
                    f"No stored Meta user token for user {user.id}. "
                    "Ask the user to reconnect Meta first, then re-run this command."
                )
            )
            return

        self.stdout.write(f"Resyncing page tokens for user {user.id} ({user.email}) ...")
        stats = resync_all_page_tokens(user, token, stdout=self.stdout)
        self.stdout.write(self.style.SUCCESS(f"Done: {stats}"))
