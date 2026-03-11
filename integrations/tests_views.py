from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase

from integrations.models import ConnectedAccount


class IntegrationsViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="admin", password="pass12345")
        self.client.login(username="admin", password="pass12345")

    def test_meta_callback_returns_json_when_oauth_error_present(self):
        response = self.client.get(
            "/auth/meta/callback",
            {"error": "access_denied", "error_description": "User denied permission"},
        )
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["error"], "Meta OAuth failed")
        self.assertIn("User denied permission", body["details"])

    def test_accounts_sync_status_default_payload(self):
        ConnectedAccount.objects.create(
            platform="facebook",
            page_id="1",
            page_name="Page 1",
            access_token="token",
        )
        response = self.client.get("/api/accounts/sync-status/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsNone(payload["meta_pages_synced"])
        self.assertEqual(payload["facebook_connected_total"], 1)

    def test_accounts_sync_status_reads_cached_sync(self):
        cache.set(
            f"meta_last_sync:{self.user.id}",
            {
                "meta_pages_synced": 41,
                "facebook_connected_total": 10,
                "instagram_connected_total": 2,
                "synced_at": "2026-03-11T04:00:00+00:00",
            },
            timeout=60,
        )
        response = self.client.get("/api/accounts/sync-status/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["meta_pages_synced"], 41)
        self.assertEqual(payload["facebook_connected_total"], 10)
