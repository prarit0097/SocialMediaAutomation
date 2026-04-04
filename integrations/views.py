import logging
import secrets
import re
from datetime import datetime, timedelta, timezone as dt_timezone

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.conf import settings
from django.db.models import Max
from django.db.utils import OperationalError
from django.utils import timezone
from django.views.decorators.http import require_GET

from analytics.models import InsightSnapshot
from core.constants import FACEBOOK, INSTAGRAM
from core.exceptions import MetaAPIError
from core.services.meta_client import MetaClient
from core.throttle import throttle_per_user
from publishing.models import ScheduledPost

from .models import ConnectedAccount, MetaUserToken
from .services import upsert_connected_accounts
from .sync_state import SYNC_CACHE_KEY_TEMPLATE, build_account_sync_state

logger = logging.getLogger("integrations")
TOKEN_HEALTH_CACHE_KEY = "meta_token_health_summary_v1"
META_USER_ACCESS_TOKEN_TTL = 60 * 60 * 24 * 30
META_USER_SESSION_TOKEN_KEY = "meta_user_access_token"


def _persist_user_access_token(user_id: int | None, token: str) -> None:
    normalized = str(token or "").strip()
    if not user_id or not normalized:
        return

    cache.set(f"meta_user_access_token:{user_id}", normalized, timeout=META_USER_ACCESS_TOKEN_TTL)
    try:
        MetaUserToken.objects.update_or_create(
            user_id=user_id,
            defaults={"access_token": normalized},
        )
    except OperationalError:
        logger.warning("Failed to persist Meta user token for user_id=%s due to database lock", user_id)


def _parse_snapshot_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt_timezone.utc)
    if isinstance(value, str):
        normalized = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value.strip())
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_timezone.utc)
    return None


def _latest_published_post_times(account_ids: list[int]) -> dict[int, datetime | None]:
    latest_by_account: dict[int, datetime | None] = {
        row["account_id"]: row["latest_published_at"]
        for row in ScheduledPost.objects.filter(account_id__in=account_ids, published_at__isnull=False)
        .values("account_id")
        .annotate(latest_published_at=Max("published_at"))
    }

    unresolved = set(account_ids)
    # Check more snapshots per account so that an empty recent snapshot
    # doesn't hide valid post data from an older snapshot.
    snapshots = (
        InsightSnapshot.objects.filter(account_id__in=account_ids)
        .order_by("account_id", "-fetched_at")[:len(account_ids) * 4]
    )
    for snapshot in snapshots:
        account_id = snapshot.account_id
        if account_id not in unresolved:
            continue
        payload = snapshot.payload or {}
        posts = payload.get("published_posts") or []
        latest_post = None
        for post in posts:
            published_at = _parse_snapshot_datetime(post.get("published_at")) or _parse_snapshot_datetime(post.get("scheduled_for"))
            if not published_at:
                continue
            if latest_post is None or published_at > latest_post:
                latest_post = published_at
        if latest_post is not None:
            current = latest_by_account.get(account_id)
            latest_by_account[account_id] = latest_post if current is None or latest_post > current else current
            # Only mark as resolved when we actually found a post time.
            # Empty snapshots (e.g. from failed Meta fetch) should not
            # prevent checking older snapshots that may have data.
            unresolved.discard(account_id)
        if not unresolved:
            break
    return latest_by_account


def _deactivate_disconnected_accounts(user, pages: list[dict]) -> None:
    if not pages:
        return

    active_fb_page_ids = {str(page.get("id")) for page in pages if page.get("id")}
    active_ig_user_ids = {
        str((page.get("instagram_business_account") or {}).get("id"))
        for page in pages
        if (page.get("instagram_business_account") or {}).get("id")
    }

    ConnectedAccount.objects.filter(is_active=True, user=user, platform=FACEBOOK).exclude(
        page_id__in=active_fb_page_ids
    ).update(is_active=False, token_expires_at=None)

    ConnectedAccount.objects.filter(is_active=True, user=user, platform=INSTAGRAM).exclude(
        page_id__in=active_ig_user_ids
    ).update(is_active=False, token_expires_at=None)


