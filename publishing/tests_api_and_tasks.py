from datetime import timedelta
from unittest.mock import patch

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
from publishing.services import publish_scheduled_post
from publishing.tasks import publish_post_task


class PublishingApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = get_user_model().objects.create_user(username="admin", password="pass12345")
        self.client.login(username="admin", password="pass12345")
        self.account = ConnectedAccount.objects.create(
            platform=FACEBOOK,
            page_id="123",
            page_name="FB Page",
            ig_user_id="17890001",
            access_token="token",
        )
        self.ig_account = ConnectedAccount.objects.create(
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

    @patch("publishing.views.process_due_posts")
    def test_list_scheduled_posts_triggers_auto_dispatch_for_due_pending(self, mock_process_due_posts):
        ScheduledPost.objects.create(
            account=self.account,
            platform=FACEBOOK,
            message="Due post",
            scheduled_for=timezone.now() - timedelta(minutes=3),
            status=POST_STATUS_PENDING,
        )

        response = self.client.get(reverse("list_scheduled_posts"))

        self.assertEqual(response.status_code, 200)
        mock_process_due_posts.assert_called_once_with(run_inline=True)

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

    def test_schedule_post_rejects_stale_account_not_in_recent_sync(self):
        from django.core.cache import cache

        cache.set(
            f"meta_last_sync:{self.user.id}",
            {"synced_at": timezone.now().isoformat()},
            timeout=600,
        )
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

        self.assertEqual(response.status_code, 400)
        self.assertIn("not refreshed in the latest Meta reconnect", response.json()["error"])

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
        self.assertIn("inactive", response.json()["error"].lower())

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
    def test_schedule_instagram_optimizes_local_png_url(self, _mock_debug_token):
        from PIL import Image

        image = Image.new("RGB", (1200, 1600), color=(230, 230, 230))
        temp = default_storage.save("scheduled_uploads/test_schedule_instagram.png", ContentFile(b"placeholder"))
        temp_path = default_storage.path(temp)
        image.save(temp_path, format="PNG")
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
    def test_retry_failed_instagram_optimizes_local_png_url(self, _mock_debug_token):
        from PIL import Image

        image = Image.new("RGB", (1200, 1600), color=(220, 220, 220))
        temp = default_storage.save("scheduled_uploads/test_retry_instagram.png", ContentFile(b"placeholder"))
        temp_path = default_storage.path(temp)
        image.save(temp_path, format="PNG")
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
        self.account = ConnectedAccount.objects.create(
            platform=FACEBOOK,
            page_id="123",
            page_name="FB Page",
            access_token="token",
        )
        self.post = ScheduledPost.objects.create(
            account=self.account,
            platform=FACEBOOK,
            message="Hello",
            scheduled_for=timezone.now(),
            status="processing",
        )

    @patch("publishing.tasks.publish_scheduled_post", return_value="meta-post-id")
    def test_publish_post_task_marks_published(self, _mock_publish):
        publish_post_task(self.post.id)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, POST_STATUS_PUBLISHED)
        self.assertEqual(self.post.external_post_id, "meta-post-id")

    @patch("publishing.tasks.publish_scheduled_post")
    def test_publish_post_task_stores_reconnect_guidance_for_invalid_token(self, mock_publish):
        mock_publish.side_effect = MetaPermanentError(
            "Error validating access token: The session has been invalidated. (code=190, subcode=460)"
        )

        publish_post_task(self.post.id)

        self.post.refresh_from_db()
        self.assertEqual(self.post.status, POST_STATUS_FAILED)
        self.assertIn("Reconnect the profile from Accounts", self.post.error_message)

    @patch("publishing.tasks.publish_post_task.retry")
    @patch("publishing.tasks.publish_scheduled_post")
    def test_publish_post_task_uses_longer_backoff_for_graph_rate_limit(self, mock_publish, mock_retry):
        mock_publish.side_effect = MetaTransientError("(#4) Application request limit reached (code=4)")
        mock_retry.side_effect = RuntimeError("retry-invoked")

        with self.assertRaises(RuntimeError):
            publish_post_task(self.post.id)

        mock_retry.assert_called_once()
        self.assertGreaterEqual(mock_retry.call_args.kwargs["countdown"], 90)


class PublishingServiceTests(TestCase):
    def setUp(self):
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

    @patch("publishing.services.MetaClient.publish_facebook_photo", return_value={"post_id": "photo-post-id"})
    def test_facebook_media_uses_photo_endpoint(self, mock_publish_photo):
        post = ScheduledPost.objects.create(
            account=self.account,
            platform=FACEBOOK,
            message="With image",
            media_url="https://example.com/a.jpg",
            scheduled_for=timezone.now(),
            status="processing",
        )
        result = publish_scheduled_post(post)
        self.assertEqual(result, "photo-post-id")
        mock_publish_photo.assert_called_once()

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
        publish_scheduled_post(post)
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
        publish_scheduled_post(post)
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
    def test_instagram_local_png_is_optimized_before_publish(
        self,
        _mock_probe,
        _mock_wait,
        mock_create_media,
        _mock_publish_media,
    ):
        from PIL import Image

        image = Image.new("RGB", (1200, 1600), color=(250, 250, 250))
        temp = default_storage.save("scheduled_uploads/test_local_instagram.png", ContentFile(b"placeholder"))
        temp_path = default_storage.path(temp)
        image.save(temp_path, format="PNG")
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
            publish_scheduled_post(post)
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
