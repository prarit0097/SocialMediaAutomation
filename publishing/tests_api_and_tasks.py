import json
import base64
from datetime import timedelta
from unittest.mock import Mock, patch
from unittest import skipUnless
import requests

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import Client, TestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone
from requests import Response

from core.constants import FACEBOOK, INSTAGRAM, POST_STATUS_FAILED, POST_STATUS_PENDING, POST_STATUS_PUBLISHED
from core.exceptions import MetaPermanentError, MetaTransientError
from core.services.meta_client import MetaClient
from integrations.models import ConnectedAccount
from publishing.models import ScheduledPost
from publishing.media_utils import Image as PIL_IMAGE
from publishing.media_utils import ensure_public_media_fetchable
from publishing.services import publish_scheduled_post
from publishing.tasks import _get_due_posts, _ig_cooldown_key, publish_post_task


class PublishingApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = get_user_model().objects.create_user(username="admin")
        self.client.force_login(self.user)
        self.account = ConnectedAccount.objects.create(
            user=self.user,
            platform=FACEBOOK,
            page_id="123",
            page_name="FB Page",
            ig_user_id="17890001",
            access_token="token",
        )
        self.ig_account = ConnectedAccount.objects.create(
            user=self.user,
            platform=INSTAGRAM,
            page_id="17890001",
            page_name="IG Page",
            ig_user_id="17890001",
            access_token="token",
        )

    @patch("publishing.views.MetaClient.debug_token", return_value={"data": {"is_valid": True}})
    def test_schedule_post_success(self, _mock_debug_token):
        response = self.client.post(
            reverse("schedule_post"),
            data={
                "account_id": self.account.id,
                "platform": FACEBOOK,
                "message": "Hello",
                "scheduled_for": (timezone.now() + timedelta(minutes=10)).isoformat(),
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(ScheduledPost.objects.count(), 1)
        self.assertEqual(ScheduledPost.objects.first().status, POST_STATUS_PENDING)

    @patch("publishing.views.MetaClient.debug_token", return_value={"data": {"is_valid": True}})
    def test_bulk_schedule_creates_one_post_per_owned_account_and_skips_others(self, _mock_debug_token):
        fb2 = ConnectedAccount.objects.create(
            user=self.user, platform=FACEBOOK, page_id="999", page_name="FB Two", access_token="token",
        )
        other_user = get_user_model().objects.create_user(username="other-bulk", password="pass12345")
        other_acc = ConnectedAccount.objects.create(
            user=other_user, platform=FACEBOOK, page_id="888", page_name="Other", access_token="token",
        )
        when = (timezone.now() + timedelta(minutes=10)).isoformat()
        response = self.client.post(
            reverse("bulk_schedule_post"),
            data={
                "account_ids": [self.account.id, fb2.id, other_acc.id, self.account.id],  # dup + foreign id
                "message": "Hello all",
                "scheduled_for": when,
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["created_count"], 2)        # self.account + fb2 (dup collapsed)
        self.assertEqual(body["skipped_count"], 1)        # other user's account
        self.assertEqual(ScheduledPost.objects.count(), 2)
        created_ids = [c["account_id"] for c in body["created"]]
        self.assertNotIn(other_acc.id, created_ids)       # IDOR: never schedules a foreign account

    def test_bulk_schedule_rejects_empty_account_list(self):
        response = self.client.post(
            reverse("bulk_schedule_post"),
            data={"account_ids": [], "message": "x", "scheduled_for": (timezone.now() + timedelta(minutes=5)).isoformat()},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_publish_health_status_counts_retrying_rows(self):
        ScheduledPost.objects.create(
            account=self.ig_account,
            platform=INSTAGRAM,
            message="retrying ig",
            media_url="https://example.com/a.mp4",
            scheduled_for=timezone.now() + timedelta(minutes=4),
            status=POST_STATUS_PENDING,
            error_message="Meta is temporarily pacing requests. Auto-retry in 180s. Last Meta response: (#4)",
        )
        ScheduledPost.objects.create(
            account=self.account,
            platform=FACEBOOK,
            message="processing fb",
            scheduled_for=timezone.now(),
            status="processing",
        )

        response = self.client.get(reverse("publish_health_status"))

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["retrying_count"], 1)
        self.assertEqual(body["retrying_instagram_count"], 1)
        self.assertEqual(body["processing_count"], 1)

    def test_schedule_post_rejects_account_platform_mismatch(self):
        response = self.client.post(
            reverse("schedule_post"),
            data={
                "account_id": self.account.id,
                "platform": "instagram",
                "message": "Hello",
                "scheduled_for": (timezone.now() + timedelta(minutes=10)).isoformat(),
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "account_id does not belong to selected platform")

    @patch("publishing.views.MetaClient.debug_token", return_value={"data": {"is_valid": True}})
    def test_schedule_post_both_creates_facebook_and_instagram_jobs(self, _mock_debug_token):
        response = self.client.post(
            reverse("schedule_post"),
            data={
                "account_id": self.account.id,
                "platform": "both",
                "message": "Hello both",
                "media_url": "https://example.com/a.jpg",
                "scheduled_for": (timezone.now() + timedelta(minutes=10)).isoformat(),
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(ScheduledPost.objects.count(), 2)
        created_platforms = set(ScheduledPost.objects.values_list("platform", flat=True))
        self.assertEqual(created_platforms, {FACEBOOK, INSTAGRAM})
        posts_by_platform = {post.platform: post for post in ScheduledPost.objects.all()}
        self.assertEqual(
            posts_by_platform[INSTAGRAM].next_attempt_at - posts_by_platform[FACEBOOK].next_attempt_at,
            timedelta(seconds=5),
        )

    @patch("publishing.views.MetaClient.debug_token", return_value={"data": {"is_valid": True}})
    def test_schedule_post_instagram_moves_to_next_available_slot_when_conflict_exists(self, _mock_debug_token):
        desired_dt = timezone.now() + timedelta(minutes=10)
        ScheduledPost.objects.create(
            account=self.ig_account,
            platform=INSTAGRAM,
            message="Existing IG",
            media_url="https://example.com/existing.jpg",
            scheduled_for=desired_dt,
            status=POST_STATUS_PENDING,
        )

        response = self.client.post(
            reverse("schedule_post"),
            data={
                "account_id": self.ig_account.id,
                "platform": INSTAGRAM,
                "message": "Hello IG",
                "media_url": "https://example.com/new.jpg",
                "scheduled_for": desired_dt.isoformat(),
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        created = ScheduledPost.objects.exclude(message="Existing IG").get()
        self.assertEqual(created.scheduled_for, desired_dt)
        self.assertEqual(created.next_attempt_at, desired_dt + timedelta(seconds=120))

    @patch("publishing.views.MetaClient.debug_token", return_value={"data": {"is_valid": True}})
    def test_schedule_post_instagram_keeps_stepping_until_free_slot_exists(self, _mock_debug_token):
        desired_dt = timezone.now() + timedelta(minutes=10)
        ScheduledPost.objects.create(
            account=self.ig_account,
            platform=INSTAGRAM,
            message="Existing IG 1",
            media_url="https://example.com/existing-1.jpg",
            scheduled_for=desired_dt,
            status=POST_STATUS_PENDING,
        )
        ScheduledPost.objects.create(
            account=self.ig_account,
            platform=INSTAGRAM,
            message="Existing IG 2",
            media_url="https://example.com/existing-2.jpg",
            scheduled_for=desired_dt + timedelta(seconds=120),
            status=POST_STATUS_PENDING,
        )

        response = self.client.post(
            reverse("schedule_post"),
            data={
                "account_id": self.ig_account.id,
                "platform": INSTAGRAM,
                "message": "Hello IG chained",
                "media_url": "https://example.com/new.jpg",
                "scheduled_for": desired_dt.isoformat(),
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        created = ScheduledPost.objects.get(message="Hello IG chained")
        self.assertEqual(created.scheduled_for, desired_dt)
        self.assertEqual(created.next_attempt_at, desired_dt + timedelta(seconds=240))

    @patch("publishing.views.MetaClient.debug_token", return_value={"data": {"is_valid": True}})
    def test_schedule_post_both_moves_instagram_leg_to_next_available_slot_when_conflict_exists(self, _mock_debug_token):
        desired_dt = timezone.now() + timedelta(minutes=10)
        ScheduledPost.objects.create(
            account=self.ig_account,
            platform=INSTAGRAM,
            message="Existing IG",
            media_url="https://example.com/existing.jpg",
            scheduled_for=desired_dt + timedelta(seconds=5),
            status=POST_STATUS_PENDING,
        )

        response = self.client.post(
            reverse("schedule_post"),
            data={
                "account_id": self.account.id,
                "platform": "both",
                "message": "Hello both",
                "media_url": "https://example.com/a.jpg",
                "scheduled_for": desired_dt.isoformat(),
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        fb_post = ScheduledPost.objects.get(platform=FACEBOOK, message="Hello both")
        ig_post = ScheduledPost.objects.get(platform=INSTAGRAM, message="Hello both")
        self.assertEqual(fb_post.scheduled_for, desired_dt)
        self.assertEqual(ig_post.scheduled_for, desired_dt)
        self.assertEqual(ig_post.next_attempt_at, desired_dt + timedelta(seconds=125))

    @patch("publishing.views.MetaClient.debug_token", return_value={"data": {"is_valid": True}})
    def test_schedule_post_allows_bm_account_with_old_sync_but_valid_token(self, _mock_debug_token):
        # A Business-Manager page not in the latest /me/accounts reconnect (old updated_at)
        # but with a valid token must be schedulable — the old reconnect-recency rule
        # wrongly blocked these.
        from django.core.cache import cache

        cache.set(f"meta_last_sync:{self.user.id}", {"synced_at": timezone.now().isoformat()}, timeout=600)
        ConnectedAccount.objects.filter(id=self.account.id).update(updated_at=timezone.now() - timedelta(hours=2))

        response = self.client.post(
            reverse("schedule_post"),
            data={
                "account_id": self.account.id,
                "platform": FACEBOOK,
                "message": "Hello",
                "scheduled_for": (timezone.now() + timedelta(minutes=10)).isoformat(),
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)

    def test_schedule_post_rejects_token_expired_account(self):
        ConnectedAccount.objects.filter(id=self.account.id).update(token_expires_at=timezone.now() - timedelta(days=1))

        response = self.client.post(
            reverse("schedule_post"),
            data={
                "account_id": self.account.id,
                "platform": FACEBOOK,
                "message": "Hello",
                "scheduled_for": (timezone.now() + timedelta(minutes=10)).isoformat(),
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("expired", response.json()["error"].lower())

    @patch("publishing.views.MetaClient.debug_token")
    def test_schedule_post_rejects_invalid_token_before_queueing(self, mock_debug_token):
        mock_debug_token.return_value = {"data": {"is_valid": False}}

        response = self.client.post(
            reverse("schedule_post"),
            data={
                "account_id": self.account.id,
                "platform": FACEBOOK,
                "message": "Hello",
                "scheduled_for": (timezone.now() + timedelta(minutes=10)).isoformat(),
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Reconnect the profile from Accounts", response.json()["error"])
        self.assertEqual(ScheduledPost.objects.count(), 0)

    def test_schedule_post_rejects_inactive_account_with_empty_token(self):
        ConnectedAccount.objects.filter(id=self.account.id).update(is_active=False)

        response = self.client.post(
            reverse("schedule_post"),
            data={
                "account_id": self.account.id,
                "platform": FACEBOOK,
                "message": "Hello",
                "scheduled_for": (timezone.now() + timedelta(minutes=10)).isoformat(),
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("reconnect", response.json()["error"].lower())

    @patch("publishing.views.MetaClient.debug_token")
    def test_retry_failed_post_rejects_invalid_token_until_reconnected(self, mock_debug_token):
        post = ScheduledPost.objects.create(
            account=self.account,
            platform=FACEBOOK,
            message="Hello",
            scheduled_for=timezone.now(),
            status=POST_STATUS_FAILED,
            error_message="Error validating access token: session invalidated (code=190, subcode=460)",
        )
        mock_debug_token.return_value = {"data": {"is_valid": False}}

        response = self.client.post(
            reverse("retry_failed_post", args=[post.id]),
            data={},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        post.refresh_from_db()
        self.assertEqual(post.status, POST_STATUS_FAILED)
        self.assertIn("Reconnect the profile from Accounts", response.json()["error"])

    @patch("publishing.views.MetaClient.debug_token")
    def test_retry_failed_post_allows_retry_after_reconnect(self, mock_debug_token):
        post = ScheduledPost.objects.create(
            account=self.account,
            platform=FACEBOOK,
            message="Hello",
            scheduled_for=timezone.now(),
            status=POST_STATUS_FAILED,
            error_message="Error validating access token: session invalidated (code=190, subcode=460)",
        )
        mock_debug_token.return_value = {"data": {"is_valid": True}}

        response = self.client.post(
            reverse("retry_failed_post", args=[post.id]),
            data={},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        post.refresh_from_db()
        self.assertEqual(post.status, POST_STATUS_PENDING)

    @override_settings(PUBLIC_BASE_URL="https://public.example.com")
    @patch("publishing.views.MetaClient.debug_token", return_value={"data": {"is_valid": True}})
    @skipUnless(PIL_IMAGE is not None, "Pillow is required for Instagram local image optimization tests.")
    def test_schedule_instagram_optimizes_local_png_url(self, _mock_debug_token):
        image = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z1ioAAAAASUVORK5CYII="
        )
        temp = default_storage.save("scheduled_uploads/test_schedule_instagram.png", ContentFile(b"placeholder"))
        temp_path = default_storage.path(temp)
        with open(temp_path, "wb") as image_file:
            image_file.write(image)
        media_url = "https://public.example.com/media/scheduled_uploads/test_schedule_instagram.png"

        try:
            response = self.client.post(
                reverse("schedule_post"),
                data={
                    "account_id": self.ig_account.id,
                    "platform": INSTAGRAM,
                    "message": "Hello",
                    "media_url": media_url,
                    "scheduled_for": (timezone.now() + timedelta(minutes=10)).isoformat(),
                },
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 201)
            created = ScheduledPost.objects.get(id=response.json()["id"])
            self.assertTrue(created.media_url.endswith("_ig.jpg"))
        finally:
            if default_storage.exists(temp):
                default_storage.delete(temp)
            derived = "scheduled_uploads/test_schedule_instagram_ig.jpg"
            if default_storage.exists(derived):
                default_storage.delete(derived)

    @override_settings(PUBLIC_BASE_URL="https://public.example.com")
    @patch("publishing.views.MetaClient.debug_token", return_value={"data": {"is_valid": True}})
    @skipUnless(PIL_IMAGE is not None, "Pillow is required for Instagram local image optimization tests.")
    def test_retry_failed_instagram_optimizes_local_png_url(self, _mock_debug_token):
        image = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z1ioAAAAASUVORK5CYII="
        )
        temp = default_storage.save("scheduled_uploads/test_retry_instagram.png", ContentFile(b"placeholder"))
        temp_path = default_storage.path(temp)
        with open(temp_path, "wb") as image_file:
            image_file.write(image)
        media_url = "https://public.example.com/media/scheduled_uploads/test_retry_instagram.png"
        post = ScheduledPost.objects.create(
            account=self.ig_account,
            platform=INSTAGRAM,
            message="Hello",
            media_url=media_url,
            scheduled_for=timezone.now(),
            status=POST_STATUS_FAILED,
            error_message="Timeout (code=-2, subcode=2207003, title=Timeout)",
        )

        try:
            response = self.client.post(
                reverse("retry_failed_post", args=[post.id]),
                data={},
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)
            post.refresh_from_db()
            self.assertEqual(post.status, POST_STATUS_PENDING)
            self.assertTrue(post.media_url.endswith("_ig.jpg"))
        finally:
            if default_storage.exists(temp):
                default_storage.delete(temp)
            derived = "scheduled_uploads/test_retry_instagram_ig.jpg"
            if default_storage.exists(derived):
                default_storage.delete(derived)


class PublishingTaskTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="scheduled")
        self.facebook = self.account(FACEBOOK, "123")

    def account(self, platform, page_id):
        return ConnectedAccount.objects.create(
            user=self.user, platform=platform, page_id=page_id, page_name=platform, access_token="token",
        )

    def due(self, account):
        return ScheduledPost.objects.create(
            account=account, platform=account.platform, message="Hello",
            media_url="https://example.com/a.jpg" if account.platform == INSTAGRAM else None,
            scheduled_for=timezone.now() - timedelta(minutes=1),
        )

    @patch("publishing.services.MetaClient.publish_facebook_post")
    def test_legacy_evidence_is_finalized_without_a_second_write(self, provider):
        post = self.due(self.facebook)
        ScheduledPost.objects.filter(pk=post.pk).update(external_post_id="123_987")

        self.assertEqual(publish_post_task(post.id)["status"], "already_published")

        post.refresh_from_db()
        self.assertEqual((post.status, post.external_post_id), (POST_STATUS_PUBLISHED, "123_987"))
        self.assertIsNotNone(post.published_at)
        provider.assert_not_called()

    @patch("publishing.services.MetaClient.check_ig_publishing_limit", return_value={"quota_remaining": 1})
    @patch("publishing.services.ensure_public_media_fetchable")
    @patch("publishing.services.MetaClient.create_instagram_media", side_effect=MetaTransientError("Application request limit (code=4)"))
    def test_instagram_rate_limit_defers_with_moderate_backoff_and_account_cooldown(self, provider, preflight, quota):
        post = self.due(self.account(INSTAGRAM, "456"))
        original = post.scheduled_for

        result = publish_post_task(post.id)

        post.refresh_from_db()
        self.assertEqual(result["retry_in"], 75)
        self.assertEqual((post.status, post.blocked_reason), ("retry_wait", "provider_cooldown"))
        self.assertEqual(post.scheduled_for, original)
        self.assertTrue(cache.get(_ig_cooldown_key(post.account_id)))

    def test_instagram_claims_are_serialized_per_account(self):
        account = self.account(INSTAGRAM, "456")
        first = self.due(account)
        second = self.due(account)
        other = self.due(self.account(INSTAGRAM, "789"))

        selected = {post.id for post in _get_due_posts()}

        self.assertEqual(selected, {first.id, other.id})
        self.assertEqual(publish_post_task(second.id)["status"], "skipped_state")
        second.refresh_from_db()
        self.assertEqual(second.status, POST_STATUS_PENDING)

    def test_due_selection_skips_only_the_cooled_instagram_account(self):
        cooled = self.due(self.account(INSTAGRAM, "456"))
        available = self.due(self.account(INSTAGRAM, "789"))
        facebook = self.due(self.facebook)
        cache.set(_ig_cooldown_key(cooled.account_id), True, timeout=60)

        self.assertEqual({post.id for post in _get_due_posts()}, {available.id, facebook.id})

class PublishingServiceTests(TestCase):
    def setUp(self):
        quota = patch("publishing.services.MetaClient.check_ig_publishing_limit", return_value={"quota_remaining": 1})
        quota.start()
        self.addCleanup(quota.stop)
        self.account = ConnectedAccount.objects.create(
            platform=FACEBOOK,
            page_id="123",
            page_name="FB Page",
            access_token="token",
        )
        self.ig_account = ConnectedAccount.objects.create(
            platform=INSTAGRAM,
            page_id="17890001",
            page_name="IG Page",
            ig_user_id="17890001",
            access_token="token",
        )

    @patch("publishing.services.ensure_public_media_fetchable")
    @patch("publishing.services.MetaClient.publish_facebook_photo", return_value={"post_id": "photo-post-id"})
    def test_facebook_media_uses_photo_endpoint(self, mock_publish_photo, _mock_preflight):
        post = ScheduledPost.objects.create(
            account=self.account,
            platform=FACEBOOK,
            message="With image",
            media_url="https://example.com/a.jpg",
            scheduled_for=timezone.now(),
            status="processing",
        )
        result = publish_scheduled_post(post, before_final_write=lambda: None)
        self.assertEqual(result, "photo-post-id")
        mock_publish_photo.assert_called_once()

    @patch("publishing.services.ensure_public_media_fetchable")
    @patch("publishing.services.MetaClient.publish_facebook_video", return_value={"id": "video-node-id", "post_id": "fb-page_post-id"})
    def test_facebook_video_prefers_post_id_for_external_id(self, mock_publish_video, _mock_preflight):
        post = ScheduledPost.objects.create(
            account=self.account,
            platform=FACEBOOK,
            message="With video",
            media_url="https://example.com/a.mp4",
            scheduled_for=timezone.now(),
            status="processing",
        )
        result = publish_scheduled_post(post, before_final_write=lambda: None)
        self.assertEqual(result, ("video-node-id", "fb-page_post-id"))
        mock_publish_video.assert_called_once()

    @patch("publishing.services.MetaClient.publish_instagram_media", return_value={"id": "ig-post-id"})
    @patch("publishing.services.MetaClient.create_instagram_media", return_value={"id": "ig-creation-id"})
    @patch("publishing.services.MetaClient.wait_for_instagram_media_ready", return_value={"status_code": "FINISHED"})
    @patch("publishing.services.ensure_public_media_fetchable")
    def test_instagram_image_uses_image_media_kind(
        self,
        _mock_probe,
        _mock_wait,
        mock_create_media,
        _mock_publish_media,
    ):
        post = ScheduledPost.objects.create(
            account=self.ig_account,
            platform=INSTAGRAM,
            message="IG image",
            media_url="https://example.com/a.jpg",
            scheduled_for=timezone.now(),
            status="processing",
        )
        publish_scheduled_post(post, before_final_write=lambda: None)
        mock_create_media.assert_called_once()
        kwargs = mock_create_media.call_args.kwargs
        self.assertEqual(kwargs["media_kind"], "image")
        self.assertEqual(kwargs["media_url"], "https://example.com/a.jpg")

    @patch("publishing.services.MetaClient.publish_instagram_media", return_value={"id": "ig-post-id"})
    @patch("publishing.services.MetaClient.create_instagram_media", return_value={"id": "ig-creation-id"})
    @patch("publishing.services.MetaClient.wait_for_instagram_media_ready", return_value={"status_code": "FINISHED"})
    @patch("publishing.services.ensure_public_media_fetchable")
    def test_instagram_video_uses_video_media_kind(
        self,
        _mock_probe,
        mock_wait,
        mock_create_media,
        _mock_publish_media,
    ):
        post = ScheduledPost.objects.create(
            account=self.ig_account,
            platform=INSTAGRAM,
            message="IG video",
            media_url="https://example.com/a.mp4",
            scheduled_for=timezone.now(),
            status="processing",
        )
        publish_scheduled_post(post, before_final_write=lambda: None)
        mock_create_media.assert_called_once()
        kwargs = mock_create_media.call_args.kwargs
        self.assertEqual(kwargs["media_kind"], "video")
        self.assertEqual(kwargs["media_url"], "https://example.com/a.mp4")
        mock_wait.assert_called_once_with(creation_id="ig-creation-id", page_access_token="token")

    @override_settings(PUBLIC_BASE_URL="https://public.example.com")
    @patch("publishing.services.MetaClient.publish_instagram_media", return_value={"id": "ig-post-id"})
    @patch("publishing.services.MetaClient.create_instagram_media", return_value={"id": "ig-creation-id"})
    @patch("publishing.services.MetaClient.wait_for_instagram_media_ready", return_value={"status_code": "FINISHED"})
    @patch("publishing.services.ensure_public_media_fetchable")
    @skipUnless(PIL_IMAGE is not None, "Pillow is required for Instagram local image optimization tests.")
    def test_instagram_local_png_is_optimized_before_publish(
        self,
        _mock_probe,
        _mock_wait,
        mock_create_media,
        _mock_publish_media,
    ):
        image = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z1ioAAAAASUVORK5CYII="
        )
        temp = default_storage.save("scheduled_uploads/test_local_instagram.png", ContentFile(b"placeholder"))
        temp_path = default_storage.path(temp)
        with open(temp_path, "wb") as image_file:
            image_file.write(image)
        media_url = "https://public.example.com/media/scheduled_uploads/test_local_instagram.png"
        post = ScheduledPost.objects.create(
            account=self.ig_account,
            platform=INSTAGRAM,
            message="IG image",
            media_url=media_url,
            scheduled_for=timezone.now(),
            status="processing",
        )

        try:
            publish_scheduled_post(post, before_final_write=lambda: None)
            kwargs = mock_create_media.call_args.kwargs
            self.assertEqual(kwargs["media_kind"], "image")
            self.assertTrue(kwargs["media_url"].endswith("_ig.jpg"))
            post.refresh_from_db()
            self.assertTrue(post.media_url.endswith("_ig.jpg"))
        finally:
            if default_storage.exists(temp):
                default_storage.delete(temp)
            derived = "scheduled_uploads/test_local_instagram_ig.jpg"
            if default_storage.exists(derived):
                default_storage.delete(derived)

    def test_handle_response_classifies_meta_download_timeout_as_transient(self):
        response = Response()
        response.status_code = 400
        response._content = (
            b'{"error":{"message":"Timeout","code":-2,"error_subcode":2207003,'
            b'"error_user_title":"Timeout","error_user_msg":"It takes too long to download the media."}}'
        )

        with self.assertRaises(MetaTransientError):
            MetaClient()._handle_response(response)

    def test_handle_response_classifies_app_request_limit_as_transient(self):
        response = Response()
        response.status_code = 400
        response._content = b'{"error":{"message":"Application request limit reached","code":4}}'

        with self.assertRaises(MetaTransientError):
            MetaClient()._handle_response(response)

    def test_handle_response_classifies_instagram_user_restricted_signal_as_transient(self):
        response = Response()
        response.status_code = 400
        response._content = (
            b'{"error":{"message":"User access is restricted","code":25,"error_subcode":2207050,'
            b'"error_user_title":"User is restricted","error_user_msg":"The Instagram account is restricted."}}'
        )

        with self.assertRaises(MetaTransientError):
            MetaClient()._handle_response(response)

    @patch("core.services.meta_client.MetaClient._get_with_transient_retry")
    def test_fetch_facebook_post_stats_resolves_numeric_video_id_to_post_id(self, mock_get_with_retry):
        def _side_effect(path, params, timeout):
            if path == "/12345" and params.get("fields") == "post_id":
                return {"post_id": "999_777"}
            if path == "/999_777" and params.get("fields", "").startswith("reactions.summary"):
                return {
                    "reactions": {"summary": {"total_count": 11}},
                    "comments": {"summary": {"total_count": 3}},
                }
            if path == "/999_777/insights":
                metric = params.get("metric")
                if metric == "post_impressions_unique":
                    return {"data": [{"values": [{"value": 101}]}]}
                return {"data": []}
            raise AssertionError(f"Unexpected call: path={path} params={params}")

        mock_get_with_retry.side_effect = _side_effect

        stats = MetaClient().fetch_facebook_post_stats("12345", "token")
        self.assertEqual(stats["total_likes"], 11)
        self.assertEqual(stats["total_comments"], 3)
        self.assertEqual(stats["total_views"], 101)

    @patch("core.services.meta_client.time.sleep")
    @patch("core.services.meta_client.MetaClient._get", return_value={"status_code": "FINISHED"})
    def test_wait_for_instagram_media_ready_skips_initial_burst_poll(self, mock_get, mock_sleep):
        payload = MetaClient().wait_for_instagram_media_ready(
            "creation-id",
            "token",
            timeout=60,
            poll_interval=12,
        )

        self.assertEqual(payload["status_code"], "FINISHED")
        mock_sleep.assert_called_once_with(8)
        mock_get.assert_called_once()

    @patch("publishing.media_utils._resolved_public_ips", return_value=["93.184.216.34"])
    @patch("publishing.media_utils._PinnedHTTPSConnection.request")
    @patch("publishing.media_utils._PinnedHTTPSConnection.getresponse")
    def test_media_fetchable_stream_timeout_is_transient(self, mock_getresponse, _mock_request, _mock_ips):
        mock_response = Mock()
        mock_response.status = 200
        mock_response.getheader.return_value = "image/jpeg"
        mock_response.read.side_effect = OSError("Read timed out.")
        mock_getresponse.return_value = mock_response

        with self.assertRaises(MetaTransientError):
            ensure_public_media_fetchable("https://example.com/a.jpg")

    def test_media_fetchable_rejects_private_ip_targets_before_request(self):
        with self.assertRaises(MetaPermanentError):
            ensure_public_media_fetchable("http://127.0.0.1/internal.jpg")

    def test_media_fetchable_rejects_localhost_hostname_before_request(self):
        with self.assertRaises(MetaPermanentError):
            ensure_public_media_fetchable("http://localhost/internal.jpg")

    def test_media_fetchable_rejects_non_http_scheme_before_request(self):
        with self.assertRaises(MetaPermanentError):
            ensure_public_media_fetchable("file:///etc/passwd")