def _lookup_catalog_target(client: MetaClient, target_id: str, access_token: str) -> tuple[str, dict]:
    facebook_error = None
    try:
        facebook_data = client._get(
            f"/{target_id}",
            {
                "access_token": access_token,
                "fields": "id,name,access_token,picture,instagram_business_account",
            },
        )
        if any(key in facebook_data for key in ("name", "access_token", "picture", "instagram_business_account")):
            return FACEBOOK, facebook_data
    except MetaAPIError as exc:
        facebook_error = exc

    try:
        instagram_data = client._get(
            f"/{target_id}",
            {
                "access_token": access_token,
                "fields": "id,username,profile_picture_url",
            },
        )
        if any(key in instagram_data for key in ("username", "profile_picture_url")):
            return INSTAGRAM, instagram_data
        raise MetaAPIError("Catalog target did not expose recognizable Facebook or Instagram fields.")
    except MetaAPIError as exc:
        raise exc from facebook_error


def _resolve_user_access_token(request: HttpRequest, user_id: int | None) -> str:
    session_token = str(request.session.get(META_USER_SESSION_TOKEN_KEY) or "").strip()
    if session_token:
        _persist_user_access_token(user_id, session_token)
        return session_token

    if user_id:
        cached_user_token = str(cache.get(f"meta_user_access_token:{user_id}") or "").strip()
        if cached_user_token:
            _persist_user_access_token(user_id, cached_user_token)
            return cached_user_token

        db_token = (
            MetaUserToken.objects.filter(user_id=user_id)
            .values_list("access_token", flat=True)
            .first()
        )
        db_token = str(db_token or "").strip()
        if db_token:
            cache.set(f"meta_user_access_token:{user_id}", db_token, timeout=META_USER_ACCESS_TOKEN_TTL)
            return db_token

    return ""


@require_GET
@login_required
def meta_start(request: HttpRequest) -> JsonResponse:
    state = secrets.token_urlsafe(24)
    cache.set(f"meta_oauth_state:{state}", {"user_id": request.user.id}, timeout=600)
    redirect_uri = settings.META_REDIRECT_URI

    client = MetaClient()
    return JsonResponse({"auth_url": client.oauth_url(state, redirect_uri=redirect_uri)})


@require_GET
@login_required
def meta_callback(request: HttpRequest) -> HttpResponse:
    oauth_error = request.GET.get("error")
    if oauth_error:
        description = request.GET.get("error_description") or request.GET.get("error_reason") or oauth_error
        return JsonResponse({"error": "Meta OAuth failed", "details": description}, status=400)

    code = request.GET.get("code")
    state = request.GET.get("state")
    state_data = cache.get(f"meta_oauth_state:{state}") if state else None

    if not code or not state or not state_data:
        return JsonResponse({"error": "Invalid OAuth callback parameters"}, status=400)

    redirect_uri = settings.META_REDIRECT_URI
    cache.delete(f"meta_oauth_state:{state}")
    user_id = state_data.get("user_id") if isinstance(state_data, dict) else None
    if not user_id or int(user_id) != int(request.user.id):
        return JsonResponse({"error": "OAuth callback does not match the current session"}, status=403)

    client = MetaClient()
    token_data = client.exchange_code_for_token(code, redirect_uri=redirect_uri)
    pages = client.get_managed_pages(token_data["access_token"])
    upsert_connected_accounts(pages, request.user)
    _deactivate_disconnected_accounts(request.user, pages)
    cache.delete(f"{TOKEN_HEALTH_CACHE_KEY}:{request.user.id}")

    target_ids_count = None
    sync_warning = None
    if pages:
        try:
            debug_data = client.debug_token(pages[0]["access_token"]).get("data", {})
            target_ids: set[str] = set()
            for scope_item in (debug_data.get("granular_scopes") or []):
                for target_id in (scope_item.get("target_ids") or []):
                    target_ids.add(str(target_id))
            target_ids_count = len(target_ids)
            if target_ids_count and len(pages) < target_ids_count:
                sync_warning = (
                    "Meta returned fewer pages than token target_ids. Reconnect and allow access to all pages."
                )
        except MetaAPIError:
            target_ids_count = None

    if user_id:
        _persist_user_access_token(user_id, token_data["access_token"])
        cache.set(
            SYNC_CACHE_KEY_TEMPLATE.format(user_id=user_id),
            {
                "meta_pages_synced": len(pages),
                "facebook_connected_total": ConnectedAccount.objects.filter(user_id=user_id, is_active=True, platform="facebook")
                .count(),
                "instagram_connected_total": ConnectedAccount.objects.filter(user_id=user_id, is_active=True, platform="instagram")
                .count(),
                "token_target_ids_count": target_ids_count,
                "warning": sync_warning,
                "synced_at": timezone.now().isoformat(),
            },
            timeout=60 * 60 * 12,
        )
        cache.delete(f"meta_pages_catalog:{user_id}")
        cache.delete(f"accounts_list_v1:{user_id}")

    request.session[META_USER_SESSION_TOKEN_KEY] = token_data["access_token"]
    request.session.modified = True

    logger.info("Meta accounts connected. total_pages=%s", len(pages))
    return redirect("dashboard:accounts")


