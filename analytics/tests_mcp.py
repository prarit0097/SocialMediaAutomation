from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from unittest import skipUnless

from analytics.models import InsightSnapshot
from analytics.tasks import DAILY_HEAVY_COLLECTION_MODE
from core.constants import POST_STATUS_FAILED, POST_STATUS_PENDING, POST_STATUS_PROCESSING, POST_STATUS_PUBLISHED
from integrations.models import ConnectedAccount
from mcp_servers.common import today_daily_heavy_status
try:
    from mcp_servers.meta_insights_server import (
        build_fb_ig_comparison,
        build_latest_snapshot_rows,
        build_posting_gap_rows,
        find_stale_profile_rows,
    )
    from mcp_servers.redis_celery_server import build_publishing_pipeline_status
    MCP_IMPORT_OK = True
except ModuleNotFoundError:
    MCP_IMPORT_OK = False
from publishing.models import ScheduledPost


@skipUnless(MCP_IMPORT_OK, "Optional MCP dependencies are not installed in this environment.")
class MCPServerHelpersTests(TestCase):
    def setUp(self):
        self.fb_account = ConnectedAccount.objects.create(
            platform="facebook",
            page_id="fb-page-1",
            page_name="Test FB Page",
            ig_user_id="ig-user-1",
            access_token="token-fb",
        )
        self.ig_account = ConnectedAccount.objects.create(
            platform="instagram",
            page_id="ig-user-1",
            page_name="Test IG Page",
            ig_user_id="ig-user-1",
            access_token="token-ig",
        )

    def _create_snapshot(self, account, *, fetched_at, total_posts, published_at, metadata=None):
        if account.platform == "facebook":
            insights = [
                {"name": "followers_count", "values": [{"value": 100}], "period": "lifetime"},
                {"name": "fan_count", "values": [{"value": 90}], "period": "lifetime"},
                {"name": "page_impressions_unique", "values": [{"value": 10}, {"value": 20}], "period": "day"},
                {"name": "page_views_total", "values": [{"value": 3}, {"value": 4}], "period": "day"},
                {"name": "page_engaged_users", "values": [{"value": 5}, {"value": 6}], "period": "day"},
                {"name": "page_post_engagements", "values": [{"value": 7}, {"value": 8}], "period": "day"},
                {"name": "page_actions_post_reactions_like_total", "values": [{"value": 2}, {"value": 3}], "period": "day"},
                {"name": "page_posts_impressions", "values": [{"value": 30}, {"value": 40}], "period": "day"},
                {"name": "page_follows", "values": [{"value": 11}, {"value": 14}], "period": "day"},
            ]
            published_posts = [
                {
                    "id": "fb-post-1",
                    "message": "fb",
                    "published_at": published_at.isoformat(),
                    "total_views": 25,
                    "total_likes": 4,
                    "total_comments": 2,
                    "total_shares": 1,
                }
            ]
        else:
            insights = [
                {"name": "followers_count", "values": [{"value": 50}], "period": "lifetime"},
                {"name": "follows_count", "values": [{"value": 6}], "period": "lifetime"},
                {"name": "media_count", "values": [{"value": 33}], "period": "lifetime"},
                {"name": "reach", "values": [{"value": 7}, {"value": 8}], "period": "day"},
                {"name": "profile_views", "values": [{"value": 2}, {"value": 3}], "period": "day"},
                {"name": "accounts_engaged", "values": [{"value": 4}, {"value": 5}], "period": "day"},
                {"name": "total_interactions", "values": [{"value": 6}, {"value": 7}], "period": "day"},
                {"name": "likes", "values": [{"value": 8}, {"value": 9}], "period": "day"},
                {"name": "comments", "values": [{"value": 1}, {"value": 1}], "period": "day"},
                {"name": "shares", "values": [{"value": 2}, {"value": 2}], "period": "day"},
                {"name": "views", "values": [{"value": 10}, {"value": 11}], "period": "day"},
                {"name": "saves", "values": [{"value": 3}, {"value": 4}], "period": "day"},
                {"name": "follower_count", "values": [{"value": 1}, {"value": 1}], "period": "day"},
            ]
            published_posts = [
                {
                    "id": "ig-post-1",
                    "message": "ig",
                    "published_at": published_at.isoformat(),
                    "total_views": 12,
                    "total_likes": 5,
                    "total_comments": 1,
                    "total_shares": 2,
                    "total_saves": 1,
                }
            ]

        snapshot = InsightSnapshot.objects.create(
            account=account,
            platform=account.platform,
            payload={
                "insights": insights,
                "published_posts": published_posts,
                "published_posts_count": total_posts,
                "metadata": metadata or {},
            },
        )
        snapshot.fetched_at = fetched_at
        snapshot.save(update_fields=["fetched_at"])
        return snapshot

    def test_build_latest_snapshot_rows_includes_summary_and_metadata(self):
        now = timezone.now()
        self._create_snapshot(
            self.fb_account,
            fetched_at=now - timedelta(hours=1),
            total_posts=12,
            published_at=now - timedelta(hours=2),
            metadata={"collection_mode": DAILY_HEAVY_COLLECTION_MODE, "collection_source": "test"},
        )

        rows = build_latest_snapshot_rows(limit=10, platform="facebook")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["account_id"], self.fb_account.id)
        self.assertEqual(rows[0]["summary"]["total_followers"], 100)
        self.assertEqual(rows[0]["summary"]["total_post_share"], 12)
        self.assertEqual(rows[0]["collection_mode"], DAILY_HEAVY_COLLECTION_MODE)
        self.assertEqual(rows[0]["collection_source"], "test")

    def test_find_stale_profile_rows_flags_old_snapshot_and_post_gap(self):
        now = timezone.now()
        self._create_snapshot(
            self.fb_account,
            fetched_at=now - timedelta(days=2),
            total_posts=10,
            published_at=now - timedelta(days=3),
        )

        rows = find_stale_profile_rows(snapshot_age_hours=24, post_gap_hours=24, limit=10)
        row = next(item for item in rows if item["account_id"] == self.fb_account.id)

        self.assertIn("stale_snapshot", row["reasons"])
        self.assertIn("posting_gap", row["reasons"])

    def test_build_posting_gap_rows_orders_oldest_first(self):
        now = timezone.now()
        self._create_snapshot(
            self.fb_account,
            fetched_at=now - timedelta(hours=2),
            total_posts=10,
            published_at=now - timedelta(days=5),
        )
        self._create_snapshot(
            self.ig_account,
            fetched_at=now - timedelta(hours=2),
            total_posts=11,
            published_at=now - timedelta(days=2),
        )

        rows = build_posting_gap_rows(min_gap_hours=24, limit=10)

        self.assertEqual(rows[0]["account_id"], self.fb_account.id)
        self.assertGreater(rows[0]["gap_hours"], rows[1]["gap_hours"])

    def test_build_fb_ig_comparison_uses_cached_linked_snapshots(self):
        now = timezone.now()
        self._create_snapshot(
            self.fb_account,
            fetched_at=now - timedelta(hours=1),
            total_posts=12,
            published_at=now - timedelta(hours=3),
        )
        self._create_snapshot(
            self.ig_account,
            fetched_at=now - timedelta(hours=1),
            total_posts=33,
            published_at=now - timedelta(hours=2),
        )

        comparison = build_fb_ig_comparison(self.fb_account.id)

        self.assertEqual(comparison["facebook_account_id"], self.fb_account.id)
        self.assertEqual(comparison["instagram_account_id"], self.ig_account.id)
        self.assertTrue(any(row["metric"] == "Total Followers" for row in comparison["comparison_rows"]))

    def test_today_daily_heavy_status_counts_completed_accounts(self):
        now = timezone.now()
        metadata = {
            "collection_mode": DAILY_HEAVY_COLLECTION_MODE,
            "collection_source": "celery_beat",
            "collection_local_date": now.date().isoformat(),
        }
        self._create_snapshot(
            self.fb_account,
            fetched_at=now - timedelta(hours=1),
            total_posts=12,
            published_at=now - timedelta(hours=2),
            metadata=metadata,
        )
        self._create_snapshot(
            self.ig_account,
            fetched_at=now - timedelta(hours=1),
            total_posts=33,
            published_at=now - timedelta(hours=2),
            metadata=metadata,
        )

        status = today_daily_heavy_status(reference_time=now)

        self.assertEqual(status["accounts_with_tokens"], 2)
        self.assertEqual(status["completed_accounts"], 2)
        self.assertEqual(status["remaining_accounts"], 0)

    def test_build_publishing_pipeline_status_reports_counts_and_failures(self):
        now = timezone.now()
        ScheduledPost.objects.create(
            account=self.fb_account,
            platform="facebook",
            message="pending",
            scheduled_for=now - timedelta(minutes=10),
            status=POST_STATUS_PENDING,
        )
        ScheduledPost.objects.create(
            account=self.fb_account,
            platform="facebook",
            message="processing",
            scheduled_for=now - timedelta(hours=1),
            status=POST_STATUS_PROCESSING,
        )
        stuck = ScheduledPost.objects.get(message="processing")
        ScheduledPost.objects.filter(id=stuck.id).update(updated_at=now - timedelta(hours=1))
        ScheduledPost.objects.create(
            account=self.fb_account,
            platform="facebook",
            message="published",
            scheduled_for=now - timedelta(hours=2),
            status=POST_STATUS_PUBLISHED,
            published_at=now - timedelta(hours=1),
        )
        ScheduledPost.objects.create(
            account=self.fb_account,
            platform="facebook",
            message="failed",
            scheduled_for=now - timedelta(hours=2),
            status=POST_STATUS_FAILED,
            error_message="publish error",
        )

        status = build_publishing_pipeline_status(limit=10)

        self.assertEqual(status["status_counts"]["pending"], 1)
        self.assertEqual(status["status_counts"]["processing"], 1)
        self.assertEqual(status["status_counts"]["published"], 1)
        self.assertEqual(status["status_counts"]["failed"], 1)
        self.assertEqual(status["overdue_pending"], 1)
        self.assertEqual(status["stuck_processing"], 1)
        self.assertEqual(len(status["failed_posts"]), 1)
