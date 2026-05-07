from datetime import timedelta
from django.contrib.auth import get_user_model
from django.conf import settings
from django.test import TestCase, override_settings
from django.core.cache import cache
from django.utils import timezone
from unittest.mock import patch, Mock

from .models import UserProfile


@override_settings(
    SECURE_SSL_REDIRECT=False,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "accounts-tests",
        }
    },
)
class AccountsLandingTests(TestCase):
    def test_root_shows_landing_for_anonymous_user(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Login")
        self.assertContains(response, "Signup")
        self.assertContains(response, "What You Get")
        self.assertContains(response, "1703024424409702")
        self.assertContains(response, "G-SCZT2H87S0")
        self.assertContains(response, "googletagmanager.com/gtag/js")
        self.assertContains(response, 'data-pixel-event="Lead"')

    def test_queued_pixel_events_render_once(self):
        session = self.client.session
        session["pixel_events"] = [{"name": "CompleteRegistration", "payload": {"method": "google"}, "custom": False}]
        session.save()

        response = self.client.get("/login/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "queuedPixelEvents")
        self.assertContains(response, "CompleteRegistration")
        self.assertContains(response, 'window.gtag("event", event.name, payload)')
        self.assertEqual(self.client.session.get("pixel_events"), None)

    def test_root_redirects_to_dashboard_for_authenticated_user(self):
        user_model = get_user_model()
        user_model.objects.create_user(username="landingadmin", password="pass12345")
        self.client.login(username="landingadmin", password="pass12345")

        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard/", response.url)

    def test_root_redirects_expired_user_to_subscription_page(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="expiredroot", password="pass12345")
        UserProfile.objects.create(
            user=user,
            subscription_plan=UserProfile.SUBSCRIPTION_PLAN_TRIAL,
            subscription_status=UserProfile.SUBSCRIPTION_STATUS_ACTIVE,
            subscription_expires_on=timezone.now().date() - timedelta(days=1),
        )
        self.client.login(username="expiredroot", password="pass12345")

        response = self.client.get("/")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard/subscription/", response.url)

    def test_signup_page_loads(self):
        response = self.client.get("/signup/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sign up with Google")

    def test_login_page_shows_google_button_when_configured(self):
        with self.settings(
            GOOGLE_OAUTH_CLIENT_ID="google-client-id",
            GOOGLE_OAUTH_CLIENT_SECRET="google-client-secret",
            GOOGLE_OAUTH_REDIRECT_URI="http://testserver/signup/google/callback/",
        ):
            response = self.client.get("/login/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Continue with Google")

    def test_privacy_policy_page_loads(self):
        response = self.client.get("/privacy-policy/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Privacy Policy")
        self.assertContains(response, "1995postzyo@gmail.com")

    def test_terms_page_loads(self):
        response = self.client.get("/terms/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Terms of Service")

    def test_data_deletion_page_loads(self):
        response = self.client.get("/data-deletion/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "User Data Deletion")
        self.assertContains(response, "Postzyo Data Deletion Request")

    @patch("accounts.views.requests.get")
    @patch("accounts.views.requests.post")
    def test_google_signup_callback_creates_user_and_logs_in(self, mock_post, mock_get):
        state = "teststate123"
        session = self.client.session
        session["google_oauth_state"] = state
        session.save()
        cache.set(f"google_oauth_state:{state}", {"issued": True}, timeout=600)

        token_response = Mock(status_code=200)
        token_response.content = b"{}"
        token_response.json.return_value = {"access_token": "token123"}
        mock_post.return_value = token_response

        profile_response = Mock(status_code=200)
        profile_response.content = b"{}"
        profile_response.json.return_value = {
            "email": "newoperator@gmail.com",
            "email_verified": True,
            "given_name": "New",
            "family_name": "Operator",
        }
        mock_get.return_value = profile_response

        with self.settings(
            GOOGLE_OAUTH_CLIENT_ID="google-client-id",
            GOOGLE_OAUTH_CLIENT_SECRET="google-client-secret",
            GOOGLE_OAUTH_REDIRECT_URI="http://testserver/signup/google/callback/",
        ):
            response = self.client.get("/signup/google/callback/", {"code": "abc", "state": state})

        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard/", response.url)
        user = get_user_model().objects.get(email="newoperator@gmail.com")
        self.assertEqual(user.profile.subscription_plan, UserProfile.SUBSCRIPTION_PLAN_TRIAL)
        self.assertEqual(user.profile.subscription_status, UserProfile.SUBSCRIPTION_STATUS_ACTIVE)
        self.assertEqual(self.client.session["pixel_events"][0]["name"], "CompleteRegistration")

    @patch("accounts.views.requests.get")
    @patch("accounts.views.requests.post")
    def test_google_signup_callback_sets_persistent_session_cookie(self, mock_post, mock_get):
        state = "persiststate123"
        session = self.client.session
        session["google_oauth_state"] = state
        session.save()
        cache.set(f"google_oauth_state:{state}", {"issued": True}, timeout=600)

        token_response = Mock(status_code=200)
        token_response.content = b"{}"
        token_response.json.return_value = {"access_token": "token123"}
        mock_post.return_value = token_response

        profile_response = Mock(status_code=200)
        profile_response.content = b"{}"
        profile_response.json.return_value = {
            "email": "persistoperator@gmail.com",
            "email_verified": True,
            "given_name": "Persist",
            "family_name": "Operator",
        }
        mock_get.return_value = profile_response

        with self.settings(
            GOOGLE_OAUTH_CLIENT_ID="google-client-id",
            GOOGLE_OAUTH_CLIENT_SECRET="google-client-secret",
            GOOGLE_OAUTH_REDIRECT_URI="http://testserver/signup/google/callback/",
            SESSION_COOKIE_AGE=86400,
            SESSION_EXPIRE_AT_BROWSER_CLOSE=False,
        ):
            response = self.client.get("/signup/google/callback/", {"code": "abc", "state": state})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get_expiry_age(), 86400)
        self.assertEqual(int(response.cookies[settings.SESSION_COOKIE_NAME]["max-age"]), 86400)
        self.assertTrue(response.cookies[settings.SESSION_COOKIE_NAME]["expires"])

    @patch("accounts.views.requests.get")
    @patch("accounts.views.requests.post")
    def test_existing_expired_google_login_redirects_to_subscription_page(self, mock_post, mock_get):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="expiredgoogle@gmail.com",
            email="expiredgoogle@gmail.com",
            password="pass12345",
        )
        UserProfile.objects.create(
            user=user,
            subscription_plan=UserProfile.SUBSCRIPTION_PLAN_TRIAL,
            subscription_status=UserProfile.SUBSCRIPTION_STATUS_ACTIVE,
            subscription_expires_on=timezone.now().date() - timedelta(days=1),
        )
        state = "expiredgooglestate"
        session = self.client.session
        session["google_oauth_state"] = state
        session.save()
        cache.set(f"google_oauth_state:{state}", {"issued": True}, timeout=600)

        token_response = Mock(status_code=200)
        token_response.content = b"{}"
        token_response.json.return_value = {"access_token": "token123"}
        mock_post.return_value = token_response

        profile_response = Mock(status_code=200)
        profile_response.content = b"{}"
        profile_response.json.return_value = {
            "email": "expiredgoogle@gmail.com",
            "email_verified": True,
            "given_name": "Expired",
            "family_name": "Google",
        }
        mock_get.return_value = profile_response

        with self.settings(
            GOOGLE_OAUTH_CLIENT_ID="google-client-id",
            GOOGLE_OAUTH_CLIENT_SECRET="google-client-secret",
            GOOGLE_OAUTH_REDIRECT_URI="http://testserver/signup/google/callback/",
        ):
            response = self.client.get("/signup/google/callback/", {"code": "abc", "state": state})

        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard/subscription/", response.url)
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.subscription_status, UserProfile.SUBSCRIPTION_STATUS_EXPIRED)

    def test_password_login_sets_persistent_session_cookie(self):
        user_model = get_user_model()
        user_model.objects.create_user(username="persistadmin", password="pass12345")

        with self.settings(SESSION_COOKIE_AGE=86400, SESSION_EXPIRE_AT_BROWSER_CLOSE=False):
            response = self.client.post("/login/", {"username": "persistadmin", "password": "pass12345"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get_expiry_age(), 86400)
        self.assertEqual(int(response.cookies[settings.SESSION_COOKIE_NAME]["max-age"]), 86400)
        self.assertTrue(response.cookies[settings.SESSION_COOKIE_NAME]["expires"])
        self.assertEqual(self.client.session["pixel_events"][0]["name"], "Login")

    def test_expired_password_login_redirects_to_subscription_page(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="expiredlogin", password="pass12345")
        UserProfile.objects.create(
            user=user,
            subscription_plan=UserProfile.SUBSCRIPTION_PLAN_TRIAL,
            subscription_status=UserProfile.SUBSCRIPTION_STATUS_ACTIVE,
            subscription_expires_on=timezone.now().date() - timedelta(days=1),
        )

        response = self.client.post("/login/", {"username": "expiredlogin", "password": "pass12345"})

        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard/subscription/", response.url)

    def test_google_signup_start_redirects_to_google_auth(self):
        with self.settings(
            GOOGLE_OAUTH_CLIENT_ID="google-client-id",
            GOOGLE_OAUTH_CLIENT_SECRET="google-client-secret",
            GOOGLE_OAUTH_REDIRECT_URI="http://testserver/signup/google/callback/",
        ):
            response = self.client.get("/signup/google/start/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("accounts.google.com/o/oauth2/v2/auth", response.url)
