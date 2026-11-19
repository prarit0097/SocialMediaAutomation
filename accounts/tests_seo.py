import json
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urlsplit
from xml.etree import ElementTree

from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import staticfiles_storage
from django.test import SimpleTestCase, override_settings

from accounts.models import TRIAL_PERIOD_DAYS
from accounts.articles import ARTICLES


class PublicHTML(HTMLParser):
    def __init__(self, response):
        super().__init__()
        self.heading_count = 0
        self.canonicals = []
        self.metadata = {}
        self.anchors = set()
        self.title = ""
        self.structured_data = {}
        self.in_title = False
        self.in_json_ld = False
        self.feed(response.content.decode())

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link" and attrs.get("rel") == "canonical":
            self.canonicals.append(attrs["href"])
        if tag == "meta" and (name := attrs.get("name") or attrs.get("property")):
            self.metadata[name] = attrs.get("content")
        if tag == "a" and "href" in attrs:
            self.anchors.add(attrs["href"])
        if tag == "h1":
            self.heading_count += 1
        self.in_title |= tag == "title"
        self.in_json_ld |= tag == "script" and attrs.get("type") == "application/ld+json"

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if self.in_json_ld:
            for node in json.loads(data)["@graph"]:
                self.structured_data[node["@type"]] = node

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag == "script":
            self.in_json_ld = False


