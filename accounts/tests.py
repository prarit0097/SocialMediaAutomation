from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.core.cache import cache
from unittest.mock import patch, Mock


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

    def test_root_redirects_to_dashboard_for_authenticated_user(self):
        user_model = get_user_model()
        user_model.objects.create_user(username="landingadmin", password="pass12345")
        self.client.login(username="landingadmin", password="pass12345")

        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard/", response.url)

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
        self.assertContains(response, "1995praritsidana@gmail.com")

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
        self.assertTrue(get_user_model().objects.filter(email="newoperator@gmail.com").exists())

    def test_google_signup_start_redirects_to_google_auth(self):
        with self.settings(
            GOOGLE_OAUTH_CLIENT_ID="google-client-id",
            GOOGLE_OAUTH_CLIENT_SECRET="google-client-secret",
            GOOGLE_OAUTH_REDIRECT_URI="http://testserver/signup/google/callback/",
        ):
            response = self.client.get("/signup/google/start/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("accounts.google.com/o/oauth2/v2/auth", response.url)
