from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from core.constants import FACEBOOK
from core.exceptions import MetaPermanentError
from integrations.models import ConnectedAccount


class AnalyticsApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="admin", password="pass12345")
        self.client.login(username="admin", password="pass12345")
        self.account = ConnectedAccount.objects.create(
            platform=FACEBOOK,
            page_id="111",
            page_name="Page",
            access_token="token",
        )

    @patch("analytics.services.MetaClient.fetch_facebook_insights")
    def test_fetch_insights(self, mock_fetch):
        mock_fetch.return_value = [{"name": "page_impressions", "values": []}]
        response = self.client.get(f"/api/insights/{self.account.id}/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["platform"], FACEBOOK)
        self.assertIn("insights", body)

    @patch("analytics.services.MetaClient.fetch_facebook_insights")
    def test_fetch_insights_meta_error_returns_json(self, mock_fetch):
        mock_fetch.side_effect = MetaPermanentError("invalid metric", status_code=400, payload={"error": {}})
        response = self.client.get(f"/api/insights/{self.account.id}/")
        self.assertEqual(response.status_code, 502)
        body = response.json()
        self.assertEqual(body["error"], "Failed to fetch insights from Meta")
