from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.test import Client, TestCase

from analytics.views import _extract_error_message
from core.constants import FACEBOOK, INSTAGRAM
from core.exceptions import MetaPermanentError
from integrations.models import ConnectedAccount


class AnalyticsApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="admin", password="pass12345")
        self.client.login(username="admin", password="pass12345")
        self.account = ConnectedAccount.objects.create(
            platform=FACEBOOK,
            page_id="111",
            page_name="Page",
            access_token="token",
        )

    @patch("analytics.services.MetaClient.fetch_facebook_published_posts_count")
    @patch("analytics.services.MetaClient.fetch_facebook_insights")
    def test_fetch_insights(self, mock_fetch, mock_posts_count):
        mock_fetch.return_value = [{"name": "page_impressions", "values": []}]
        mock_posts_count.return_value = 0
        response = self.client.get(f"/api/insights/{self.account.id}/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["platform"], FACEBOOK)
        self.assertIn("insights", body)
        self.assertIn("summary", body)
        self.assertIn("published_posts", body)

    @patch("analytics.services.MetaClient.fetch_facebook_published_posts_count")
    @patch("analytics.services.MetaClient.fetch_facebook_insights")
    def test_fetch_insights_meta_error_returns_json(self, mock_fetch, mock_posts_count):
        mock_fetch.side_effect = MetaPermanentError("invalid metric", status_code=400, payload={"error": {}})
        mock_posts_count.return_value = 0
        response = self.client.get(f"/api/insights/{self.account.id}/")
        self.assertEqual(response.status_code, 502)
        body = response.json()
        self.assertEqual(body["error"], "Failed to fetch insights from Meta")

    @patch("analytics.services._get_published_posts")
    @patch("analytics.services.MetaClient.fetch_facebook_published_posts_count")
    @patch("analytics.services.MetaClient.fetch_instagram_insights")
    @patch("analytics.services.MetaClient.fetch_facebook_insights")
    def test_fetch_insights_combines_linked_facebook_and_instagram(
        self,
        mock_fetch_fb,
        mock_fetch_ig,
        mock_posts_count,
        mock_published_posts,
    ):
        self.account.ig_user_id = "178400001"
        self.account.save(update_fields=["ig_user_id"])
        ig_account = ConnectedAccount.objects.create(
            platform=INSTAGRAM,
            page_id="178400001",
            page_name="Page (IG)",
            ig_user_id="178400001",
            access_token="token",
        )

        mock_fetch_fb.return_value = [{"name": "followers_count", "values": [{"value": 100}]}]
        mock_fetch_ig.return_value = [{"name": "follower_count", "values": [{"value": 50}]}]
        mock_posts_count.return_value = 12
        mock_published_posts.side_effect = [
            [{"id": "fb_post_1", "message": "fb", "media_url": None, "published_at": None, "scheduled_for": None}],
            [{"id": "ig_post_1", "message": "ig", "media_url": None, "published_at": None, "scheduled_for": None}],
        ]

        response = self.client.get(f"/api/insights/{self.account.id}/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body.get("combined"))
        self.assertEqual(body.get("platform"), "facebook+instagram")
        self.assertEqual(len(body.get("accounts", [])), 2)
        self.assertEqual(body["summary"]["facebook"]["total_followers"], 100)
        self.assertEqual(body["summary"]["instagram"]["total_followers"], 50)
        platforms = {row["platform"] for row in body.get("published_posts", [])}
        self.assertIn("facebook", platforms)
        self.assertIn("instagram", platforms)

    @patch("analytics.services._get_published_posts")
    @patch("analytics.services.MetaClient.fetch_facebook_published_posts_count")
    @patch("analytics.services.MetaClient.fetch_instagram_insights")
    @patch("analytics.services.MetaClient.fetch_facebook_insights")
    def test_instagram_summary_prefers_profile_level_followers_over_day_follower_count(
        self,
        mock_fetch_fb,
        mock_fetch_ig,
        mock_posts_count,
        mock_published_posts,
    ):
        self.account.ig_user_id = "178400001"
        self.account.save(update_fields=["ig_user_id"])
        ConnectedAccount.objects.create(
            platform=INSTAGRAM,
            page_id="178400001",
            page_name="Page (IG)",
            ig_user_id="178400001",
            access_token="token",
        )

        mock_fetch_fb.return_value = [{"name": "followers_count", "values": [{"value": 100}]}]
        mock_fetch_ig.return_value = [
            {"name": "follower_count", "values": [{"value": 0}]},
            {"name": "followers_count", "values": [{"value": 321}]},
        ]
        mock_posts_count.return_value = 5
        mock_published_posts.side_effect = [[], []]

        response = self.client.get(f"/api/insights/{self.account.id}/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["summary"]["instagram"]["total_followers"], 321)

    def test_extract_error_message_sanitizes_html_payloads(self):
        response = JsonResponse(
            {
                "error": "Failed to fetch insights from Meta",
                "details": "<!DOCTYPE html><html><body>ngrok gateway error ERR_NGROK_3004</body></html>",
            },
            status=502,
        )

        message = _extract_error_message(response, "Linked instagram insights unavailable.")
        self.assertEqual(
            message,
            "Public media URL is unavailable through ngrok right now. Restart ngrok and refresh again.",
        )
