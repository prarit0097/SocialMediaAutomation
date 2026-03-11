from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from core.media_views import serve_media

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", RedirectView.as_view(pattern_name="dashboard:home", permanent=False)),
    path("", include("accounts.urls")),
    path("auth/", include("integrations.auth_urls")),
    path("api/", include("integrations.api_urls")),
    path("api/", include("publishing.urls")),
    path("api/", include("analytics.urls")),
    path("dashboard/", include(("dashboard.urls", "dashboard"), namespace="dashboard")),
]

if settings.DEBUG:
    media_prefix = settings.MEDIA_URL.lstrip("/")
    urlpatterns += [
        path(f"{media_prefix}<path:path>", serve_media, name="serve_media"),
    ]
