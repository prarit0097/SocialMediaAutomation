from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue
from threading import Barrier
from time import monotonic, sleep
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, connections, transaction
from django.test import RequestFactory, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from integrations.models import ConnectedAccount

from .models import ScheduledPost
from .tasks import _get_due_posts, publish_post_task
from .views import cancel_scheduled_post


class PublishClaimConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_two_deliveries_using_same_generation_send_only_one_final_write(self):
        user = get_user_model().objects.create_user(username="publisher")
        account = ConnectedAccount.objects.create(
            user=user, platform="facebook", page_id="test-page", page_name="Test Page", access_token="mock-token",
        )
        post = ScheduledPost.objects.create(
            account=account, platform="facebook", message="A post", scheduled_for=timezone.now() - timedelta(minutes=1),
        )
        (claim,) = _get_due_posts()
        barrier = Barrier(2)

        def deliver():
            try:
                barrier.wait(timeout=5)
                return publish_post_task.run(post.pk, claim.claim_generation)["status"]
            finally:
                connections.close_all()

        with patch("core.services.meta_client.MetaClient.publish_facebook_post", return_value={"id": "confirmed-id"}) as send:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = [pool.submit(deliver) for _ in range(2)]
                outcomes = [result.result(timeout=10) for result in results]

        send.assert_called_once()
        self.assertEqual(outcomes.count("published"), 1)
        self.assertTrue({*outcomes} <= {"published", "already_published", "skipped_state"})
        post.refresh_from_db()
        self.assertEqual(post.status, "published")
        self.assertEqual(post.external_post_id, "confirmed-id")

    @skipUnlessDBFeature("has_select_for_update")
    def test_cancellation_waiting_on_post_lock_cannot_cross_final_write_boundary(self):
        if connection.vendor != "postgresql":
            self.skipTest("Verifies PostgreSQL row-lock rechecks")
        user = get_user_model().objects.create_user(username="publisher")
        account = ConnectedAccount.objects.create(
            user=user, platform="facebook", page_id="test-page", page_name="Test Page", access_token="mock-token",
        )
        post = ScheduledPost.objects.create(
            account=account, platform="facebook", message="A post", scheduled_for=timezone.now(),
            status="processing", stage="preparing",
        )
        other_connection = Queue()

        def cancel():
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    other_connection.put(cursor.fetchone()[0])
                request = RequestFactory().post(f"/api/posts/{post.pk}/cancel/")
                request.user = user
                return cancel_scheduled_post(request, post.pk).status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                ScheduledPost.objects.select_for_update().get(pk=post.pk)
                response = pool.submit(cancel)
                pid = other_connection.get(timeout=5)
                deadline = monotonic() + 5
                with connection.cursor() as cursor:
                    while monotonic() < deadline:
                        cursor.execute("SELECT cardinality(pg_blocking_pids(%s))", [pid])
                        if cursor.fetchone()[0]:
                            break
                        sleep(0.01)
                    else:
                        self.fail("Cancellation did not wait on the post's row lock")
                ScheduledPost.objects.filter(pk=post.pk).update(
                    stage="final_write", final_write_started_at=timezone.now(),
                )
            self.assertEqual(response.result(timeout=5), 409)

        post.refresh_from_db()
        self.assertEqual(post.status, "processing")
        self.assertEqual(post.stage, "final_write")
