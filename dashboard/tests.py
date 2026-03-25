from unittest.mock import patch
from datetime import timedelta
import json
import hmac
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from core.exceptions import MetaAPIError
from integrations.models import ConnectedAccount
from accounts.models import UserProfile


class DashboardAuthTests(TestCase):
    def setUp(self):
        self.client = Client()
        cache.clear()

    def test_dashboard_requires_login(self):
        response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_meta_app_config_requires_login(self):
        response = self.client.post(
            "/dashboard/meta-app-config/",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_meta_app_config_forbids_non_staff_user(self):
        user_model = get_user_model()
        user_model.objects.create_user(username="metablocked", password="pass12345")
        self.client.login(username="metablocked", password="pass12345")

        response = self.client.get("/dashboard/meta-app-config/")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "Forbidden.")

    def test_ai_insights_page_loads_for_authenticated_user(self):
        user_model = get_user_model()
        user_model.objects.create_user(username="aiadmin", password="pass12345")
        self.client.login(username="aiadmin", password="pass12345")

        response = self.client.get("/dashboard/ai-insights/")
        self.assertEqual(response.status_code, 200)

    def test_planning_page_loads_for_authenticated_user(self):
        user_model = get_user_model()
        user_model.objects.create_user(username="planningadmin", password="pass12345")
        self.client.login(username="planningadmin", password="pass12345")

        response = self.client.get("/dashboard/planning/")
        self.assertEqual(response.status_code, 200)

    def test_dashboard_home_omits_meta_configuration_and_setup_guide_sections(self):
        user_model = get_user_model()
        user_model.objects.create_user(username="homeadmin", password="pass12345")
        self.client.login(username="homeadmin", password="pass12345")

        response = self.client.get("/dashboard/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose where you want to work today.")
        self.assertNotContains(response, "Meta App Configuration")
        self.assertNotContains(response, "Setup Guide")
        self.assertNotContains(response, 'id="metaAppConfigForm"', html=False)
        self.assertNotContains(response, 'id="metaSetupGuide"', html=False)
        self.assertNotContains(response, "home-command-hero", html=False)
        self.assertNotContains(response, "home-module-card", html=False)

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

    def test_token_health_status_reports_red_when_no_accounts_connected(self):
        user_model = get_user_model()
        user_model.objects.create_user(username="noaccounts", password="pass12345")
        self.client.login(username="noaccounts", password="pass12345")

        response = self.client.get("/dashboard/token-health-status/")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["level"], "bad")
        self.assertEqual(body["checked_accounts"], 0)
        self.assertIn("No connected Meta accounts found", body["summary"])
        self.assertIn("Connect Facebook + Instagram", body["next_steps"][0])

    @patch("dashboard.views.MetaClient.debug_token")
    def test_token_health_cache_is_scoped_per_user(self, mock_debug_token):
        user_model = get_user_model()
        user_one = user_model.objects.create_user(username="cacheuser1", password="pass12345")
        user_two = user_model.objects.create_user(username="cacheuser2", password="pass12345")

        self.client.login(username="cacheuser1", password="pass12345")
        first = self.client.get("/dashboard/token-health-status/")
        self.client.logout()

        ConnectedAccount.objects.create(
            platform="facebook",
            page_id="fb-cache",
            page_name="Cache FB",
            access_token="cache-token",
        )
        mock_debug_token.return_value = {"data": {"is_valid": True}}

        self.client.login(username="cacheuser2", password="pass12345")
        second = self.client.get("/dashboard/token-health-status/")

        self.assertFalse(first.json()["ok"])
        self.assertTrue(second.json()["ok"])

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
    def test_token_health_status_stays_green_when_connected_accounts_exist_but_some_rows_are_stale(self, mock_debug_token):
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
        self.assertTrue(body["ok"])
        self.assertEqual(body["level"], "ok")

    def test_meta_app_config_updates_env_and_runtime_settings(self):
        user_model = get_user_model()
        user_model.objects.create_superuser(username="metaadmin", email="metaadmin@example.com", password="pass12345")
        self.client.login(username="metaadmin", password="pass12345")

        with TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "META_APP_ID=old-app-id",
                        "META_APP_SECRET=old-secret",
                        "META_REDIRECT_URI=https://old.example.com/auth/meta/callback",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with self.settings(
                BASE_DIR=Path(tmp_dir),
                META_APP_ID="old-app-id",
                META_APP_SECRET="old-secret",
                META_REDIRECT_URI="https://old.example.com/auth/meta/callback",
            ):
                response = self.client.post(
                    "/dashboard/meta-app-config/",
                    data=json.dumps(
                        {
                            "meta_app_id": "new-app-id",
                            "meta_app_secret": "new-secret",
                            "meta_redirect_uri": "https://new.example.com/auth/meta/callback",
                        }
                    ),
                    content_type="application/json",
                )

                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertTrue(body["ok"])
                self.assertEqual(body["meta_app_id"], "new-app-id")
                self.assertEqual(body["meta_redirect_uri"], "https://new.example.com/auth/meta/callback")
                self.assertTrue(body["meta_app_secret_configured"])
                self.assertTrue(str(body["meta_app_secret_masked"]).endswith("cret"))

                file_content = env_path.read_text(encoding="utf-8")
                self.assertIn("META_APP_ID=new-app-id", file_content)
                self.assertIn("META_APP_SECRET=new-secret", file_content)
                self.assertIn("META_REDIRECT_URI=https://new.example.com/auth/meta/callback", file_content)

                self.assertEqual(settings.META_APP_ID, "new-app-id")
                self.assertEqual(settings.META_APP_SECRET, "new-secret")
                self.assertEqual(settings.META_REDIRECT_URI, "https://new.example.com/auth/meta/callback")

    def test_meta_app_config_keeps_existing_secret_when_field_blank(self):
        user_model = get_user_model()
        user_model.objects.create_superuser(username="metaadmin2", email="metaadmin2@example.com", password="pass12345")
        self.client.login(username="metaadmin2", password="pass12345")

        with TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "META_APP_ID=old-app-id",
                        "META_APP_SECRET=old-secret",
                        "META_REDIRECT_URI=https://old.example.com/auth/meta/callback",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with self.settings(
                BASE_DIR=Path(tmp_dir),
                META_APP_ID="old-app-id",
                META_APP_SECRET="old-secret",
                META_REDIRECT_URI="https://old.example.com/auth/meta/callback",
            ):
                response = self.client.post(
                    "/dashboard/meta-app-config/",
                    data=json.dumps(
                        {
                            "meta_app_id": "old-app-id",
                            "meta_app_secret": "",
                            "meta_redirect_uri": "https://old.example.com/auth/meta/callback",
                        }
                    ),
                    content_type="application/json",
                )

                self.assertEqual(response.status_code, 200)
                file_content = env_path.read_text(encoding="utf-8")
                self.assertIn("META_APP_SECRET=old-secret", file_content)

    def test_meta_app_config_rejects_invalid_redirect_uri(self):
        user_model = get_user_model()
        user_model.objects.create_superuser(username="metaadmin3", email="metaadmin3@example.com", password="pass12345")
        self.client.login(username="metaadmin3", password="pass12345")

        response = self.client.post(
            "/dashboard/meta-app-config/",
            data=json.dumps(
                {
                    "meta_app_id": "abc",
                    "meta_app_secret": "def",
                    "meta_redirect_uri": "not-a-url",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["error"], "Validation failed.")

    def test_profile_page_requires_login(self):
        response = self.client.get("/dashboard/profile/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_profile_data_get_returns_user_profile_payload(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="profileadmin",
            email="profile@example.com",
            first_name="Priya",
            last_name="Sharma",
            password="pass12345",
        )
        UserProfile.objects.create(
            user=user,
            first_name="Priya",
            last_name="Sharma",
            profile_picture_url="https://example.com/avatar.jpg",
            subscription_plan="Pro",
            subscription_status=UserProfile.SUBSCRIPTION_STATUS_ACTIVE,
        )
        self.client.login(username="profileadmin", password="pass12345")

        response = self.client.get("/dashboard/profile-data/")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["email"], "profile@example.com")
        self.assertEqual(body["first_name"], "Priya")
        self.assertEqual(body["last_name"], "Sharma")
        self.assertEqual(body["profile_picture_url"], "https://example.com/avatar.jpg")
        self.assertEqual(body["subscription_plan"], "Pro")
        self.assertEqual(body["subscription_status"], "active")

    def test_profile_data_post_updates_only_first_and_last_name(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="profileeditor",
            email="locked@example.com",
            first_name="Old",
            last_name="Name",
            password="pass12345",
        )
        UserProfile.objects.create(user=user)
        self.client.login(username="profileeditor", password="pass12345")

        response = self.client.post(
            "/dashboard/profile-data/",
            data=json.dumps(
                {
                    "first_name": "New",
                    "last_name": "Person",
                    "email": "hacker@example.com",
                    "profile_picture_url": "https://example.com/new-avatar.jpg",
                    "subscription_plan": "Growth",
                    "subscription_status": "expired",
                    "subscription_expires_on": "2026-12-31",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        profile = user.profile
        self.assertEqual(user.email, "locked@example.com")
        self.assertEqual(user.first_name, "New")
        self.assertEqual(user.last_name, "Person")
        self.assertEqual(profile.profile_picture_url, "")
        self.assertEqual(profile.subscription_plan, "Trial")
        self.assertEqual(profile.subscription_status, "active")

    def test_profile_data_post_ignores_non_name_fields_in_payload(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="profileinvalid",
            email="invalid@example.com",
            first_name="Old",
            last_name="Name",
            password="pass12345",
        )
        UserProfile.objects.create(
            user=user,
            first_name="Old",
            last_name="Name",
            profile_picture_url="https://example.com/old-avatar.jpg",
            subscription_plan="Pro",
            subscription_status=UserProfile.SUBSCRIPTION_STATUS_ACTIVE,
            subscription_expires_on=timezone.now().date() + timedelta(days=10),
        )
        self.client.login(username="profileinvalid", password="pass12345")

        response = self.client.post(
            "/dashboard/profile-data/",
            data=json.dumps(
                {
                    "first_name": "Test",
                    "last_name": "User",
                    "profile_picture_url": "https://example.com/new-avatar.jpg",
                    "subscription_plan": "Trial",
                    "subscription_status": "paused",
                    "subscription_expires_on": "2026-12-31",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        profile = user.profile
        self.assertEqual(user.first_name, "Test")
        self.assertEqual(user.last_name, "User")
        self.assertEqual(profile.profile_picture_url, "https://example.com/old-avatar.jpg")
        self.assertEqual(profile.subscription_plan, "Pro")
        self.assertEqual(profile.subscription_status, UserProfile.SUBSCRIPTION_STATUS_ACTIVE)

    def test_subscription_page_requires_login(self):
        response = self.client.get("/dashboard/subscription/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_subscription_create_order_returns_error_when_razorpay_missing(self):
        user_model = get_user_model()
        user_model.objects.create_user(username="subs1", password="pass12345")
        self.client.login(username="subs1", password="pass12345")

        with self.settings(RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET=""):
            response = self.client.post(
                "/dashboard/subscription/create-order/",
                data=json.dumps({"plan": "monthly"}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Razorpay is not configured", response.json()["error"])

    @patch("dashboard.views.requests.post")
    def test_subscription_create_order_success(self, mock_post):
        user_model = get_user_model()
        user_model.objects.create_user(
            username="subs2",
            email="subs2@example.com",
            first_name="Sub",
            last_name="User",
            password="pass12345",
        )
        self.client.login(username="subs2", password="pass12345")

        class _Resp:
            status_code = 200

            @staticmethod
            def json():
                return {"id": "order_test_123", "amount": 600000, "currency": "INR"}

        mock_post.return_value = _Resp()

        with self.settings(RAZORPAY_KEY_ID="rzp_test_abc", RAZORPAY_KEY_SECRET="secret_xyz", RAZORPAY_CURRENCY="INR"):
            response = self.client.post(
                "/dashboard/subscription/create-order/",
                data=json.dumps({"plan": "monthly"}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["order_id"], "order_test_123")
        self.assertEqual(body["plan"], "monthly")
        self.assertEqual(body["razorpay_key_id"], "rzp_test_abc")
        self.assertEqual(cache.get("subscription_order:order_test_123")["billing_cycle"], "monthly")

    def test_subscription_verify_payment_success(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="subs3", password="pass12345")
        self.client.login(username="subs3", password="pass12345")

        order_id = "order_test_999"
        payment_id = "pay_test_888"
        secret = "secret_xyz"
        signature = hmac.new(secret.encode("utf-8"), f"{order_id}|{payment_id}".encode("utf-8"), hashlib.sha256).hexdigest()
        cache.set(
            f"subscription_order:{order_id}",
            {"user_id": user.id, "plan_key": "monthly", "billing_cycle": "monthly"},
            timeout=3600,
        )

        with self.settings(RAZORPAY_KEY_ID="rzp_test_abc", RAZORPAY_KEY_SECRET=secret):
            response = self.client.post(
                "/dashboard/subscription/verify-payment/",
                data=json.dumps(
                    {
                        "razorpay_order_id": order_id,
                        "razorpay_payment_id": payment_id,
                        "razorpay_signature": signature,
                    }
                ),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        user.refresh_from_db()
        self.assertEqual(user.profile.subscription_plan, "Monthly")
        self.assertEqual(user.profile.subscription_status, "active")

    def test_subscription_verify_payment_rejects_bad_signature(self):
        user_model = get_user_model()
        user_model.objects.create_user(username="subs4", password="pass12345")
        self.client.login(username="subs4", password="pass12345")

        with self.settings(RAZORPAY_KEY_ID="rzp_test_abc", RAZORPAY_KEY_SECRET="secret_xyz"):
            response = self.client.post(
                "/dashboard/subscription/verify-payment/",
                data=json.dumps(
                    {
                        "razorpay_order_id": "order_test",
                        "razorpay_payment_id": "pay_test",
                        "razorpay_signature": "invalid_signature",
                    }
                ),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("verification failed", response.json()["error"].lower())

    def test_subscription_verify_payment_can_activate_yearly_plan(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="subs5", password="pass12345")
        self.client.login(username="subs5", password="pass12345")

        order_id = "order_test_yearly"
        payment_id = "pay_test_yearly"
        secret = "secret_xyz"
        signature = hmac.new(secret.encode("utf-8"), f"{order_id}|{payment_id}".encode("utf-8"), hashlib.sha256).hexdigest()
        cache.set(
            f"subscription_order:{order_id}",
            {"user_id": user.id, "plan_key": "yearly", "billing_cycle": "yearly"},
            timeout=3600,
        )

        with self.settings(RAZORPAY_KEY_ID="rzp_test_abc", RAZORPAY_KEY_SECRET=secret):
            response = self.client.post(
                "/dashboard/subscription/verify-payment/",
                data=json.dumps(
                    {
                        "razorpay_order_id": order_id,
                        "razorpay_payment_id": payment_id,
                        "razorpay_signature": signature,
                    }
                ),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.profile.subscription_plan, "Yearly")

    def test_expired_user_is_redirected_to_subscription_expired_page(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="expired1", password="pass12345")
        profile = UserProfile.objects.create(
            user=user,
            subscription_plan="Trial",
            subscription_status=UserProfile.SUBSCRIPTION_STATUS_ACTIVE,
            subscription_expires_on=timezone.now().date() - timedelta(days=1),
        )
        self.client.login(username="expired1", password="pass12345")

        response = self.client.get("/dashboard/accounts/")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard/subscription/expired/", response.url)
        profile.refresh_from_db()
        self.assertEqual(profile.subscription_status, UserProfile.SUBSCRIPTION_STATUS_EXPIRED)

    def test_expired_user_api_request_returns_payment_required(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="expired2", password="pass12345")
        UserProfile.objects.create(
            user=user,
            subscription_plan="Trial",
            subscription_status=UserProfile.SUBSCRIPTION_STATUS_ACTIVE,
            subscription_expires_on=timezone.now().date() - timedelta(days=1),
        )
        self.client.login(username="expired2", password="pass12345")

        response = self.client.get("/api/accounts/")

        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.json()["code"], "subscription_expired")

    def test_expired_user_can_open_subscription_page(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="expired3", password="pass12345")
        UserProfile.objects.create(
            user=user,
            subscription_plan="Monthly",
            subscription_status=UserProfile.SUBSCRIPTION_STATUS_ACTIVE,
            subscription_expires_on=timezone.now().date() - timedelta(days=1),
        )
        self.client.login(username="expired3", password="pass12345")

        response = self.client.get("/dashboard/subscription/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your app access has expired")

    def test_profile_payload_normalizes_legacy_starter_plan_to_trial(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="legacyplan", password="pass12345")
        UserProfile.objects.create(
            user=user,
            subscription_plan="Starter",
            subscription_status=UserProfile.SUBSCRIPTION_STATUS_ACTIVE,
            subscription_expires_on=timezone.now().date() + timedelta(days=1),
        )
        self.client.login(username="legacyplan", password="pass12345")

        response = self.client.get("/dashboard/profile-data/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["subscription_plan"], "Trial")
