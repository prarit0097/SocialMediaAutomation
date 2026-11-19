from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.constants import FACEBOOK, INSTAGRAM
from core.exceptions import MetaPermanentError, MetaPublishOutcomeUnknown, MetaTransientError
from integrations.models import ConnectedAccount
from publishing.models import ScheduledPost
from publishing.tasks import _get_due_posts, _ig_cooldown_key, _recover_stale_processing, process_due_posts, publish_post_task, reconcile_publish_outcome


class WorkerDied(BaseException):
    pass


class PublishingReliabilityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="publisher")
        self.client.force_login(self.user)
        self.facebook = self.account(FACEBOOK, "123")
        self.instagram = self.account(INSTAGRAM, "456")

    def account(self, platform, page_id):
        return ConnectedAccount.objects.create(
            user=self.user, platform=platform, page_id=page_id, page_name=platform, access_token="token",
        )

    def post(self, account=None, when=None):
        account = account or self.facebook
        return ScheduledPost.objects.create(
            account=account, platform=account.platform, message="Hello",
            media_url="https://example.com/image.jpg" if account.platform == INSTAGRAM else None,
            scheduled_for=when or timezone.now() - timedelta(minutes=2),
        )

    @patch("publishing.tasks.publish_post_task.apply_async")
    @patch("publishing.tasks.publish_scheduled_post", return_value="provider-id")
    def test_list_never_dispatches_due_or_recovers_stale_work(self, provider, enqueue):
        due = self.post()
        old = self.post()
        ScheduledPost.objects.filter(pk=old.pk).update(status="processing", updated_at=timezone.now() - timedelta(hours=1))

        self.assertEqual(self.client.get(reverse("list_scheduled_posts")).status_code, 200)

        due.refresh_from_db()
        old.refresh_from_db()
        self.assertEqual(due.status, "pending")
        self.assertEqual(old.status, "processing")
        provider.assert_not_called()
        enqueue.assert_not_called()

    def test_cooling_instagram_backlog_cannot_hide_due_facebook(self):
        oldest = timezone.now() - timedelta(minutes=10)
        ScheduledPost.objects.bulk_create([
            ScheduledPost(account=self.instagram, platform=INSTAGRAM, media_url="https://example.com/image.jpg", scheduled_for=oldest)
            for _ in range(151)
        ])
        facebook = self.post()
        cache.set(_ig_cooldown_key(self.instagram.id), True)

        self.assertIn(facebook.id, {post.id for post in _get_due_posts()})

    @patch("publishing.tasks.publish_post_task.apply_async")
    def test_dispatch_prioritizes_instagram_and_caps_outstanding_facebook(self, enqueue):
        for _ in range(20):
            self.post()
        instagram = self.post(self.instagram)

        self.assertEqual(process_due_posts()["queued"], 13)
        self.assertEqual(enqueue.call_args_list[0].kwargs["args"][0], instagram.id)
        self.assertLess(enqueue.call_args_list[0].kwargs["priority"], enqueue.call_args_list[1].kwargs["priority"])
        self.assertEqual(process_due_posts()["queued"], 0)

    @patch("publishing.services.MetaClient.publish_facebook_post", side_effect=MetaTransientError("App limit (code=4)"))
    def test_retried_job_preserves_requested_instant(self, provider):
        post = self.post()
        original = post.scheduled_for

        publish_post_task(post.id)

        post.refresh_from_db()
        self.assertEqual(post.scheduled_for, original)
        self.assertEqual(post.status, "retry_wait")
        self.assertGreater(post.next_attempt_at, timezone.now())

    @patch("publishing.views.MetaClient.debug_token", return_value={"data": {"is_valid": True}})
    def test_composer_key_reuses_schedule_and_conflicts_for_changed_content(self, token):
        payload = {"account_id": self.facebook.id, "platform": FACEBOOK, "message": "Original", "scheduled_for": timezone.now().isoformat()}
        headers = {"HTTP_IDEMPOTENCY_KEY": "composer-attempt-1"}
        url = reverse("schedule_post")

        created = self.client.post(url, payload, content_type="application/json", **headers)
        replay = self.client.post(url, payload, content_type="application/json", **headers)
        conflict = self.client.post(url, dict(payload, message="Changed"), content_type="application/json", **headers)

        self.assertEqual(created.status_code, 201)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(created.json()["id"], replay.json()["id"])
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(ScheduledPost.objects.count(), 1)

    @patch("publishing.views.MetaClient.debug_token", return_value={"data": {"is_valid": True}})
    def test_scheduling_rejects_unsupported_instagram_media_and_private_links(self, token):
        for media_url in ("https://example.com/file.webm", "https://127.0.0.1/file.jpg"):
            response = self.client.post(reverse("schedule_post"), {
                "account_id": self.instagram.id, "platform": INSTAGRAM, "message": "Hello", "media_url": media_url,
                "scheduled_for": timezone.now().isoformat(),
            }, content_type="application/json")
            self.assertEqual(response.status_code, 400)
        self.assertFalse(ScheduledPost.objects.exists())

    @patch("publishing.views.MetaClient.debug_token", return_value={"data": {"is_valid": True}})
    def test_bulk_retry_keeps_accepted_leg_when_second_destination_recovers(self, token):
        second = self.account(FACEBOOK, "234")
        ConnectedAccount.objects.filter(pk=second.pk).update(is_active=False)
        url = reverse("bulk_schedule_post")
        payload = {"account_ids": [self.facebook.id, second.id], "message": "Hello", "scheduled_for": timezone.now().isoformat()}
        headers = {"HTTP_IDEMPOTENCY_KEY": "bulk-attempt-1"}

        first = self.client.post(url, payload, content_type="application/json", **headers).json()
        self.assertEqual(first["created_count"], 1)
        ConnectedAccount.objects.filter(pk=second.pk).update(is_active=True)
        retry = self.client.post(url, payload, content_type="application/json", **headers).json()

        self.assertEqual(retry["created_count"], 2)
        self.assertEqual(retry["new_count"], 1)
        self.assertEqual(first["created"][0]["id"], retry["created"][0]["id"])
        self.assertEqual(ScheduledPost.objects.count(), 2)

    @patch("publishing.services.MetaClient.publish_facebook_post")
    def test_duplicate_delivery_has_one_final_write(self, provider):
        post = self.post()
        generation = _get_due_posts()[0].claim_generation

        def accepted(**kwargs):
            post.refresh_from_db()
            self.assertIsNotNone(post.final_write_started_at)
            self.assertEqual(publish_post_task(post.id, generation)["status"], "skipped_state")
            return {"id": "123_987"}

        provider.side_effect = accepted
        publish_post_task(post.id, generation)
        publish_post_task(post.id, generation)

        post.refresh_from_db()
        self.assertEqual((post.status, post.external_post_id), ("published", "123_987"))
        self.assertEqual(post.attempt_count, 1)
        provider.assert_called_once()

    @patch("publishing.tasks.publish_post_task.apply_async", side_effect=RuntimeError("broker lost"))
    @patch("publishing.services.MetaClient.publish_facebook_post", return_value={"id": "123_987"})
    def test_lost_enqueue_recovers_and_late_message_cannot_take_new_claim(self, provider, enqueue):
        post = self.post()
        self.assertEqual(process_due_posts()["queued"], 0)
        post.refresh_from_db()
        first_generation = post.claim_generation
        ScheduledPost.objects.filter(pk=post.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))

        _recover_stale_processing()
        replacement = _get_due_posts()[0]
        publish_post_task(post.id, first_generation)
        provider.assert_not_called()
        publish_post_task(post.id, replacement.claim_generation)

        post.refresh_from_db()
        self.assertEqual(post.status, "published")
        provider.assert_called_once()

    def test_legacy_migration_quarantines_only_unconfirmed_delivery_risk(self):
        pending, risk, confirmed, definitive = (self.post() for _ in range(4))
        ScheduledPost.objects.filter(pk=risk.pk).update(error_message="Temporary Meta delay. Last Meta response: Meta API network error.")
        ScheduledPost.objects.filter(pk=confirmed.pk).update(status="processing", external_post_id="123_987")
        ScheduledPost.objects.filter(pk=definitive.pk).update(status="failed", error_message="Media is unsupported.")
        original = pending.scheduled_for
        migration = import_module("publishing.migrations.0003_scheduledpost_attempt_count_and_more")

        migration.preserve_legacy_delivery(apps, SimpleNamespace(connection=connection))

        for post in (pending, risk, confirmed, definitive):
            post.refresh_from_db()
        self.assertEqual((pending.status, pending.next_attempt_at, pending.owner_id), ("pending", original, self.user.id))
        self.assertEqual((risk.status, risk.stage), ("outcome_unknown", "operator_review"))
        self.assertEqual((confirmed.status, confirmed.external_post_id), ("published", "123_987"))
        self.assertEqual(definitive.status, "failed")

    @patch("publishing.services.MetaClient.publish_facebook_post", side_effect=WorkerDied)
    def test_crash_after_final_marker_goes_to_review_without_retry(self, provider):
        post = self.post()
        with self.assertRaises(WorkerDied):
            publish_post_task(post.id)
        ScheduledPost.objects.filter(pk=post.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))

        _recover_stale_processing()
        reconcile_publish_outcome(post.id)
        publish_post_task(post.id)

        post.refresh_from_db()
        self.assertEqual((post.status, post.stage), ("outcome_unknown", "operator_review"))
        self.assertIsNotNone(post.final_write_started_at)
        provider.assert_called_once()

    @patch("publishing.services.MetaClient.publish_facebook_post", side_effect=MetaPublishOutcomeUnknown("response lost"))
    def test_unknown_provider_outcome_cannot_be_cancelled_or_manually_retried(self, provider):
        post = self.post()
        publish_post_task(post.id)

        for route in ("cancel_scheduled_post", "retry_failed_post", "edit_scheduled_post"):
            self.assertEqual(self.client.post(reverse(route, args=[post.id]), {}, content_type="application/json").status_code, 409)

        post.refresh_from_db()
        self.assertEqual(post.status, "outcome_unknown")
        self.assertIsNotNone(post.final_write_started_at)
        self.assertIsNone(post.external_post_id)

    @patch("publishing.services.MetaClient.publish_facebook_post")
    def test_cancel_before_claim_execution_fences_the_existing_delivery(self, provider):
        post = self.post()
        generation = _get_due_posts()[0].claim_generation
        response = self.client.post(reverse("cancel_scheduled_post", args=[post.id]))
        publish_post_task(post.id, generation)

        self.assertEqual(response.status_code, 200)
        post.refresh_from_db()
        self.assertEqual(post.status, "cancelled")
        provider.assert_not_called()

    def test_reschedule_persists_audit_and_fences_original_delivery(self):
        post = self.post()
        generation = _get_due_posts()[0].claim_generation
        requested = post.scheduled_for
        rescheduled = timezone.now() + timedelta(hours=1)

        response = self.client.post(
            reverse("edit_scheduled_post", args=[post.id]),
            {"scheduled_for": rescheduled.isoformat(), "display_timezone": "Asia/Kolkata"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(publish_post_task(post.id, generation)["status"], "skipped_state")
        post.refresh_from_db()
        audit = post.schedule_changes.get()
        self.assertEqual((audit.previous_scheduled_for, audit.scheduled_for), (requested, rescheduled))
        self.assertEqual(post.next_attempt_at, rescheduled)

    @patch("publishing.services.MetaClient.check_ig_publishing_limit", return_value={"quota_remaining": 0})
    @patch("publishing.services.MetaClient.create_instagram_media")
    def test_zero_quota_defers_before_container_creation(self, create, quota):
        post = self.post(self.instagram)
        requested = post.scheduled_for
        publish_post_task(post.id)

        post.refresh_from_db()
        self.assertEqual((post.status, post.blocked_reason), ("retry_wait", "instagram_quota"))
        self.assertEqual(post.scheduled_for, requested)
        self.assertGreater(post.next_attempt_at, timezone.now() + timedelta(minutes=50))
        create.assert_not_called()

    @patch("publishing.services.MetaClient.check_ig_publishing_limit", return_value={"quota_remaining": 1})
    @patch("publishing.services.ensure_public_media_fetchable")
    @patch("publishing.services.MetaClient.create_instagram_media", return_value={"id": "container-1"})
    @patch("publishing.services.MetaClient.wait_for_instagram_media_ready", return_value={"status_code": "FINISHED"})
    @patch("publishing.services.MetaClient.publish_instagram_media", side_effect=MetaPermanentError(
        "Quota consumed", payload={"error": {"code": 9, "error_subcode": 2207042}},
    ))
    def test_confirmed_final_quota_rejection_defers_safely(self, provider, ready, create, preflight, quota):
        post = self.post(self.instagram)
        publish_post_task(post.id)

        post.refresh_from_db()
        self.assertEqual((post.status, post.blocked_reason), ("retry_wait", "instagram_quota"))
        self.assertIsNone(post.final_write_started_at)
        self.assertEqual(post.ig_container_id, "container-1")

    @override_settings(PUBLISH_CANARY_USER_IDS=[999999])
    @patch("publishing.services.MetaClient.publish_facebook_post")
    def test_opt_in_allowlist_excludes_unenrolled_work(self, provider):
        post = self.post()
        self.assertEqual(_get_due_posts(), [])
        self.assertEqual(publish_post_task(post.id)["status"], "skipped_state")
        provider.assert_not_called()

    @patch("publishing.services.ensure_public_media_fetchable")
    @patch("publishing.services.MetaClient.publish_facebook_photo")
    def test_claim_pause_takes_effect_before_final_write(self, provider, preflight):
        post = self.post()
        ScheduledPost.objects.filter(pk=post.pk).update(media_url="https://example.com/image.jpg")
        paused = override_settings(PUBLISH_CLAIMS_ENABLED=False)
        self.addCleanup(paused.disable)
        preflight.side_effect = lambda url: paused.enable()

        publish_post_task(post.id)

        post.refresh_from_db()
        self.assertEqual((post.status, post.blocked_reason), ("blocked", "release_paused"))
        self.assertIsNone(post.final_write_started_at)
        provider.assert_not_called()

    @patch("publishing.services.MetaClient.publish_facebook_post")
    def test_late_190_preserves_newly_rotated_token(self, provider):
        post = self.post()

        def old_token_rejected(**kwargs):
            ConnectedAccount.objects.filter(pk=self.facebook.pk).update(access_token="fresh-token")
            raise MetaPermanentError("Expired (code=190)", payload={"error": {"code": 190}})

        provider.side_effect = old_token_rejected
        publish_post_task(post.id)

        self.facebook.refresh_from_db()
        post.refresh_from_db()
        self.assertTrue(self.facebook.is_active)
        self.assertEqual((post.status, post.blocked_reason), ("blocked", "token_rotated"))

    @patch("publishing.services.ensure_public_media_fetchable")
    @patch("publishing.services.MetaClient.publish_facebook_photo")
    def test_revocation_during_preparation_stops_final_write(self, provider, preflight):
        post = self.post()
        ScheduledPost.objects.filter(pk=post.pk).update(media_url="https://example.com/image.jpg")

        def revoke(_url):
            ConnectedAccount.objects.filter(pk=self.facebook.pk).update(is_active=False)

        preflight.side_effect = revoke
        publish_post_task(post.id)

        post.refresh_from_db()
        self.assertEqual((post.status, post.blocked_reason), ("blocked", "reconnect_required"))
        self.assertIsNone(post.final_write_started_at)
        provider.assert_not_called()

    @patch("publishing.services.ensure_public_media_fetchable")
    @patch("publishing.services.MetaClient.publish_facebook_video", return_value={"id": "321"})
    @patch("publishing.tasks.MetaClient.get_facebook_video_publish_status", return_value={"status": {"video_status": "ready"}})
    @patch("publishing.tasks.MetaClient.find_facebook_page_post_for_video", return_value="123_987", create=True)
    @patch("publishing.tasks.MetaClient.get_facebook_page_post_status")
    def test_facebook_video_requires_authoritative_confirmation(self, publication, find_post, video, provider, preflight):
        post = self.post()
        ScheduledPost.objects.filter(pk=post.pk).update(media_url="https://example.com/video.mp4")
        publish_post_task(post.id)

        post.refresh_from_db()
        self.assertEqual((post.status, post.provider_upload_id, post.external_post_id), ("outcome_unknown", "321", None))
        for is_published, expected_status in ((False, "outcome_unknown"), (True, "published")):
            publication.return_value = {"is_published": is_published}
            ScheduledPost.objects.filter(pk=post.pk).update(next_attempt_at=timezone.now())
            reconcile_publish_outcome(post.id)
            post.refresh_from_db()
            self.assertEqual(post.status, expected_status)
            self.assertEqual(post.external_post_id, "123_987")
        provider.assert_called_once()

    @patch("publishing.services.MetaClient.check_ig_publishing_limit", return_value={"quota_remaining": 1})
    @patch("publishing.services.ensure_public_media_fetchable")
    @patch("publishing.services.MetaClient.create_instagram_media", return_value={"id": "container-1"})
    @patch("publishing.services.MetaClient.wait_for_instagram_media_ready", return_value={"status_code": "FINISHED"})
    @patch("publishing.services.MetaClient.publish_instagram_media", side_effect=MetaPublishOutcomeUnknown("response lost"))
    @patch("publishing.tasks.MetaClient.get_instagram_container_status", return_value={"status_code": "PUBLISHED"})
    @patch("publishing.views.MetaClient.debug_token", return_value={"data": {"is_valid": True}})
    def test_dual_posts_keep_confirmed_leg_when_other_leg_loses_response(self, token, evidence, instagram, ready, create, preflight, quota):
        self.facebook.ig_user_id = self.instagram.page_id
        self.facebook.save(update_fields=["ig_user_id"])
        response = self.client.post(reverse("schedule_post"), {
            "account_id": self.facebook.id, "platform": "both", "message": "Hello", "media_url": "https://example.com/image.jpg",
            "scheduled_for": (timezone.now() - timedelta(minutes=2)).isoformat(),
        }, content_type="application/json", HTTP_IDEMPOTENCY_KEY="both-platforms")
        self.assertEqual(response.status_code, 201)
        first, second = response.json()["ids"]
        with patch("publishing.services.MetaClient.publish_facebook_photo", return_value={"post_id": "123_987"}):
            publish_post_task(first)
        publish_post_task(second)
        reconcile_publish_outcome(second)

        posts = {row["platform"]: row for row in self.client.get(reverse("list_scheduled_posts")).json()}
        self.assertEqual(posts[FACEBOOK]["status"], "published")
        self.assertEqual(posts[INSTAGRAM]["status"], "outcome_unknown")
        self.assertEqual(posts[INSTAGRAM]["blocked_reason"], "container_published_media_id_missing")
        self.assertEqual(posts[FACEBOOK]["schedule_request_id"], posts[INSTAGRAM]["schedule_request_id"])
        self.assertFalse(posts[INSTAGRAM]["retryable"])
        instagram.assert_called_once()
