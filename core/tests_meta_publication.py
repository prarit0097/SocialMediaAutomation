from unittest.mock import patch

import requests
from django.test import SimpleTestCase, override_settings

from core.exceptions import MetaPermanentError, MetaPublishOutcomeUnknown, MetaTransientError
from core.services.meta_client import MetaClient


@override_settings(META_REQUEST_RETRY_ATTEMPTS=3)
class MetaFinalPublicationTests(SimpleTestCase):
    def test_network_loss_does_not_replay_any_final_write(self):
        client = MetaClient()
        calls = (
            (client.publish_facebook_post, ("page", "token", "caption")),
            (client.publish_facebook_photo, ("page", "token", "https://example.org/photo.jpg")),
            (client.publish_facebook_video, ("page", "token", "https://example.org/video.mp4")),
            (client.publish_instagram_media, ("ig", "token", "container")),
        )
        for publish, args in calls:
            with self.subTest(method=publish.__name__), patch(
                "requests.post", side_effect=requests.ReadTimeout("response lost")
            ) as send, patch("core.services.meta_client.time.sleep"):
                with self.assertRaises(MetaPublishOutcomeUnknown):
                    publish(*args)
                send.assert_called_once()
                self.assertFalse(send.call_args.kwargs["allow_redirects"])

    def test_uncertain_final_responses_are_distinct_from_rejections(self):
        client = MetaClient()
        cases = (
            (502, b'{"error":{"message":"upstream failed","code":2}}'),
            (400, b'{"error":{"message":"Instagram server error","code":9}}'),
            (400, b'{"error":{"message":"response timed out","code":8192}}'),
            (200, b'{"success":true}'),
            (200, b'<html>unexpected response</html>'),
            (307, b'{"id":"not-a-publication"}'),
        )
        for status, body in cases:
            with self.subTest(status=status, body=body):
                response = requests.Response()
                response.status_code = status
                response._content = body
                with patch("requests.post", return_value=response) as send:
                    with self.assertRaises(MetaPublishOutcomeUnknown):
                        client.publish_facebook_post("page", "token", "caption")
                send.assert_called_once()

    def test_final_response_preserves_a_definite_throttle_and_confirmation(self):
        client = MetaClient()
        rejection = requests.Response()
        rejection.status_code = 429
        rejection._content = b'{"error":{"message":"Rate limit exceeded","code":4}}'
        confirmation = requests.Response()
        confirmation.status_code = 200
        confirmation._content = b'{"post_id":"page_post","id":"photo"}'

        with patch("requests.post", side_effect=[rejection, confirmation]) as send:
            with self.assertRaises(MetaTransientError):
                client.publish_facebook_photo("page", "token", "https://example.org/photo.jpg")
            self.assertEqual(client.publish_facebook_photo("page", "token")["post_id"], "page_post")
        self.assertEqual(send.call_count, 2)

    def test_facebook_video_and_page_post_status_are_read_only(self):
        client = MetaClient()
        with patch.object(client, "_get", side_effect=[{"status": {}}, {"is_published": True}]) as read:
            self.assertEqual(client.get_facebook_video_publish_status("video", "token"), {"status": {}})
            self.assertTrue(client.get_facebook_page_post_status("page_post", "token")["is_published"])
        self.assertEqual(read.call_args_list[0].args, ("/video", {"fields": "status", "access_token": "token"}))
        self.assertEqual(read.call_args_list[1].args, ("/page_post", {"fields": "is_published", "access_token": "token"}))

    def test_facebook_video_post_lookup_requires_exact_provider_object_id(self):
        client = MetaClient()
        result = {"data": [
            {"id": "different_post", "object_id": "1234"},
            {"id": "page_post", "object_id": "123"},
        ]}
        with patch.object(client, "_get", side_effect=[{}, result, {"post_id": "other_story"}, result]) as read:
            self.assertEqual(client.find_facebook_page_post_for_video("page", "123", "token"), "page_post")
            self.assertIsNone(client.find_facebook_page_post_for_video("page", "12", "token"))
        read.assert_called_with(
            "/page/published_posts", {"fields": "id,object_id", "limit": 25, "access_token": "token"},
        )
        with patch.object(client, "_get", return_value={"post_id": "page_story"}) as read:
            self.assertEqual(client.find_facebook_page_post_for_video("page", "123", "token"), "page_story")
        read.assert_called_once_with("/123", {"fields": "post_id", "access_token": "token"})

    def test_instagram_quota_reads_graph_data_envelope(self):
        client = MetaClient()
        with patch.object(client, "_get", return_value={"data": [{"quota_usage": 100, "config": {"quota_total": 100}}]}):
            self.assertEqual(
                client.check_ig_publishing_limit("ig", "token"),
                {"quota_usage": 100, "quota_total": 100, "quota_remaining": 0},
            )

    def test_instagram_quota_does_not_turn_unknown_into_zero_usage(self):
        client = MetaClient()
        token_error = MetaPermanentError("Expired token", payload={"error": {"code": 190}})
        with patch.object(client, "_get", side_effect=token_error), self.assertRaises(MetaPermanentError) as error:
            client.check_ig_publishing_limit("ig", "token")
        self.assertIs(error.exception, token_error)
        with patch.object(client, "_get", return_value={"data": []}), self.assertRaises(MetaTransientError):
            client.check_ig_publishing_limit("ig", "token")
