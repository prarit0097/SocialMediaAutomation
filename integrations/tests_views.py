from django.contrib.auth import get_user_model
from django.test import Client, TestCase


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
