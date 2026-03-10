from urllib.parse import urlencode

import requests
from django.conf import settings

from core.constants import META_SCOPES
from core.exceptions import MetaPermanentError, MetaTransientError


class MetaClient:
    def __init__(self):
        self.base_url = f"https://graph.facebook.com/{settings.META_GRAPH_VERSION}"

    def oauth_url(self, state: str) -> str:
        params = urlencode(
            {
                "client_id": settings.META_APP_ID,
                "redirect_uri": settings.META_REDIRECT_URI,
                "state": state,
                "scope": ",".join(META_SCOPES),
                "response_type": "code",
            }
        )
        return f"https://www.facebook.com/{settings.META_GRAPH_VERSION}/dialog/oauth?{params}"

    def exchange_code_for_token(self, code: str) -> dict:
        return self._get(
            "/oauth/access_token",
            {
                "client_id": settings.META_APP_ID,
                "client_secret": settings.META_APP_SECRET,
                "redirect_uri": settings.META_REDIRECT_URI,
                "code": code,
            },
        )

    def get_managed_pages(self, user_access_token: str) -> list[dict]:
        data = self._get(
            "/me/accounts",
            {
                "access_token": user_access_token,
                "fields": "id,name,access_token,instagram_business_account",
            },
        )
        return data.get("data", [])

    def publish_facebook_post(self, page_id: str, page_access_token: str, message: str) -> dict:
        return self._post(
            f"/{page_id}/feed",
            {
                "access_token": page_access_token,
                "message": message,
            },
        )

    def create_instagram_media(self, ig_user_id: str, page_access_token: str, image_url: str, caption: str) -> dict:
        return self._post(
            f"/{ig_user_id}/media",
            {
                "access_token": page_access_token,
                "image_url": image_url,
                "caption": caption,
            },
        )

    def publish_instagram_media(self, ig_user_id: str, page_access_token: str, creation_id: str) -> dict:
        return self._post(
            f"/{ig_user_id}/media_publish",
            {
                "access_token": page_access_token,
                "creation_id": creation_id,
            },
        )

    def fetch_facebook_insights(self, page_id: str, page_access_token: str) -> list[dict]:
        data = self._get(
            f"/{page_id}/insights",
            {
                "access_token": page_access_token,
                "metric": "page_impressions,page_engaged_users,page_post_engagements",
                "period": "day",
            },
        )
        return data.get("data", [])

    def fetch_instagram_insights(self, ig_user_id: str, page_access_token: str) -> list[dict]:
        data = self._get(
            f"/{ig_user_id}/insights",
            {
                "access_token": page_access_token,
                "metric": "impressions,reach,profile_views,website_clicks",
                "period": "day",
            },
        )
        return data.get("data", [])

    def debug_token(self, input_token: str) -> dict:
        app_access_token = f"{settings.META_APP_ID}|{settings.META_APP_SECRET}"
        return self._get(
            "/debug_token",
            {
                "input_token": input_token,
                "access_token": app_access_token,
            },
        )

    def _get(self, path: str, params: dict) -> dict:
        response = requests.get(f"{self.base_url}{path}", params=params, timeout=20)
        return self._handle_response(response)

    def _post(self, path: str, data: dict) -> dict:
        response = requests.post(f"{self.base_url}{path}", data=data, timeout=20)
        return self._handle_response(response)

    def _handle_response(self, response: requests.Response) -> dict:
        if response.status_code < 400:
            return response.json()

        payload = {}
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text}

        message = payload.get("error", {}).get("message", "Meta API request failed")
        if response.status_code >= 500 or response.status_code == 429:
            raise MetaTransientError(message, status_code=response.status_code, payload=payload)

        raise MetaPermanentError(message, status_code=response.status_code, payload=payload)