@require_GET
@login_required
@throttle_per_user("30/m", scope="list_accounts")
def list_accounts(request: HttpRequest) -> JsonResponse:
    force_refresh = request.GET.get("refresh") == "1"
    user_id = getattr(request.user, "id", None)
    cache_key = f"accounts_list_v1:{user_id}"
    if not force_refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            return JsonResponse(cached, safe=False)

    account_rows = list(
        ConnectedAccount.objects.filter(is_active=True, user=request.user).values(
            "id",
            "platform",
            "page_id",
            "page_name",
            "ig_user_id",
            "is_active",
            "created_at",
            "updated_at",
        )
    )
    last_post_map = _latest_published_post_times([row["id"] for row in account_rows])
    stale_cutoff = timezone.now() - timedelta(hours=24)
    rows = []
    for row in account_rows:
        last_post = last_post_map.get(row["id"])
        account = ConnectedAccount(
            id=row["id"],
            platform=row["platform"],
            page_id=row["page_id"],
            page_name=row["page_name"],
            ig_user_id=row["ig_user_id"],
            is_active=row["is_active"],
            access_token="",
            updated_at=row["updated_at"],
        )
        sync_state = build_account_sync_state(account, user_id)
        rows.append(
            {
                **row,
                "last_post_at": (last_post.isoformat() if last_post else None),
                "last_post_is_stale": (last_post is None) or (last_post < stale_cutoff),
                **sync_state,
            }
        )
    cache.set(cache_key, rows, timeout=getattr(settings, "ACCOUNTS_LIST_CACHE_TTL", 20))
    return JsonResponse(rows, safe=False)


@require_GET
@login_required
def accounts_sync_status(request: HttpRequest) -> JsonResponse:
    data = cache.get(f"meta_last_sync:{request.user.id}") or {}
    fb_total = ConnectedAccount.objects.filter(is_active=True, user=request.user, platform="facebook").count()
    ig_total = ConnectedAccount.objects.filter(is_active=True, user=request.user, platform="instagram").count()
    latest_updated = (
        ConnectedAccount.objects.filter(is_active=True, user=request.user).aggregate(latest=Max("updated_at")).get("latest")
    )
    data = {
        "meta_pages_synced": data.get("meta_pages_synced") or fb_total,
        "facebook_connected_total": data.get("facebook_connected_total") or fb_total,
        "instagram_connected_total": data.get("instagram_connected_total") or ig_total,
        "token_target_ids_count": data.get("token_target_ids_count") or None,
        "warning": data.get("warning"),
        "synced_at": data.get("synced_at") or (latest_updated.isoformat() if latest_updated else None),
    }
    return JsonResponse(data)


