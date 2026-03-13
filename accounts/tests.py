from django.contrib.auth import get_user_model
from django.test import TestCase


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
        self.assertContains(response, "Create Account")

    def test_signup_creates_user_and_logs_in(self):
        response = self.client.post(
            "/signup/",
            data={
                "username": "newoperator",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard/", response.url)
        self.assertTrue(get_user_model().objects.filter(username="newoperator").exists())
