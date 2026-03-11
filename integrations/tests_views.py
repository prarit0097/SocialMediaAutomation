from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from unittest.mock import patch

from integrations.models import ConnectedAccount


class IntegrationsViewTests(TestCase):
    def setUp(self):
        cache.clear()
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
        self.assertEqual(payload["meta_pages_synced"], 1)
        self.assertEqual(payload["facebook_connected_total"], 1)
        self.assertIsNotNone(payload["synced_at"])

    def test_accounts_sync_status_reads_cached_sync(self):
        cache.set(
            f"meta_last_sync:{self.user.id}",
            {
                "meta_pages_synced": 41,
                "facebook_connected_total": 10,
                "instagram_connected_total": 2,
                "token_target_ids_count": 41,
                "warning": "Meta returned fewer pages than token target_ids.",
                "synced_at": "2026-03-11T04:00:00+00:00",
            },
            timeout=60,
        )
        response = self.client.get("/api/accounts/sync-status/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["meta_pages_synced"], 41)
        self.assertEqual(payload["facebook_connected_total"], 10)
        self.assertEqual(payload["token_target_ids_count"], 41)

    @patch("integrations.views.MetaClient._get")
    @patch("integrations.views.MetaClient.debug_token")
    def test_meta_pages_catalog_includes_catalog_only_rows(self, mock_debug_token, mock_get):
        ConnectedAccount.objects.create(
            platform="facebook",
            page_id="10",
            page_name="Connected Page",
            access_token="token",
        )
        mock_debug_token.return_value = {
            "data": {
                "granular_scopes": [
                    {"scope": "pages_show_list", "target_ids": ["10", "20"]},
                ]
            }
        }
        mock_get.return_value = {"id": "20", "name": "Catalog Page"}

        response = self.client.get("/api/accounts/meta-pages/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["connected_pages"], 1)
        self.assertEqual(payload["total_pages"], 2)
        statuses = {row["page_id"]: row["status"] for row in payload["rows"]}
        self.assertEqual(statuses["10"], "connected")
        self.assertEqual(statuses["20"], "catalog-only")
        connectability = {row["page_id"]: row.get("connectability") for row in payload["rows"]}
        self.assertEqual(connectability["10"], "connected")
        self.assertEqual(connectability["20"], "not_connectable")
        reasons = {row["page_id"]: row.get("reason", "") for row in payload["rows"]}
        self.assertIn("token", reasons["10"].lower())
        self.assertTrue(len(reasons["20"]) > 0)

    @patch("integrations.views.MetaClient._get")
    @patch("integrations.views.MetaClient.debug_token")
    def test_meta_pages_catalog_auto_syncs_connectable_facebook_page(self, mock_debug_token, mock_get):
        ConnectedAccount.objects.create(
            platform="facebook",
            page_id="10",
            page_name="Connected Page",
            access_token="token",
        )
        mock_debug_token.return_value = {
            "data": {
                "granular_scopes": [
                    {"scope": "pages_show_list", "target_ids": ["10", "58"]},
                ]
            }
        }
        mock_get.return_value = {
            "id": "58",
            "name": "Riya Arora",
            "access_token": "page-token-58",
            "picture": {"data": {"url": "https://example.com/pic.jpg"}},
        }

        response = self.client.get("/api/accounts/meta-pages/?refresh=1")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        by_id = {row["page_id"]: row for row in payload["rows"]}
        self.assertEqual(by_id["58"]["status"], "connected")
        self.assertEqual(by_id["58"]["connectability"], "connected")
        self.assertIn("synced", by_id["58"]["reason"].lower())

        synced = ConnectedAccount.objects.get(platform="facebook", page_id="58")
        self.assertEqual(synced.page_name, "Riya Arora")
