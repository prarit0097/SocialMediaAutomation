from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.utils import OperationalError
from django.test import Client, TestCase
from django.utils import timezone
from unittest.mock import patch

from analytics.models import InsightSnapshot
from publishing.models import ScheduledPost
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

    def test_list_accounts_includes_latest_post_time_from_snapshot(self):
        account = ConnectedAccount.objects.create(
            platform="facebook",
            page_id="1",
            page_name="Page 1",
            access_token="token",
        )
        newer = (timezone.now() - timedelta(hours=2)).isoformat()
        older = (timezone.now() - timedelta(hours=6)).isoformat()
        InsightSnapshot.objects.create(
            account=account,
            platform="facebook",
            payload={
                "published_posts": [
                    {"published_at": older},
                    {"published_at": newer},
                ]
            },
        )

        response = self.client.get("/api/accounts/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        row = next(item for item in payload if item["id"] == account.id)
        self.assertEqual(row["last_post_at"], newer)
        self.assertFalse(row["last_post_is_stale"])

    def test_list_accounts_falls_back_to_latest_published_scheduled_post(self):
        account = ConnectedAccount.objects.create(
            platform="facebook",
            page_id="1",
            page_name="Page 1",
            access_token="token",
        )
        post = ScheduledPost.objects.create(
            account=account,
            platform="facebook",
            message="hello",
            scheduled_for=timezone.now() - timedelta(hours=30),
            media_url="https://example.com/test.jpg",
            status="published",
            published_at=timezone.now() - timedelta(hours=30),
        )

        response = self.client.get("/api/accounts/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        row = next(item for item in payload if item["id"] == account.id)
        self.assertEqual(row["last_post_at"], post.published_at.isoformat())
        self.assertTrue(row["last_post_is_stale"])

    def test_list_accounts_marks_account_stale_when_not_in_latest_sync_window(self):
        account = ConnectedAccount.objects.create(
            platform="facebook",
            page_id="1",
            page_name="Page 1",
            access_token="token",
        )
        cache.set(
            f"meta_last_sync:{self.user.id}",
            {
                "synced_at": timezone.now().isoformat(),
            },
            timeout=600,
        )
        ConnectedAccount.objects.filter(id=account.id).update(updated_at=timezone.now() - timedelta(hours=2))

        response = self.client.get("/api/accounts/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        row = next(item for item in payload if item["id"] == account.id)
        self.assertTrue(row["is_sync_stale"])
        self.assertEqual(row["sync_state"], "stale")

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

    @patch("integrations.views.MetaClient._get")
    @patch("integrations.views.MetaClient.debug_token")
    def test_meta_pages_catalog_auto_syncs_connectable_instagram_profile(self, mock_debug_token, mock_get):
        ConnectedAccount.objects.create(
            platform="facebook",
            page_id="10",
            page_name="BodyByte",
            access_token="token",
            ig_user_id="17841479977081188",
        )
        mock_debug_token.return_value = {
            "data": {
                "granular_scopes": [
                    {"scope": "pages_show_list", "target_ids": ["10", "17841479977081188"]},
                ]
            }
        }
        mock_get.return_value = {
            "id": "17841479977081188",
            "username": "bodybyte",
            "profile_picture_url": "https://example.com/ig.jpg",
        }

        response = self.client.get("/api/accounts/meta-pages/?refresh=1")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        by_id = {row["page_id"]: row for row in payload["rows"]}
        self.assertEqual(by_id["17841479977081188"]["status"], "connected")
        self.assertEqual(by_id["17841479977081188"]["connectability"], "connected")

        synced = ConnectedAccount.objects.get(platform="instagram", page_id="17841479977081188")
        self.assertEqual(synced.page_name, "bodybyte (IG)")
        self.assertEqual(synced.access_token, "token")

    @patch("integrations.views.MetaClient._get")
    @patch("integrations.views.MetaClient.debug_token")
    def test_meta_pages_catalog_handles_sqlite_lock_gracefully(self, mock_debug_token, mock_get):
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

        with patch.object(ConnectedAccount.objects, "update_or_create", side_effect=OperationalError("database is locked")):
            response = self.client.get("/api/accounts/meta-pages/?refresh=1")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        by_id = {row["page_id"]: row for row in payload["rows"]}
        self.assertEqual(by_id["58"]["status"], "catalog-only")
        self.assertEqual(by_id["58"]["connectability"], "connectable")
        self.assertIn("database is busy", by_id["58"]["reason"])
