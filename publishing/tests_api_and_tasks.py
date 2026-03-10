from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.constants import FACEBOOK, POST_STATUS_PENDING, POST_STATUS_PUBLISHED
from integrations.models import ConnectedAccount
from publishing.models import ScheduledPost
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