@override_settings(
    ALLOWED_HOSTS=["postzyo.com", "www.postzyo.com", "localhost"],
    SECURE_SSL_REDIRECT=False,
    PUBLIC_BASE_URL="https://tunnel.example.test",
)
class PublicSEOTests(SimpleTestCase):
    origin = "https://postzyo.com"
    public_paths = ("/", "/pricing/", "/help/", "/privacy-policy/", "/terms/", "/data-deletion/", "/blog/")
    paths = public_paths + tuple(f"/blog/{slug}/" for slug in ARTICLES)

    def get(self, path, host="postzyo.com"):
        return self.client.get(path, secure=True, HTTP_HOST=host)

    def test_anonymous_public_pages_render_distinct_metadata_and_h1(self):
        titles, descriptions = set(), set()
        for path in self.paths:
            with self.subTest(path=path):
                response = self.get(f"{path}?utm_source=search", "www.postzyo.com")
                self.assertEqual(response.status_code, 200)
                html = PublicHTML(response)
                self.assertEqual(html.heading_count, 1)
                self.assertEqual(html.canonicals, [self.origin + path])
                self.assertEqual(html.metadata["og:url"], self.origin + path)
                self.assertEqual(html.metadata["og:title"], html.title)
                self.assertEqual(html.metadata["og:description"], html.metadata["description"])
                self.assertNotIn("noindex", html.metadata.get("robots", ""))
                image_url = urlsplit(html.metadata["og:image"])
                self.assertEqual((image_url.scheme, image_url.netloc), ("https", "postzyo.com"))
                self.assertTrue(image_url.path.startswith("/static/"))
                static_path = image_url.path.removeprefix("/static/")
                self.assertTrue(finders.find(static_path) or staticfiles_storage.exists(static_path))
                titles.add(html.title)
                descriptions.add(html.metadata["description"])
        self.assertEqual(len(titles), len(self.paths))
        self.assertEqual(len(descriptions), len(self.paths))

    def test_homepage_links_to_public_pages_in_server_rendered_html(self):
        html = PublicHTML(self.get("/"))
        self.assertTrue(set(self.public_paths[1:]) <= html.anchors)

    def test_blog_index_links_to_illustrated_guides_and_unknown_slug_returns_404(self):
        index = PublicHTML(self.get("/blog/"))
        for slug, article in ARTICLES.items():
            with self.subTest(slug=slug):
                path = f"/blog/{slug}/"
                self.assertIn(path, index.anchors)
                response = self.get(path)
                self.assertContains(response, article.image_alt)
                html = PublicHTML(response)
                self.assertEqual(html.metadata["og:type"], "article")
                self.assertEqual(html.structured_data["BlogPosting"]["headline"], article.title)
                self.assertIn("/blog/", html.anchors)
        self.assertEqual(self.get("/blog/missing/").status_code, 404)

    def test_every_guide_links_to_two_other_public_guides(self):
        guide_paths = {f"/blog/{slug}/" for slug in ARTICLES}
        for path in sorted(guide_paths):
            with self.subTest(path=path):
                response = self.get(path)
                self.assertContains(response, "Related guides")
                html = PublicHTML(response)
                related_links = {link for link in html.anchors if link.startswith("/blog/") and link != "/blog/"}
                self.assertEqual(len(related_links), 2)
                self.assertTrue(related_links <= guide_paths - {path})

    def test_landing_links_to_signup_and_each_visible_section(self):
        response = self.get("/")
        html = PublicHTML(response)
        self.assertIn("/signup/", html.anchors)
        self.assertContains(response, f"free for {TRIAL_PERIOD_DAYS} days")
        for fragment in ("features", "how-it-works", "pricing", "faq", "account-requirements"):
            with self.subTest(fragment=fragment):
                self.assertIn(f"#{fragment}", html.anchors)
                self.assertContains(response, f'id="{fragment}"', count=1)

    def test_homepage_and_pricing_render_product_and_breadcrumb_schema(self):
        home = PublicHTML(self.get("/"))
        self.assertEqual(home.structured_data.get("Organization", {}).get("name"), "Postzyo")
        self.assertEqual(home.structured_data["SoftwareApplication"]["url"], self.origin + "/")
        pricing = PublicHTML(self.get("/pricing/"))
        crumbs = pricing.structured_data["BreadcrumbList"]["itemListElement"]
        self.assertEqual([crumb["item"] for crumb in crumbs], [self.origin + "/", self.origin + "/pricing/"])
        self.assertIn("/", pricing.anchors)

    def test_pricing_shows_trial_and_current_checkout_prices_in_html(self):
        response = self.get("/pricing/")
        self.assertContains(response, "INR 6,000 / month")
        self.assertContains(response, "INR 70,000 / year")
        self.assertContains(response, "3-day free trial")
        self.assertIn("/signup/", PublicHTML(response).anchors)

    def test_sitemap_lists_only_the_canonical_public_pages_with_dates(self):
        response = self.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/xml")
        namespace = {"site": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        sitemap = ElementTree.fromstring(response.content)
        locations = set()
        for url in sitemap.findall("site:url", namespace):
            locations.add(url.findtext("site:loc", namespaces=namespace))
            date.fromisoformat(url.findtext("site:lastmod", namespaces=namespace))
        self.assertEqual(locations, {self.origin + path for path in self.paths})

    def test_robots_advertises_sitemap_and_allows_auth_noindex_to_be_read(self):
        response = self.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
        self.assertIn(b"User-agent: *", response.content)
        self.assertIn(b"Sitemap: https://postzyo.com/sitemap.xml", response.content)
        for path in ("/login/", "/signup/"):
            with self.subTest(path=path):
                self.assertNotIn(f"Disallow: {path}".encode(), response.content)
                html = PublicHTML(self.get(path))
                self.assertIn("noindex", html.metadata.get("robots", ""))
                self.assertEqual(html.canonicals, [])

    def test_dashboard_still_requires_login_for_anonymous_https_visitors(self):
        response = self.get("/dashboard/")
        self.assertRedirects(response, "/login/?next=/dashboard/", fetch_redirect_response=False)

    @override_settings(SECURE_SSL_REDIRECT=True, SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"))
    def test_https_forwarding_does_not_redirect_back_to_the_same_public_url(self):
        response = self.client.get("/pricing/", HTTP_HOST="postzyo.com", HTTP_X_FORWARDED_PROTO="https")
        self.assertEqual(response.status_code, 200)

    def test_local_development_host_still_serves_public_routes(self):
        response = self.client.get("/", HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PublicHTML(response).canonicals, [self.origin + "/"])
