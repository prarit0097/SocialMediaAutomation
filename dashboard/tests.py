from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase


class DashboardAuthTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_dashboard_requires_login(self):
        response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    @patch("core.services.meta_client.MetaClient.debug_token")
    @patch("core.services.meta_client.MetaClient.get_managed_pages")
    @patch("core.services.meta_client.MetaClient.exchange_code_for_token")
    def test_meta_callback_upserts_accounts(self, mock_exchange, mock_pages, mock_debug_token):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="admin", password="pass12345")
        self.client.login(username="admin", password="pass12345")

        cache.set("meta_oauth_state:state123", {"user_id": user.id}, timeout=600)

        mock_exchange.return_value = {"access_token": "user-token"}
        mock_pages.return_value = [
            {
                "id": "1",
                "name": "Main Page",
                "access_token": "page-token",
                "instagram_business_account": {"id": "ig-1"},
            }
        ]
        mock_debug_token.return_value = {"data": {"granular_scopes": []}}

        response = self.client.get("/auth/meta/callback", {"code": "abc", "state": "state123"})
        self.assertEqual(response.status_code, 302)
