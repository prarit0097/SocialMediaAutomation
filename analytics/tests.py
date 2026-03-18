import json
from datetime import timedelta
from unittest.mock import patch
import requests

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import OperationalError
from django.http import JsonResponse
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from analytics.models import BulkInsightRefreshRun, InsightSnapshot
from analytics.ai_service import AIInsightsError
from analytics.services import _aggregate_recent_post_metric, build_comparison_rows, build_insight_response, build_post_stats_summary
from analytics.tasks import DAILY_HEAVY_COLLECTION_MODE, queue_daily_heavy_insight_refresh, refresh_account_insights_snapshot
from analytics.views import _build_combined_response, _extract_error_message
from core.constants import FACEBOOK, INSTAGRAM
from core.exceptions import MetaPermanentError, MetaTransientError
from core.services.meta_client import MetaClient
from integrations.models import ConnectedAccount


class AnalyticsApiTests(TestCase):
    def setUp(self):
        cache.clear()
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
        self.assertIn("posting_strategy_assist", body)
        self.assertIn("low_distribution_alerts", body)
        self.assertIn("early_engagement_monitor", body)

    @patch("analytics.services.MetaClient.fetch_facebook_published_posts_count")
    @patch("analytics.services.MetaClient.fetch_facebook_insights")
    def test_scheduler_assist_endpoint_returns_profile_wise_strategy(self, mock_fetch, mock_posts_count):
        mock_fetch.return_value = [{"name": "page_impressions", "values": []}]
        mock_posts_count.return_value = 0

        InsightSnapshot.objects.create(
            account=self.account,
            platform=FACEBOOK,
            payload={
                "insights": [],
                "published_posts": [
                    {
                        "id": "p1",
                        "message": "Short caption",
                        "published_at": (timezone.now() - timedelta(days=1)).isoformat(),
                        "total_views": 100,
                        "total_likes": 10,
                        "total_comments": 2,
                        "total_shares": 1,
                        "total_saves": 0,
                    },
                    {
                        "id": "p2",
                        "message": "Long caption " * 20,
                        "published_at": (timezone.now() - timedelta(days=2)).isoformat(),
                        "total_views": 40,
                        "total_likes": 3,
                        "total_comments": 0,
                        "total_shares": 0,
                        "total_saves": 0,
                    },
                ],
                "metadata": {},
            },
        )

        response = self.client.get(f"/api/insights/scheduler-assist/{self.account.id}/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["account_id"], self.account.id)
        self.assertIn("platforms", body)
        self.assertIn("facebook", body["platforms"])
        self.assertIn("best_time_slots", body["platforms"]["facebook"])

    @patch("analytics.services.MetaClient.fetch_facebook_published_posts_count")
    @patch("analytics.services.MetaClient.fetch_facebook_insights")
    def test_fetch_insights_meta_error_returns_json(self, mock_fetch, mock_posts_count):
        mock_fetch.side_effect = MetaPermanentError("invalid metric", status_code=400, payload={"error": {}})
        mock_posts_count.return_value = 0
        response = self.client.get(f"/api/insights/{self.account.id}/")
        self.assertEqual(response.status_code, 502)
        body = response.json()
        self.assertEqual(body["error"], "Failed to fetch insights from Meta")

    @patch("analytics.views.refresh_account_insights_snapshot.apply_async")
    def test_force_refresh_all_accounts_insights_queues_active_profiles(self, mock_apply_async):
        ConnectedAccount.objects.create(
            platform=FACEBOOK,
            page_id="active-no-token",
            page_name="No Token",
            access_token="",
            is_active=True,
        )
        ConnectedAccount.objects.create(
            platform=FACEBOOK,
            page_id="inactive-token",
            page_name="Inactive Token",
            access_token="token-2",
            is_active=False,
        )

        response = self.client.post(
            "/api/insights/force-refresh-all/",
            data=json.dumps({}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["total_accounts"], 2)
        self.assertEqual(body["queued_count"], 1)
        self.assertEqual(body["skipped_no_token"], 1)
        self.assertEqual(body["enqueue_failed"], 0)
        self.assertTrue(body["has_active_run"])
        self.assertGreater(body["run_id"], 0)
        mock_apply_async.assert_called_once_with(
            args=[self.account.id],
            kwargs={"force": True, "bulk_run_id": body["run_id"]},
            priority=1,
        )

    @patch("analytics.views.refresh_account_insights_snapshot.apply_async")
    def test_force_refresh_all_accounts_insights_blocks_duplicate_running_job(self, mock_apply_async):
        response1 = self.client.post(
            "/api/insights/force-refresh-all/",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response1.status_code, 200)
        self.assertEqual(mock_apply_async.call_count, 1)

        response2 = self.client.post(
            "/api/insights/force-refresh-all/",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response2.status_code, 409)
        body = response2.json()
        self.assertEqual(body["error"], "Force refresh already running")
        self.assertTrue(body["has_active_run"])
        self.assertEqual(mock_apply_async.call_count, 1)

    @patch("analytics.views.refresh_account_insights_snapshot.apply_async")
    def test_force_refresh_all_accounts_status_endpoint_returns_current_run(self, mock_apply_async):
        self.client.post(
            "/api/insights/force-refresh-all/",
            data=json.dumps({}),
            content_type="application/json",
        )
        response = self.client.get("/api/insights/force-refresh-all/status/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["has_active_run"])
        self.assertEqual(body["status"], "running")

    @patch("analytics.views._reconcile_bulk_run_progress", side_effect=OperationalError("database is locked"))
    def test_force_refresh_all_accounts_status_handles_database_lock(self, _mock_reconcile):
        run = BulkInsightRefreshRun.objects.create(
            user=self.user,
            status=BulkInsightRefreshRun.STATUS_RUNNING,
            total_accounts=2,
            queued_count=2,
        )
        response = self.client.get("/api/insights/force-refresh-all/status/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["run_id"], run.id)
        self.assertEqual(body["status"], "running")
        self.assertTrue(body.get("db_lock_contention"))

    @override_settings(BULK_REFRESH_STALE_MINUTES=10)
    def test_force_refresh_status_reconciles_stuck_run_from_snapshots(self):
        run = BulkInsightRefreshRun.objects.create(
            user=self.user,
            status=BulkInsightRefreshRun.STATUS_RUNNING,
            total_accounts=1,
            queued_count=1,
            completed_count=0,
            failed_count=0,
            skipped_no_token=0,
            enqueue_failed=0,
        )
        stale_started = timezone.now() - timedelta(minutes=20)
        BulkInsightRefreshRun.objects.filter(id=run.id).update(started_at=stale_started, updated_at=stale_started)

        snapshot = InsightSnapshot.objects.create(
            account=self.account,
            platform=FACEBOOK,
            payload={"insights": [], "metadata": {}},
        )
        InsightSnapshot.objects.filter(id=snapshot.id).update(fetched_at=timezone.now() - timedelta(minutes=5))

        response = self.client.get("/api/insights/force-refresh-all/status/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "completed")
        self.assertFalse(body["has_active_run"])
        self.assertEqual(body["completed_count"], 1)
        self.assertTrue(body["auto_reconciled"])

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

    @patch("analytics.views.generate_profile_ai_insights")
    def test_ai_profile_insights_returns_structured_report(self, mock_generate):
        InsightSnapshot.objects.create(
            account=self.account,
            platform=FACEBOOK,
            payload={
                "insights": [{"name": "followers_count", "values": [{"value": 1200}]}],
                "published_posts_count": 20,
                "published_posts": [
                    {
                        "id": "p1",
                        "message": "Sample post",
                        "published_at": timezone.now().isoformat(),
                        "total_views": 200,
                        "total_likes": 15,
                        "total_comments": 3,
                        "total_shares": 2,
                        "total_saves": 1,
                    }
                ],
                "metadata": {},
            },
        )
        mock_generate.return_value = {
            "executive_summary": "Growth is stable with room for better cadence.",
            "pros": ["Profile has consistent likes."],
            "cons": ["Comment rate is low."],
            "risks": [],
            "opportunities": ["Push more reels."],
            "posting_strategy": {
                "current_posting": "0.8/day",
                "recommended_posting": "1.5/day",
                "reasoning": "Increase frequency for distribution lift.",
            },
            "action_plan_7d": [],
            "kpi_growth_plan": [],
            "content_ideas": [],
        }

        response = self.client.post(
            f"/api/ai-insights/{self.account.id}/",
            data=json.dumps({"focus": "Increase comments", "force_refresh": False}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("analysis", body)
        self.assertEqual(body["analysis"]["executive_summary"], "Growth is stable with room for better cadence.")
        self.assertEqual(body["page_name"], self.account.page_name)

    @patch("analytics.views.generate_profile_ai_insights")
    def test_ai_profile_insights_handles_openai_errors(self, mock_generate):
        InsightSnapshot.objects.create(
            account=self.account,
            platform=FACEBOOK,
            payload={
                "insights": [],
                "published_posts_count": 0,
                "published_posts": [],
                "metadata": {},
            },
        )
        mock_generate.side_effect = AIInsightsError("OPENAI_API_KEY is missing")

        response = self.client.post(
            f"/api/ai-insights/{self.account.id}/",
            data=json.dumps({"focus": ""}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 502)
        body = response.json()
        self.assertEqual(body["error"], "AI insights unavailable")
        self.assertIn("OPENAI_API_KEY", body["details"])

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
        self.assertEqual(data["post_stats_summary"]["total_posts"], 0)

    def test_build_post_stats_summary_counts_live_cached_and_missing(self):
        summary = build_post_stats_summary(
            [
                {"id": "1", "total_likes": 10, "reason": None},
                {"id": "2", "total_likes": None, "reason": "Live post stats timed out; showing last cached stats."},
                {"id": "3", "total_likes": None, "reason": "Metric unavailable"},
            ]
        )

        self.assertEqual(summary["total_posts"], 3)
        self.assertEqual(summary["live_stats_posts"], 1)
        self.assertEqual(summary["cached_fallback_posts"], 1)
        self.assertEqual(summary["missing_stats_posts"], 1)

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
        self.assertIn("post_stats_summary", combined)

    def test_combined_published_posts_sort_handles_missing_publish_time(self):
        combined = _build_combined_response(
            {
                "platform": "facebook",
                "account_id": 42,
                "page_id": "fb-page",
                "page_name": "FB Page",
                "published_posts": [
                    {"id": "fb-missing", "published_at": None, "scheduled_for": None},
                ],
                "summary": {"total_followers": 1, "total_following": 0, "total_post_share": 1},
                "insights": [],
                "snapshot_id": 1,
                "fetched_at": None,
                "cached": False,
            },
            {
                "platform": "instagram",
                "account_id": 74,
                "page_id": "ig-page",
                "page_name": "IG Page",
                "published_posts": [
                    {"id": "ig-dated", "published_at": "2026-03-12T10:54:26+00:00", "scheduled_for": None},
                ],
                "summary": {"total_followers": 1, "total_following": 1, "total_post_share": 1},
                "insights": [],
                "snapshot_id": 2,
                "fetched_at": "2026-03-12T11:01:00+00:00",
                "cached": False,
            },
        )

        self.assertEqual([row["id"] for row in combined["published_posts"]], ["ig-dated", "fb-missing"])
        self.assertEqual(combined["fetched_at"], "2026-03-12T11:01:00+00:00")

    def test_build_comparison_rows_uses_verified_metric_windows(self):
        rows = build_comparison_rows(
            [
                {
                    "platform": "facebook",
                    "summary": {"total_followers": 146234, "total_following": 0, "total_post_share": 1852},
                    "insights": [
                        {"name": "page_impressions_unique", "period": "day", "values": [{"value": 100}, {"value": 200}]},
                        {"name": "page_posts_impressions", "period": "day", "values": [{"value": 300}, {"value": 400}]},
                        {"name": "page_post_engagements", "period": "day", "values": [{"value": 20}, {"value": 30}]},
                        {
                            "name": "page_actions_post_reactions_like_total",
                            "period": "day",
                            "values": [{"value": 7}, {"value": 11}],
                        },
                        {"name": "page_views_total", "period": "day", "values": [{"value": 12}, {"value": 8}]},
                        {"name": "page_follows", "period": "day", "values": [{"value": 144000}, {"value": 146000}]},
                    ],
                },
                {
                    "platform": "instagram",
                    "summary": {"total_followers": 4379, "total_following": 6, "total_post_share": 1106},
                    "insights": [
                        {"name": "reach", "period": "day", "values": [{"value": 5}, {"value": 10}]},
                        {"name": "profile_views", "period": "day", "total_value": {"value": 16}},
                        {"name": "accounts_engaged", "period": "day", "total_value": {"value": 78}},
                        {"name": "total_interactions", "period": "day", "total_value": {"value": 176}},
                        {"name": "likes", "period": "day", "total_value": {"value": 65}},
                        {"name": "comments", "period": "day", "total_value": {"value": 1}},
                        {"name": "shares", "period": "day", "total_value": {"value": 45}},
                        {"name": "views", "period": "day", "total_value": {"value": 2988}},
                        {"name": "saves", "period": "day", "total_value": {"value": 18}},
                        {"name": "follower_count", "period": "day", "values": [{"value": 0}, {"value": 3}]},
                        {"name": "follows_count", "period": "lifetime", "values": [{"value": 6}]},
                        {"name": "media_count", "period": "lifetime", "values": [{"value": 1106}]},
                    ],
                },
            ],
            [
                {
                    "platform": "facebook",
                    "published_at": "2026-03-12T10:53:26+00:00",
                    "total_likes": 9,
                    "total_comments": 4,
                    "total_shares": 5,
                },
            ],
        )

        indexed = {row["metric"]: row for row in rows}
        self.assertEqual(indexed["Total Reach"]["facebook"], 300)
        self.assertEqual(indexed["Total Reach"]["instagram"], 15)
        self.assertEqual(indexed["Total Profile Views"]["facebook"], 20)
        self.assertEqual(indexed["Total Accounts Engaged"]["facebook"], "N/A")
        self.assertEqual(indexed["Total Interactions"]["facebook"], 50)
        self.assertEqual(indexed["Total Likes"]["facebook"], 18)
        self.assertEqual(indexed["Total Comments"]["facebook"], 4)
        self.assertEqual(indexed["Total Shares"]["facebook"], 5)
        self.assertEqual(indexed["Total Views"]["facebook"], 700)
        self.assertEqual(indexed["Total Saves"]["facebook"], "N/A")
        self.assertEqual(indexed["Total Followers Count"]["facebook"], 2000)
        self.assertEqual(indexed["Total Follows Count"]["facebook"], 146000)
        self.assertEqual(indexed["Total Media Count"]["instagram"], 1106)

    def test_aggregate_recent_post_metric_parses_utc_offset_without_colon(self):
        total_comments = _aggregate_recent_post_metric(
            [
                {
                    "platform": "facebook",
                    "published_at": "2026-03-12T12:30:23+0000",
                    "total_comments": 4,
                },
                {
                    "platform": "facebook",
                    "published_at": "2026-03-11T12:30:23+0000",
                    "total_comments": 2,
                },
            ],
            "facebook",
            "total_comments",
        )

        self.assertEqual(total_comments, 6)


class MetaClientTests(TestCase):
    @patch("core.services.meta_client.requests.get")
    def test_get_raises_transient_error_on_network_timeout(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectTimeout("connect timeout")

        with self.assertRaises(MetaTransientError):
            MetaClient()._get("/test", {"access_token": "token"}, timeout=1)

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

    @patch.object(MetaClient, "_get_by_url")
    @patch.object(MetaClient, "_get")
    def test_fetch_facebook_published_posts_falls_back_to_minimal_fields(self, mock_get, mock_get_by_url):
        def fake_get(path, params, timeout=20):
            self.assertEqual(path, "/page-1/published_posts")
            fields = str(params.get("fields") or "")
            if "attachments{" in fields:
                raise MetaPermanentError("unsupported rich fields", status_code=400, payload={})
            return {
                "data": [{"id": "fb-post-1", "created_time": "2026-03-18T08:00:00+0000"}],
                "paging": {},
            }

        mock_get.side_effect = fake_get
        mock_get_by_url.return_value = {"data": [], "paging": {}}

        rows = MetaClient().fetch_facebook_published_posts("page-1", "token", limit=10)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "fb-post-1")
        attempted_fields = [str(call.args[1].get("fields") or "") for call in mock_get.call_args_list]
        self.assertTrue(any("attachments{" in field for field in attempted_fields))
        self.assertTrue(any(field == "id,message,created_time,permalink_url,full_picture" for field in attempted_fields))

    @patch.object(MetaClient, "_get")
    def test_fetch_facebook_insights_requests_verified_metrics_with_7_day_window(self, mock_get):
        def fake_get(path, params, timeout=20):
            if path == "/page-1":
                return {"fan_count": 10, "followers_count": 20}
            return {
                "data": [
                    {
                        "name": params["metric"],
                        "period": params.get("period"),
                        "values": [{"value": 1}],
                    }
                ]
            }

        mock_get.side_effect = fake_get

        insights = MetaClient().fetch_facebook_insights("page-1", "token")

        names = [metric["name"] for metric in insights]
        self.assertIn("page_impressions_unique", names)
        self.assertIn("page_posts_impressions", names)
        self.assertIn("page_post_engagements", names)
        self.assertIn("page_actions_post_reactions_like_total", names)
        self.assertIn("page_views_total", names)
        self.assertIn("page_follows", names)
        self.assertIn("fan_count", names)
        self.assertIn("followers_count", names)

        insight_calls = [call for call in mock_get.call_args_list if call.args[0] == "/page-1/insights"]
        self.assertTrue(insight_calls)
        for call in insight_calls:
            params = call.args[1]
            self.assertEqual(params["period"], "day")
            self.assertIn("since", params)
            self.assertIn("until", params)

    @patch.object(MetaClient, "_get")
    def test_fetch_instagram_insights_requests_7_day_window(self, mock_get):
        def fake_get(path, params, timeout=20):
            if path == "/ig-1":
                return {"followers_count": 4379, "follows_count": 6, "media_count": 1106}
            return {"data": []}

        mock_get.side_effect = fake_get

        MetaClient().fetch_instagram_insights("ig-1", "token")

        insight_calls = [call for call in mock_get.call_args_list if call.args[0] == "/ig-1/insights"]
        self.assertTrue(insight_calls)
        self.assertTrue(any("since" in call.args[1] and "until" in call.args[1] for call in insight_calls))


@override_settings(
    CELERY_TIMEZONE="Asia/Kolkata",
    DAILY_INSIGHTS_POST_LIMIT=100,
    DAILY_INSIGHTS_POST_STATS_LIMIT=40,
)
class AnalyticsAutomationTaskTests(TestCase):
    def setUp(self):
        cache.clear()
        self.account = ConnectedAccount.objects.create(
            platform=FACEBOOK,
            page_id="auto-1",
            page_name="Auto Page",
            access_token="token",
        )
        self.blank_token_account = ConnectedAccount.objects.create(
            platform=INSTAGRAM,
            page_id="auto-2",
            page_name="Blank Token",
            access_token="",
        )

    @patch("analytics.tasks.refresh_account_insights_snapshot.apply_async")
    def test_queue_daily_heavy_refresh_enqueues_accounts_without_snapshot(self, mock_apply_async):
        result = queue_daily_heavy_insight_refresh()

        self.assertEqual(result["total_accounts"], 2)
        self.assertEqual(result["queued"], 1)
        self.assertEqual(result["skipped"], 1)
        mock_apply_async.assert_called_once_with(args=[self.account.id], kwargs={"force": False}, priority=1)

    @patch("analytics.tasks.refresh_account_insights_snapshot.apply_async")
    def test_queue_daily_heavy_refresh_skips_existing_daily_heavy_snapshot(self, mock_apply_async):
        snapshot = InsightSnapshot.objects.create(
            account=self.account,
            platform=FACEBOOK,
            payload={"metadata": {"collection_mode": DAILY_HEAVY_COLLECTION_MODE}},
        )
        snapshot.fetched_at = timezone.now()
        snapshot.save(update_fields=["fetched_at"])

        result = queue_daily_heavy_insight_refresh()

        self.assertEqual(result["queued"], 0)
        self.assertEqual(result["skipped"], 2)
        mock_apply_async.assert_not_called()

    @patch("analytics.tasks.fetch_and_store_insights")
    def test_refresh_account_insights_snapshot_uses_heavy_limits_and_metadata(self, mock_fetch):
        mock_fetch.return_value = {"snapshot_id": 77}

        task_result = refresh_account_insights_snapshot.apply(args=[self.account.id], kwargs={"force": False}).result

        self.assertEqual(task_result["status"], "stored")
        self.assertEqual(task_result["snapshot_id"], 77)
        mock_fetch.assert_called_once()
        args = mock_fetch.call_args.args
        kwargs = mock_fetch.call_args.kwargs
        self.assertEqual(args[0].id, self.account.id)
        self.assertTrue(kwargs["include_post_stats"])
        self.assertEqual(kwargs["post_limit"], 100)
        self.assertEqual(kwargs["post_stats_limit"], 40)
        self.assertEqual(kwargs["payload_metadata"]["collection_mode"], DAILY_HEAVY_COLLECTION_MODE)
        self.assertEqual(kwargs["payload_metadata"]["collection_source"], "celery_beat")
        self.assertEqual(kwargs["payload_metadata"]["collection_timezone"], "Asia/Kolkata")

    def test_refresh_account_insights_snapshot_skips_when_lock_exists(self):
        cache.set(f"insight_refresh_lock:{self.account.id}", "busy", timeout=60)

        task_result = refresh_account_insights_snapshot.apply(args=[self.account.id], kwargs={"force": False}).result

        self.assertEqual(task_result["status"], "skipped_locked")

    @patch("analytics.services._get_published_posts")
    @patch("analytics.services.MetaClient.fetch_facebook_published_posts_count")
    @patch("analytics.services.MetaClient.fetch_facebook_insights")
    def test_fetch_and_store_insights_persists_snapshot_metadata(self, mock_fetch_insights, mock_posts_count, mock_posts):
        mock_fetch_insights.return_value = [{"name": "followers_count", "values": [{"value": 5}]}]
        mock_posts_count.return_value = 1
        mock_posts.return_value = []

        from analytics.services import fetch_and_store_insights

        result = fetch_and_store_insights(
            self.account,
            payload_metadata={"collection_mode": DAILY_HEAVY_COLLECTION_MODE, "collection_source": "test"},
        )

        snapshot = InsightSnapshot.objects.get(id=result["snapshot_id"])
        self.assertEqual(snapshot.payload["metadata"]["collection_mode"], DAILY_HEAVY_COLLECTION_MODE)
        self.assertEqual(snapshot.payload["metadata"]["collection_source"], "test")


class PublishedPostsStatsFallbackTests(TestCase):
    def setUp(self):
        self.account = ConnectedAccount.objects.create(
            platform=FACEBOOK,
            page_id="fb-page-1",
            page_name="Fallback Page",
            access_token="token",
        )
        InsightSnapshot.objects.create(
            account=self.account,
            platform=FACEBOOK,
            payload={
                "insights": [],
                "published_posts": [
                    {
                        "id": "fb_post_1",
                        "total_views": 777,
                        "total_likes": 88,
                        "total_comments": 9,
                        "total_shares": 5,
                        "total_saves": 0,
                    }
                ],
                "metadata": {},
            },
        )

    @patch("analytics.services.MetaClient.fetch_facebook_post_stats")
    @patch("analytics.services.MetaClient.fetch_facebook_published_posts")
    def test_get_published_posts_uses_cached_stats_when_live_fetch_times_out(self, mock_posts, mock_stats):
        mock_posts.return_value = [
            {
                "id": "fb_post_1",
                "message": "test",
                "created_time": "2026-03-14T04:00:00+0000",
                "attachments": {"data": [{}]},
            }
        ]
        mock_stats.side_effect = MetaTransientError("Connection to graph.facebook.com timed out.")

        from analytics.services import _get_published_posts

        rows = _get_published_posts(self.account, include_post_stats=True, limit=1, stats_limit=1)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "fb_post_1")
        self.assertEqual(rows[0]["total_views"], 777)
        self.assertEqual(rows[0]["total_likes"], 88)
        self.assertEqual(rows[0]["total_comments"], 9)
        self.assertEqual(rows[0]["total_shares"], 5)
        self.assertIn("showing last cached stats", str(rows[0].get("reason", "")).lower())