@require_GET
@login_required
@throttle_per_user("10/m", scope="meta_pages_catalog")
def meta_pages_catalog(request: HttpRequest) -> JsonResponse:
    force_refresh = request.GET.get("refresh") == "1"
    cache_key = f"meta_pages_catalog:{request.user.id}"
    cached = None if force_refresh else cache.get(cache_key)
    if cached:
        return JsonResponse(cached)

    accounts = list(ConnectedAccount.objects.filter(is_active=True, user=request.user).order_by("-updated_at"))
    if not accounts:
        payload = {"total_pages": 0, "connected_pages": 0, "rows": []}
        cache.set(cache_key, payload, timeout=300)
        return JsonResponse(payload)

    seed_account = next((a for a in accounts if a.platform == "facebook"), accounts[0])
    connected_ids = {str(a.page_id) for a in accounts}
    user_access_token = _resolve_user_access_token(request, getattr(request.user, "id", None))
    app_access_token = f"{settings.META_APP_ID}|{settings.META_APP_SECRET}"
    client = MetaClient()

    rows: list[dict] = []
    seen_ids: set[str] = set()
    fb_by_ig_id = {
        str(a.ig_user_id): a
        for a in ConnectedAccount.objects.filter(platform="facebook", is_active=True, user=request.user)
        if a.ig_user_id and a.access_token
    }

    for account in accounts:
        page_id = str(account.page_id)
        if page_id in seen_ids:
            continue
        seen_ids.add(page_id)
        rows.append(
            {
                "page_id": page_id,
                "page_name": account.page_name,
                "status": "connected",
                "connectability": "connected",
                "reason": "Page access token is synced in app.",
                "platform": account.platform,
                "ig_user_id": account.ig_user_id,
                "profile_picture_url": None,
            }
        )

    try:
        debug_data = client.debug_token(seed_account.access_token).get("data", {})
        target_ids: list[str] = []
        for scope_item in (debug_data.get("granular_scopes") or []):
            for target_id in (scope_item.get("target_ids") or []):
                sid = str(target_id)
                if sid not in target_ids:
                    target_ids.append(sid)

        for target_id in target_ids:
            if target_id in seen_ids:
                continue
            page_name = None
            platform = INSTAGRAM if target_id in fb_by_ig_id else FACEBOOK
            reason = "Asset is visible in token target_ids but not returned by /me/accounts."
            connectability = "not_connectable"
            profile_picture_url = None
            try:
                detail_token = user_access_token
                if not detail_token:
                    raise MetaAPIError(
                        "User access token is unavailable. Reconnect to refresh catalog detail access token."
                    )
                platform, page_data = _lookup_catalog_target(client, target_id, detail_token)
                if platform == INSTAGRAM:
                    username = page_data.get("username")
                    page_name = f"{username} (IG)" if username else None
                    profile_picture_url = page_data.get("profile_picture_url")
                    if username:
                        linked_fb = fb_by_ig_id.get(target_id)
                        if linked_fb:
                            try:
                                ConnectedAccount.objects.update_or_create(
                                    user=request.user,
                                    platform="instagram",
                                    page_id=target_id,
                                    defaults={
                                        "page_name": page_name or f"{linked_fb.page_name} (IG)",
                                        "ig_user_id": target_id,
                                        "access_token": linked_fb.access_token,
                                        "is_active": True,
                                    },
                                )
                                reason = "Instagram profile is linked and has been synced in app."
                                connectability = "connected"
                            except OperationalError:
                                reason = (
                                    "Instagram profile is connectable but app database is busy. "
                                    "Retry refresh in a few seconds."
                                )
                                connectability = "connectable"
                        else:
                            reason = (
                                "Instagram business account is visible but not connected in app. "
                                "Reconnect to sync it."
                            )
                            connectability = "connectable"
                else:
                    page_name = page_data.get("name")
                    picture_data = page_data.get("picture") or {}
                    profile_picture_url = (picture_data.get("data") or {}).get("url")
                    if page_data.get("access_token"):
                        # Auto-sync connectable Facebook pages discovered in token target_ids.
                        try:
                            ig_id = (page_data.get("instagram_business_account") or {}).get("id")
                            ConnectedAccount.objects.update_or_create(
                                user=request.user,
                                platform="facebook",
                                page_id=target_id,
                                defaults={
                                    "page_name": page_name or "(name unavailable)",
                                    "access_token": page_data.get("access_token"),
                                    "ig_user_id": ig_id,
                                    "is_active": True,
                                },
                            )
                            if ig_id:
                                fb_by_ig_id[str(ig_id)] = ConnectedAccount(
                                    user=request.user,
                                    platform="facebook",
                                    page_id=target_id,
                                    page_name=page_name or "(name unavailable)",
                                    access_token=page_data.get("access_token"),
                                    ig_user_id=ig_id,
                                )
                            reason = "Page token was available from page node and has been synced in app."
                            connectability = "connected"
                        except OperationalError:
                            reason = (
                                "Page token is available but app database is busy. "
                                "Retry refresh in a few seconds."
                            )
                            connectability = "connectable"
                    else:
                        reason = (
                            "Meta did not return page access token for this page. "
                            "Check page admin/task access and Business Integration page selection."
                        )
                        connectability = "not_connectable"
            except MetaAPIError:
                # Retry with app access token for best-effort name lookup on public assets.
                try:
                    platform, page_data = _lookup_catalog_target(client, target_id, app_access_token)
                    if platform == INSTAGRAM:
                        username = page_data.get("username")
                        page_name = f"{username} (IG)" if username else page_name
                        profile_picture_url = page_data.get("profile_picture_url") or profile_picture_url
                    else:
                        page_data = client._get(
                            f"/{target_id}",
                            {
                                "access_token": app_access_token,
                                "fields": "id,name,picture",
                            },
                        )
                        page_name = page_data.get("name") or page_name
                        picture_data = page_data.get("picture") or {}
                        profile_picture_url = (picture_data.get("data") or {}).get("url") or profile_picture_url
                except MetaAPIError:
                    pass

                if not user_access_token:
                    if page_name:
                        reason = (
                            "Name resolved with limited lookup, but user token is missing for full catalog access. "
                            "Click Connect Facebook + Instagram and Refresh List."
                        )
                    else:
                        reason = (
                            "Catalog details need a fresh user token. "
                            "Click Connect Facebook + Instagram, allow all required pages/profiles, then Refresh List."
                        )
                    connectability = "connectable"
                elif page_name:
                    reason = (
                        "Name resolved via limited lookup, but page token is unavailable for full access. "
                        "Grant admin/full control and reconnect in Business Integration."
                    )
                    connectability = "not_connectable"
                else:
                    if platform == INSTAGRAM:
                        reason = (
                            "Unable to read Instagram profile details with current token. "
                            "Check IG business linking, app permissions, and Business Integration selection."
                        )
                    else:
                        reason = (
                            "Unable to read page details with current token. "
                            "Check that this user has admin/full control on this page."
                        )
                    connectability = "not_connectable"

            status = "connected" if connectability == "connected" else "catalog-only"
            rows.append(
                {
                    "page_id": target_id,
                    "page_name": page_name or "(name unavailable)",
                    "status": status,
                    "connectability": connectability,
                    "reason": reason,
                    "platform": platform,
                    "profile_picture_url": profile_picture_url,
                }
            )
            seen_ids.add(target_id)
    except MetaAPIError:
        pass

    rows.sort(key=lambda r: (0 if r["status"] == "connected" else 1, (r.get("page_name") or "").lower()))
    payload = {
        "total_pages": len(rows),
        "connected_pages": sum(1 for r in rows if r.get("status") == "connected"),
        "rows": rows,
    }
    cache.set(cache_key, payload, timeout=300)
    return JsonResponse(payload)
