import json
from datetime import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from integrations.models import ConnectedAccount
from planning.models import CalendarContentItem, ContentTag


class PlanningApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="planner", password="pass12345")
        self.client.login(username="planner", password="pass12345")

    def test_create_tag_and_list(self):
        create_res = self.client.post(
            reverse("create_planning_tag"),
            data=json.dumps({"name": "Education", "category": "pillar"}),
            content_type="application/json",
        )
        self.assertEqual(create_res.status_code, 201)

        list_res = self.client.get(reverse("planning_tags"))
        self.assertEqual(list_res.status_code, 200)
        self.assertEqual(len(list_res.json()["tags"]), 1)

    def test_create_calendar_item_and_get_month(self):
        tag = ContentTag.objects.create(owner=self.user, name="Promo", slug="promo", category="pillar")
        create_res = self.client.post(
            reverse("create_calendar_item"),
            data=json.dumps(
                {
                    "title": "March Campaign",
                    "start_at": "2026-03-20T10:30:00",
                    "platform": "both",
                    "status": "draft",
                    "tag_ids": [tag.id],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(create_res.status_code, 201)

        month_res = self.client.get(reverse("planning_calendar_items") + "?month=2026-03")
        self.assertEqual(month_res.status_code, 200)
        self.assertEqual(len(month_res.json()["items"]), 1)

    def test_update_calendar_item_start_at(self):
        item = CalendarContentItem.objects.create(
            owner=self.user,
            title="Move me",
            start_at=timezone.make_aware(datetime(2026, 3, 21, 9, 0, 0)),
            platform="facebook",
            status="draft",
        )
        update_res = self.client.patch(
            reverse("update_calendar_item", args=[item.id]),
            data=json.dumps({"start_at": "2026-03-25T14:00:00"}),
            content_type="application/json",
        )
        self.assertEqual(update_res.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.start_at.day, 25)

    def test_calendar_items_rejects_invalid_month(self):
        response = self.client.get(reverse("planning_calendar_items") + "?month=2026-13")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "month must be YYYY-MM")

    def test_create_calendar_item_rejects_other_users_connected_account(self):
        other_user = get_user_model().objects.create_user(username="planner2", password="pass12345")
        other_account = ConnectedAccount.objects.create(
            user=other_user,
            platform="facebook",
            page_id="fb-other",
            page_name="Other Page",
            access_token="token",
        )

        response = self.client.post(
            reverse("create_calendar_item"),
            data=json.dumps(
                {
                    "title": "Unauthorized account usage",
                    "start_at": "2026-03-20T10:30:00",
                    "platform": "facebook",
                    "connected_account_id": other_account.id,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "connected_account_id is invalid for this user")

    def test_update_calendar_item_returns_404_for_other_users_item(self):
        other_user = get_user_model().objects.create_user(username="planner3", password="pass12345")
        other_item = CalendarContentItem.objects.create(
            owner=other_user,
            title="Other item",
            start_at=timezone.make_aware(datetime(2026, 3, 22, 9, 0, 0)),
            platform="facebook",
            status="draft",
        )

        response = self.client.patch(
            reverse("update_calendar_item", args=[other_item.id]),
            data=json.dumps({"title": "Try hijack"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "Item not found")

    @patch("planning.views.generate_content_calendar_plan")
    def test_generate_ai_calendar_plan_ignores_other_users_account_context(self, mock_generate):
        other_user = get_user_model().objects.create_user(username="planner4", password="pass12345")
        other_account = ConnectedAccount.objects.create(
            user=other_user,
            platform="facebook",
            page_id="fb-other-ctx",
            page_name="Other Context",
            access_token="token",
            is_active=True,
        )
        mock_generate.return_value = {
            "strategy_summary": "Keep cadence steady.",
            "calendar_items": [],
        }

        response = self.client.post(
            reverse("generate_ai_calendar_plan"),
            data=json.dumps(
                {
                    "niche": "Ayurveda",
                    "goal": "Increase reach",
                    "platform": "facebook",
                    "duration_days": 7,
                    "account_id": other_account.id,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["account_context"], {})
        self.assertEqual(mock_generate.call_args.args[0]["account_context"], {})

    @patch("planning.views.generate_content_calendar_plan")
    def test_generate_ai_calendar_plan_returns_rows(self, mock_generate):
        mock_generate.return_value = {
            "strategy_summary": "Use reels + education mix.",
            "cadence_recommendation": "Post daily.",
            "best_time_recommendation": "Tue 10:00-12:00",
            "calendar_items": [
                {
                    "day_label": "Day 1",
                    "post_type": "reel",
                    "platform": "instagram",
                    "topic": "Ayurveda morning routine",
                    "hook": "Start your day right",
                    "cta": "Save this routine.",
                    "best_time_window": "Tue 10:00-12:00",
                    "goal": "Reach",
                }
            ],
        }

        response = self.client.post(
            reverse("generate_ai_calendar_plan"),
            data=json.dumps(
                {
                    "niche": "Ayurveda",
                    "goal": "Increase reach",
                    "platform": "instagram",
                    "duration_days": 7,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("plan", body)
        self.assertEqual(body["plan"]["calendar_items"][0]["post_type"], "reel")
