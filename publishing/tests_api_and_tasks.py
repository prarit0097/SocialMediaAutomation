from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.constants import FACEBOOK, INSTAGRAM, POST_STATUS_FAILED, POST_STATUS_PENDING, POST_STATUS_PUBLISHED
from core.exceptions import MetaPermanentError
from integrations.models import ConnectedAccount
from publishing.models import ScheduledPost
from publishing.services import publish_scheduled_post
from publishing.tasks import publish_post_task


class PublishingApiTests(TestCase):
    def setUp(self):
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

    def test_schedule_post_success(self):
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

    def test_schedule_post_both_creates_facebook_and_instagram_jobs(self):
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
    def test_instagram_image_uses_image_media_kind(self, mock_create_media, _mock_publish_media):
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
    def test_instagram_video_uses_video_media_kind(self, mock_create_media, _mock_publish_media):
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
