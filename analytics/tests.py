from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.test import Client, TestCase

from analytics.views import _build_combined_response, _extract_error_message
from analytics.services import build_insight_response
from core.constants import FACEBOOK, INSTAGRAM
from core.exceptions import MetaPermanentError
from core.services.meta_client import MetaClient
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

    @patch("analytics.views.fetch_and_store_insights")
    def test_force_refresh_fetches_stats_for_visible_posts(self, mock_fetch_and_store):
        mock_fetch_and_store.return_value = {
            "account_id": self.account.id,
            "page_id": self.account.page_id,
            "page_name": self.account.page_name,
            "platform": FACEBOOK,
            "insights": [],
            "summary": {"total_followers": 0, "total_following": 0, "total_post_share": 0},
            "published_posts": [],
            "snapshot_id": 1,
            "fetched_at": "2026-03-12T12:00:00+00:00",
            "cached": False,
        }

        response = self.client.get(f"/api/insights/{self.account.id}/?refresh=1")
        self.assertEqual(response.status_code, 200)
        mock_fetch_and_store.assert_called_once_with(
            self.account,
            include_post_stats=True,
            post_limit=20,
            post_stats_limit=20,
        )

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

    def test_facebook_total_following_does_not_reuse_fan_count(self):
        data = build_insight_response(
            account=self.account,
            platform=FACEBOOK,
            insights=[
                {"name": "followers_count", "values": [{"value": 146234}]},
                {"name": "fan_count", "values": [{"value": 146234}]},
            ],
            snapshot_id=1,
            fetched_at=None,
            cached=False,
            published_posts=[],
        )

        self.assertEqual(data["summary"]["total_followers"], 146234)
        self.assertEqual(data["summary"]["total_following"], 0)

    def test_combined_published_posts_are_sorted_by_latest_published_at_first(self):
        combined = _build_combined_response(
            {
                "platform": "facebook",
                "account_id": 42,
                "page_id": "fb-page",
                "page_name": "FB Page",
                "published_posts": [
                    {"id": "fb-older", "published_at": "2026-03-12T10:53:26+00:00", "scheduled_for": None},
                ],
                "summary": {"total_followers": 1, "total_following": 0, "total_post_share": 1},
                "insights": [],
                "snapshot_id": 1,
                "fetched_at": "2026-03-12T11:00:00+00:00",
                "cached": False,
            },
            {
                "platform": "instagram",
                "account_id": 74,
                "page_id": "ig-page",
                "page_name": "IG Page",
                "published_posts": [
                    {"id": "ig-newer", "published_at": "2026-03-12T10:54:26+00:00", "scheduled_for": None},
                ],
                "summary": {"total_followers": 1, "total_following": 1, "total_post_share": 1},
                "insights": [],
                "snapshot_id": 2,
                "fetched_at": "2026-03-12T11:01:00+00:00",
                "cached": False,
            },
        )

        self.assertEqual([row["id"] for row in combined["published_posts"]], ["ig-newer", "fb-older"])


class MetaClientTests(TestCase):
    @patch.object(MetaClient, "_get_by_url")
    @patch.object(MetaClient, "_get")
    def test_fetch_facebook_published_posts_count_counts_all_pages(self, mock_get, mock_get_by_url):
        mock_get.return_value = {
            "data": [{"id": "1"}, {"id": "2"}],
            "paging": {"next": "https://graph.facebook.com/next-page"},
        }
        mock_get_by_url.return_value = {
            "data": [{"id": "3"}, {"id": "4"}, {"id": "5"}],
            "paging": {},
        }

        count = MetaClient().fetch_facebook_published_posts_count("page-1", "token")

        self.assertEqual(count, 5)
