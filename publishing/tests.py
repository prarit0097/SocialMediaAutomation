from django.core.exceptions import ValidationError
from django.test import TestCase

from core.constants import FACEBOOK, INSTAGRAM
from integrations.models import ConnectedAccount

from .models import ScheduledPost


class ScheduledPostModelTests(TestCase):
    def setUp(self):
        self.fb_account = ConnectedAccount.objects.create(
            platform=FACEBOOK,
            page_id="1",
            page_name="FB",
            access_token="token",
        )
        self.ig_account = ConnectedAccount.objects.create(
            platform=INSTAGRAM,
            page_id="2",
            page_name="IG",
            access_token="token",
            ig_user_id="2",
        )

    def test_facebook_requires_message(self):
        post = ScheduledPost(
            account=self.fb_account,
            platform=FACEBOOK,
            message="",
            scheduled_for="2026-03-10T00:00:00Z",
        )
        with self.assertRaises(ValidationError):
            post.full_clean()

    def test_instagram_requires_media_url(self):
        post = ScheduledPost(
            account=self.ig_account,
            platform=INSTAGRAM,
            message="caption",
            scheduled_for="2026-03-10T00:00:00Z",
        )
        with self.assertRaises(ValidationError):
            post.full_clean()
