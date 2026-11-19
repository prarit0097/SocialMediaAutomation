from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from integrations.models import ConnectedAccount, MetaUserToken
from integrations.tasks import resync_page_tokens_task


class TokenResyncTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="owner")
        self.token = MetaUserToken.objects.create(user=self.user, access_token="user-token")

    @patch("core.services.meta_client.MetaClient.get_managed_pages", return_value=[])
    @patch("core.services.meta_client.MetaClient.debug_token", return_value={
        "data": {
            "is_valid": True,
            "scopes": ["pages_show_list", "pages_manage_posts"],
            "granular_scopes": [{"scope": "pages_show_list", "target_ids": ["business"]}],
        },
    })
    @patch("core.services.meta_client.MetaClient._get")
    def test_partial_asset_batch_retries_and_recovers_the_missing_token(self, get, _debug, _pages):
        get.side_effect = [{}, {"business": {"id": "business", "name": "Business", "access_token": "fresh"}}]

        result = resync_page_tokens_task.apply(args=[self.user.id]).get()

        self.assertEqual(result["status"], "done")
        self.assertEqual(ConnectedAccount.objects.get(user=self.user, page_id="business").access_token, "fresh")

    @patch("core.services.meta_client.meta_app_over_budget", return_value=True)
    @patch("core.services.meta_client.MetaClient.get_managed_pages", return_value=[])
    @patch("core.services.meta_client.MetaClient.debug_token", return_value={})
    def test_over_budget_resync_exhausts_bounded_retries_without_querying_meta(self, debug, pages, _over_budget):
        result = resync_page_tokens_task.apply(args=[self.user.id]).get()

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["retries"], 2)
        pages.assert_not_called()
        debug.assert_not_called()

    @patch("core.services.meta_client.MetaClient.get_managed_pages")
    @patch("core.services.meta_client.MetaClient.debug_token", return_value={
        "data": {
            "is_valid": True,
            "scopes": ["pages_show_list"],
            "granular_scopes": [{"scope": "pages_show_list", "target_ids": ["page"]}],
        },
    })
    def test_old_resync_cannot_overwrite_a_newer_reconnect(self, _debug, pages):
        account = ConnectedAccount.objects.create(
            user=self.user, platform="facebook", page_id="page", page_name="Page", access_token="original",
        )

        def reconnect_before_old_lookup_returns(token):
            MetaUserToken.objects.filter(user=self.user).update(generation=uuid4())
            ConnectedAccount.objects.filter(pk=account.pk).update(access_token="newest")
            return [{"id": "page", "name": "Page", "access_token": "old-response"}]

        pages.side_effect = reconnect_before_old_lookup_returns
        result = resync_page_tokens_task.apply(args=[self.user.id]).get()

        self.assertEqual(result["status"], "superseded")
        account.refresh_from_db()
        self.assertEqual(account.access_token, "newest")
