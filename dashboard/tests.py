from unittest.mock import patch
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from core.exceptions import MetaAPIError
from integrations.models import ConnectedAccount


class DashboardAuthTests(TestCase):
    def setUp(self):
        self.client = Client()
        cache.clear()

    def test_dashboard_requires_login(self):
        response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_ai_insights_page_loads_for_authenticated_user(self):
        user_model = get_user_model()
        user_model.objects.create_user(username="aiadmin", password="pass12345")
        self.client.login(username="aiadmin", password="pass12345")

        response = self.client.get("/dashboard/ai-insights/")
        self.assertEqual(response.status_code, 200)

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

    @override_settings(
        PUBLIC_BASE_URL="https://old-tunnel.ngrok-free.app",
        META_REDIRECT_URI="https://old-tunnel.ngrok-free.app/auth/meta/callback",
        ALLOWED_HOSTS=["testserver", "new-tunnel.ngrok-free.app"],
    )
    def test_public_url_status_reports_request_host_mismatch(self):
        user_model = get_user_model()
        user_model.objects.create_user(username="admin", password="pass12345")
        self.client.login(username="admin", password="pass12345")

        response = self.client.get("/dashboard/public-url-status/", HTTP_HOST="new-tunnel.ngrok-free.app")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertTrue(any("PUBLIC_BASE_URL points to old-tunnel.ngrok-free.app" in item for item in body["warnings"]))
        self.assertTrue(any("Ngrok free domains can rotate" in item for item in body["notes"]))

    @patch("dashboard.views.MetaClient.debug_token")
    def test_token_health_status_reports_green_when_tokens_valid(self, mock_debug_token):
        user_model = get_user_model()
        user_model.objects.create_user(username="admin", password="pass12345")
        self.client.login(username="admin", password="pass12345")
        ConnectedAccount.objects.create(
            platform="facebook",
            page_id="fb-1",
            page_name="Valid FB",
            access_token="token-shared",
        )
        ConnectedAccount.objects.create(
            platform="instagram",
            page_id="ig-1",
            page_name="Valid IG",
            ig_user_id="ig-1",
            access_token="token-shared",
        )
        mock_debug_token.return_value = {"data": {"is_valid": True}}

        response = self.client.get("/dashboard/token-health-status/")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["level"], "ok")
        self.assertEqual(body["checked_accounts"], 2)
        self.assertEqual(body["checked_tokens"], 1)
        self.assertEqual(body["invalid_accounts"], [])

    @patch("dashboard.views.MetaClient.debug_token")
    def test_token_health_status_reports_red_when_token_invalid(self, mock_debug_token):
        user_model = get_user_model()
        user_model.objects.create_user(username="admin", password="pass12345")
        self.client.login(username="admin", password="pass12345")
        ConnectedAccount.objects.create(
            platform="facebook",
            page_id="fb-1",
            page_name="Broken FB",
            access_token="broken-token",
        )
        mock_debug_token.return_value = {"data": {"is_valid": False, "error": {"message": "Token expired"}}}

        response = self.client.get("/dashboard/token-health-status/")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["level"], "bad")
        self.assertEqual(body["invalid_accounts"][0]["page_name"], "Broken FB")
        self.assertIn("Connect Facebook + Instagram", body["next_steps"][0])

    @patch("dashboard.views.MetaClient.debug_token")
    def test_token_health_status_stays_green_on_meta_rate_limit_without_confirmed_invalid_token(self, mock_debug_token):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="admin", password="pass12345")
        self.client.login(username="admin", password="pass12345")
        ConnectedAccount.objects.create(
            platform="facebook",
            page_id="fb-1",
            page_name="Recent FB",
            access_token="recent-token",
        )
        cache.set(
            f"meta_last_sync:{user.id}",
            {
                "synced_at": timezone.now().isoformat(),
            },
            timeout=600,
        )
        mock_debug_token.side_effect = MetaAPIError("(#4) Application request limit reached (code=4)")

        response = self.client.get("/dashboard/token-health-status/")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["level"], "ok")
        self.assertEqual(body["scope"], "recent_sync")
        self.assertIn("rate limit", body["summary"].lower())

    @patch("dashboard.views.MetaClient.debug_token")
    def test_token_health_status_reports_red_when_stale_accounts_exist(self, mock_debug_token):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="staleadmin", password="pass12345")
        self.client.login(username="staleadmin", password="pass12345")
        fresh = ConnectedAccount.objects.create(
            platform="facebook",
            page_id="fb-fresh",
            page_name="Fresh FB",
            access_token="fresh-token",
        )
        stale = ConnectedAccount.objects.create(
            platform="facebook",
            page_id="fb-stale",
            page_name="Stale FB",
            access_token="stale-token",
        )
        ConnectedAccount.objects.filter(id=fresh.id).update(updated_at=timezone.now())
        ConnectedAccount.objects.filter(id=stale.id).update(updated_at=timezone.now() - timedelta(hours=2))
        mock_debug_token.return_value = {"data": {"is_valid": True}}

        response = self.client.get("/dashboard/token-health-status/")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["level"], "bad")
        self.assertTrue(body["stale_accounts"])
        self.assertEqual(body["stale_accounts"][0]["page_name"], "Stale FB")
