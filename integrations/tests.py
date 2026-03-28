from django.contrib.auth import get_user_model
from django.test import TestCase

from core.constants import FACEBOOK, INSTAGRAM

from .models import ConnectedAccount


class ConnectedAccountModelTests(TestCase):
    def test_unique_platform_page_id(self):
        user = get_user_model().objects.create_user(username="owner1", password="pass12345")
        ConnectedAccount.objects.create(
            user=user,
            platform=FACEBOOK,
            page_id="123",
            page_name="Page A",
            access_token="token-1",
        )

        with self.assertRaises(Exception):
            ConnectedAccount.objects.create(
                user=user,
                platform=FACEBOOK,
                page_id="123",
                page_name="Page B",
                access_token="token-2",
            )

    def test_token_is_encrypted_at_rest(self):
        user = get_user_model().objects.create_user(username="owner2", password="pass12345")
        account = ConnectedAccount.objects.create(
            user=user,
            platform=INSTAGRAM,
            page_id="ig-1",
            page_name="IG Page",
            access_token="plain-token",
        )

        fetched = ConnectedAccount.objects.get(id=account.id)
        self.assertEqual(fetched.access_token, "plain-token")
