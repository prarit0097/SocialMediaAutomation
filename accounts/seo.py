import json
from dataclasses import dataclass

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import reverse

from .articles import ARTICLES


@dataclass(frozen=True)
class PublicPage:
    label: str
    title: str
    description: str
    last_modified: str


# Update last_modified when a page's visible content changes substantially.
PUBLIC_PAGES = {
    "landing": PublicPage(
        "Home",
        "Facebook and Instagram Scheduling | Postzyo",
        "Plan, schedule and publish Facebook and Instagram posts, track performance, and get AI content recommendations in one Postzyo workspace.",
        "2026-11-19",
    ),
    "pricing": PublicPage(
        "Pricing",
        "Plans and Pricing | Postzyo",
        "Compare Postzyo’s monthly and annual plans for Facebook and Instagram scheduling, analytics and AI insights. Start with a free trial.",
        "2026-11-19",
    ),
    "help": PublicPage(
        "Help",
        "Help with Scheduling, Meta and Billing | Postzyo",
        "Find answers about connecting Meta accounts, scheduling posts and billing, or send a support request to Postzyo.",
        "2026-05-07",
    ),
    "blog_index": PublicPage(
        "Blog",
        "Social Media Planning and Scheduling Guides | Postzyo",
        "Practical guides for planning social content, scheduling Facebook and Instagram posts, and checking delivery.",
        "2026-11-19",
    ),
    "privacy_policy": PublicPage(
        "Privacy Policy",
        "Privacy Policy | Postzyo",
        "Read how Postzyo handles account data, Meta connections, scheduled content and analytics.",
        "2026-03-19",
    ),
    "terms": PublicPage(
        "Terms of Service",
        "Terms of Service | Postzyo",
        "Review Postzyo’s terms for workspace access, platform use, billing and subscriptions.",
        "2026-03-19",
    ),
    "data_deletion": PublicPage(
        "Data Deletion",
        "User Data Deletion | Postzyo",
        "Learn how to request deletion of your Postzyo account data and what to expect after submitting a request.",
        "2026-03-19",
    ),
}


def _canonical_url(route, **kwargs):
    return settings.SEO_CANONICAL_ORIGIN + reverse(route, kwargs=kwargs or None)


def public_metadata(request):
    match = request.resolver_match
    route = match.view_name if match else None
    article = ARTICLES.get(match.kwargs["slug"]) if route == "blog_article" else None
    page = PUBLIC_PAGES.get(route)
    if article:
        page = PublicPage(article.title, f"{article.title} | Postzyo", article.description, article.published)
    if page is None:
        return {}

    origin = settings.SEO_CANONICAL_ORIGIN
    home_url = _canonical_url("landing")
    canonical = _canonical_url(route, **match.kwargs)
    if route == "landing":
        organization_id = f"{home_url}#organization"
        graph = [
            {
                "@type": "Organization",
                "@id": organization_id,
                "name": "Postzyo",
                "url": home_url,
                "logo": origin + static("dashboard/brand/postzyo-mark.png"),
            },
            {
                "@type": "SoftwareApplication",
                "@id": f"{home_url}#app",
                "name": "Postzyo",
                "url": home_url,
                "description": page.description,
                "operatingSystem": "Web browser",
                "applicationCategory": "BusinessApplication",
                "publisher": {"@id": organization_id},
            },
        ]
    else:
        graph = [{
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home", "item": home_url},
                {"@type": "ListItem", "position": 2, "name": page.label, "item": canonical},
            ],
        }]

    if article:
        graph[0]["itemListElement"].insert(1, {
            "@type": "ListItem", "position": 2, "name": "Blog", "item": _canonical_url("blog_index"),
        })
        graph[0]["itemListElement"][-1]["position"] = 3
        graph.append({
            "@type": "BlogPosting",
            "headline": article.title,
            "description": article.description,
            "datePublished": article.published,
            "image": origin + static(article.image),
            "mainEntityOfPage": canonical,
            "author": {"@type": "Organization", "name": "Postzyo", "url": home_url},
        })

    # Escape markup before embedding the JSON as script text.
    json_ld = json.dumps({"@context": "https://schema.org", "@graph": graph}).replace("<", "\\u003c")
    return {
        "seo_page": page,
        "seo_type": "article" if article else "website",
        "seo_canonical": canonical,
        "seo_image": origin + static(
            article.image if article else (
                "dashboard/landing/scheduler-sharing.jpg" if route == "landing" else "dashboard/illustrations/dashboard-overview.png"
            )
        ),
        "seo_image_alt": article.image_alt if article else (
            "Postzyo’s Scheduler with a sample Facebook and Instagram post"
            if route == "landing" else "Postzyo workflow and dashboard preview"
        ),
        "seo_breadcrumb": route != "landing",
        "seo_json_ld": json_ld,
    }


def robots_txt(request):
    return HttpResponse(
        f"User-agent: *\nDisallow:\n\nSitemap: {_canonical_url('sitemap')}\n",
        content_type="text/plain; charset=utf-8",
    )


def sitemap_xml(request):
    pages = [(_canonical_url(route), page.last_modified) for route, page in PUBLIC_PAGES.items()]
    pages += [(_canonical_url("blog_article", slug=slug), article.published) for slug, article in ARTICLES.items()]
    return render(request, "sitemap.xml", {"pages": pages}, content_type="application/xml")
