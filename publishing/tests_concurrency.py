from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue
from threading import Barrier
from time import monotonic, sleep
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
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


class PublishingMigrationTests(TransactionTestCase):
    def test_postgresql_upgrade_of_existing_rows_creates_the_claim_index(self):
        if connection.vendor != "postgresql":
            self.skipTest("Verifies PostgreSQL deferred foreign keys during upgrade")
        prior = ("publishing", "0002_add_scalability_indexes")
        next_revision = ("publishing", "0003_scheduledpost_attempt_count_and_more")
        targets = [prior, ("integrations", "0008_connectedaccount_insight_refresh_expires_at_and_more")]
        self.addCleanup(lambda: MigrationExecutor(connection).migrate([next_revision]))
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        apps = executor.loader.project_state(targets).apps
        user = apps.get_model("auth", "User").objects.create(username="migration-qa", last_login=timezone.now())
        account = apps.get_model("integrations", "ConnectedAccount").objects.create(
            user_id=user.pk, platform="facebook", page_id="migration-page", page_name="Migration QA", access_token="test-token",
        )
        apps.get_model("publishing", "ScheduledPost").objects.create(
            account_id=account.pk, platform="facebook", message="pending", scheduled_for=timezone.now(),
        )

        MigrationExecutor(connection).migrate([next_revision])

        post = ScheduledPost.objects.get()
        self.assertEqual(post.owner_id, user.pk)
        self.assertEqual(post.next_attempt_at, post.scheduled_for)
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, ScheduledPost._meta.db_table)
        self.assertIn("publish_one_ig_claim_per_account", constraints)
