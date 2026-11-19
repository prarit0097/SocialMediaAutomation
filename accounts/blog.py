from django.http import Http404
from django.shortcuts import render

from .articles import ARTICLES


def blog_index(request):
    return render(request, "accounts/blog_index.html", {"articles": ARTICLES.items()})


def blog_article(request, slug):
    article = ARTICLES.get(slug)
    if article is None:
        raise Http404("Guide not found")
    return render(request, "accounts/blog_article.html", {"article": article})
