import json
from datetime import datetime

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

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
