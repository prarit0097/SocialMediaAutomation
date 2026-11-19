from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse

from .assistant import answer_question, question_index
from .models import HomepageLead, TRIAL_PERIOD_DAYS
from .subscriptions import SUBSCRIPTION_PLAN_CONFIG


class AssistantKnowledgeTests(SimpleTestCase):
    def test_matches_over_ten_thousand_distinct_question_phrasings(self):
        questions = question_index()
        self.assertGreater(len(questions), 10_000)
        self.assertGreaterEqual(len(set(questions.values())), 70)
        for question, topic in questions.items():
            self.assertEqual(answer_question(question), topic, question)

    def test_typo_hinglish_and_unsupported_questions(self):
        self.assertEqual(answer_question("How much is the yaerly plan?").route, "pricing")
        self.assertIn("monthly", answer_question("Monthly plan ka price kitna hai").answer.lower())
        self.assertEqual(answer_question("Instagram account kaise connect kare?").route, "help")
        self.assertIn("reconnect", answer_question("Mera Meta token expire ho gaya kya karu").answer)
        self.assertIn("checkout status", answer_question("Payment went through but account still locked").answer)
        for preposition in ("to", "on"):
            topic = answer_question(f"Can I post {preposition} Facebook without a Page?")
            self.assertIn("Postzyo publishes to Facebook Pages", topic.answer)
        self.assertIsNone(answer_question("How do I book a hotel in Jaipur?"))
        self.assertIsNone(answer_question("Can I schedule an appointment with my dentist?"))
        self.assertIsNone(answer_question("Tell me if you support auto-replying to DMs"))


class AssistantEndpointTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.ask = reverse("homepage_assistant_ask")
        self.follow_up = reverse("homepage_assistant_follow_up")
        self.contact = {"name": "Asha Demo", "email": "Asha@example.com", "consent": "on"}

    def test_public_answers_use_current_trial_and_pricing(self):
        trial = self.client.post(self.ask, {"question": "How long is the free trial?"}).json()
        self.assertIn(f"{TRIAL_PERIOD_DAYS} days", trial["answer"])
        pricing = self.client.post(self.ask, {"question": "monthly plan price"}).json()
        self.assertIn(SUBSCRIPTION_PLAN_CONFIG["monthly"]["price_label"], pricing["answer"])
        self.assertEqual(pricing["link"], reverse("pricing"))
        self.assertFalse(HomepageLead.objects.exists())

    def test_empty_oversized_and_unknown_questions(self):
        for value in ("", "x" * 301):
            self.assertEqual(self.client.post(self.ask, {"question": value}).status_code, 400)
        unknown = self.client.post(self.ask, {"question": "Can you tell me tomorrow's weather?"})
        self.assertEqual(unknown.json()["link"], reverse("help"))
        self.assertFalse(HomepageLead.objects.exists())

    def test_lead_requires_consent_and_valid_fields_and_is_idempotent(self):
        for fields in (
            {**self.contact, "consent": ""},
            {**self.contact, "consent": "anything"},
            {**self.contact, "name": " "},
            {**self.contact, "email": "invalid"},
        ):
            self.assertEqual(self.client.post(self.follow_up, fields).status_code, 400)
        self.assertFalse(HomepageLead.objects.exists())
        first = self.client.post(self.follow_up, self.contact)
        duplicate = self.client.post(self.follow_up, {**self.contact, "email": "asha@example.com", "name": "Ignored"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), duplicate.json())
        lead = HomepageLead.objects.get()
        self.assertEqual((lead.name, lead.email), ("Asha Demo", "asha@example.com"))
        self.assertIsNotNone(lead.created_at)

    def test_lead_normalizes_internationalized_domains_and_checks_encoded_length(self):
        overly_long = "a@" + ("é" * 47 + ".") * 5 + "com"
        self.assertEqual(self.client.post(self.follow_up, {**self.contact, "email": overly_long}).status_code, 400)
        self.assertFalse(HomepageLead.objects.exists())
        for address in ("asha@bücher.example", "asha@xn--bcher-kva.example"):
            self.assertEqual(self.client.post(self.follow_up, {**self.contact, "email": address}).status_code, 200)
        self.assertEqual(HomepageLead.objects.get().email, "asha@xn--bcher-kva.example")

    def test_post_only_csrf_and_spam_rejection(self):
        client = Client(enforce_csrf_checks=True)
        for url, data in ((self.ask, {"question": "free trial"}), (self.follow_up, self.contact)):
            self.assertEqual(client.get(url).status_code, 405)
            self.assertEqual(client.post(url, data).status_code, 403)
            response = client.get(reverse("landing"))
            token = response.cookies["csrftoken"].value
            self.assertEqual(client.post(url, data, HTTP_X_CSRFTOKEN=token).status_code, 200)
        spam = self.client.post(self.follow_up, {**self.contact, "email": "spam@example.com", "website": "spam"})
        self.assertEqual(spam.status_code, 200)
        self.assertFalse(HomepageLead.objects.filter(email="spam@example.com").exists())

    def test_follow_up_is_throttled_separately_from_questions(self):
        for index in range(5):
            self.assertEqual(self.client.post(self.follow_up, {**self.contact, "email": f"user{index}@example.com"}).status_code, 200)
        self.assertEqual(self.client.post(self.follow_up, self.contact).status_code, 429)
        self.assertEqual(self.client.post(self.ask, {"question": "free trial"}).status_code, 200)
        self.assertEqual(HomepageLead.objects.count(), 5)

    def test_leads_are_only_visible_to_authorized_staff(self):
        HomepageLead.objects.create(name="Asha Demo", email="asha@example.com")
        url = reverse("admin:accounts_homepagelead_changelist")
        self.assertEqual(self.client.get(url).status_code, 302)
        staff = get_user_model().objects.create_user(username="staff", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(url).status_code, 403)
        staff.user_permissions.add(Permission.objects.get(codename="view_homepagelead"))
        self.assertContains(self.client.get(url), "asha@example.com")
        self.assertEqual(self.client.get(reverse("admin:accounts_homepagelead_add")).status_code, 403)
