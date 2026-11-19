import logging
import secrets
import re
from datetime import datetime, timedelta, timezone as dt_timezone
from uuid import uuid4

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.conf import settings
from django.db.models import Max
from django.db import transaction
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
from accounts.pixel import queue_pixel_event
from core.fields import decrypt_text, encrypt_text
from .services import TOKEN_HEALTH_CACHE_KEY, apply_meta_grants, read_meta_grants, upsert_connected_accounts
from .sync_state import SYNC_CACHE_KEY_TEMPLATE, SYNC_STATUS_TTL, build_account_sync_state, get_recent_sync_time, resync_cache_key

logger = logging.getLogger("integrations")
META_USER_ACCESS_TOKEN_TTL = 60 * 60 * 24 * 30
META_USER_SESSION_TOKEN_KEY = "meta_user_access_token"


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

    if not account_ids:
        return latest_by_account

    # Only inspect each account's MOST RECENT snapshot, not its whole history. Loading
    # every snapshot's JSON payload for the entire fleet (~30 per account) was the bulk
    # of this endpoint's latency and a memory spike. The latest daily snapshot already
    # carries the account's recent posts. Stream with an iterator so peak memory stays
    # bounded even across many accounts.
    latest_ids = list(
        InsightSnapshot.objects.filter(account_id__in=account_ids)
        .values("account_id")
        .annotate(mid=Max("id"))
        .values_list("mid", flat=True)
    )
    snapshots = (
        InsightSnapshot.objects.filter(id__in=latest_ids).only("account_id", "payload").iterator(chunk_size=25)
    )
    for snapshot in snapshots:
        posts = (snapshot.payload or {}).get("published_posts") or []
        latest_post = None
        for post in posts:
            published_at = _parse_snapshot_datetime(post.get("published_at")) or _parse_snapshot_datetime(post.get("scheduled_for"))
            if not published_at:
                continue
            if latest_post is None or published_at > latest_post:
                latest_post = published_at
        if latest_post is not None:
            current = latest_by_account.get(snapshot.account_id)
            if current is None or latest_post > current:
                latest_by_account[snapshot.account_id] = latest_post
    return latest_by_account


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


def _resolve_user_access_token(request: HttpRequest, user_id: int | None) -> MetaUserToken | None:
    if not user_id:
        return None
    stored = MetaUserToken.objects.filter(user_id=user_id).first()
    if stored is not None:
        return stored
    # Older sessions may predate persistent tokens. Import one only if no newer token exists.
    token = request.session.get(META_USER_SESSION_TOKEN_KEY) or cache.get(f"meta_user_access_token:{user_id}")
    if not token:
        return None
    stored, _ = MetaUserToken.objects.get_or_create(
        user_id=user_id, defaults={"access_token": decrypt_text(token)},
    )
    return stored


def _sync_catalog_account(credential: MetaUserToken, platform: str, page_id: str, defaults: dict, linked_fb=None):
    with transaction.atomic():
        if not MetaUserToken.objects.select_for_update().filter(
            user_id=credential.user_id, generation=credential.generation,
        ).first():
            return None
        if linked_fb is not None and not ConnectedAccount.objects.select_for_update().filter(
            pk=linked_fb.pk, user_id=credential.user_id, is_active=True, token_generation=linked_fb.token_generation,
        ).first():
            return None

        sync = cache.get(SYNC_CACHE_KEY_TEMPLATE.format(user_id=credential.user_id)) or {}
        if sync.get("generation") and sync["generation"] != str(credential.generation):
            sync = {}
        resync = cache.get(resync_cache_key(credential.user_id, credential.generation)) or {}
        missing = resync.get("missing_permissions", sync.get("missing_permissions", []))
        if platform == INSTAGRAM and "instagram_basic" in missing:
            return None
        publish_scope = "pages_manage_posts" if platform == FACEBOOK else "instagram_content_publish"
        if publish_scope in missing:
            defaults["publishing_permission_granted"] = False
        account, _ = ConnectedAccount.objects.update_or_create(
            user_id=credential.user_id, platform=platform, page_id=page_id,
            defaults={**defaults, "token_expires_at": None},
        )
        return account


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
    # Exchange the short-lived (~1h) user token for a long-lived (~60d) one. Page access
    # tokens derived from a short-lived user token also expire in ~1h, which silently
    # breaks scheduled publishing run later; long-lived page tokens do not. Fall back to
    # the short-lived token if the exchange call fails.
    user_access_token = token_data.get("access_token")
    try:
        long_lived = client.exchange_user_token_for_long_lived(user_access_token)
        if long_lived.get("access_token"):
            user_access_token = long_lived["access_token"]
    except Exception as exc:  # noqa: BLE001 - best-effort upgrade; fall back to short-lived
        logger.warning("long-lived token exchange failed user_id=%s error=%s", request.user.id, exc)

    if not user_access_token:
        return JsonResponse({"error": "Meta did not return an access token. Reconnect to try again."}, status=502)

    pages_complete = False
    try:
        pages = client.get_managed_pages(user_access_token)
        pages_complete = True
    except MetaAPIError:
        pages = []
    grants = read_meta_grants(client, user_access_token)
    target_ids = set().union(*grants.targets.values())
    generation = uuid4()
    from .tasks import resync_page_tokens_task

    def queue_resync():
        try:
            resync_page_tokens_task.delay(user_id, str(generation))
        except Exception:  # noqa: BLE001 - keep the completed direct reconnect usable
            cache.set(resync_cache_key(user_id, generation), {"status": "partial"}, timeout=SYNC_STATUS_TTL)
            logger.warning("token resync enqueue failed user_id=%s", user_id)

    with transaction.atomic():
        MetaUserToken.objects.update_or_create(
            user_id=user_id, defaults={"access_token": user_access_token, "generation": generation},
        )
        upsert_connected_accounts(pages, request.user, grants)
        response_is_complete = apply_meta_grants(request.user, pages, grants, pages_complete=pages_complete)
        previous_sync = cache.get(SYNC_CACHE_KEY_TEMPLATE.format(user_id=user_id)) or {}
        cache.set(
            SYNC_CACHE_KEY_TEMPLATE.format(user_id=user_id),
            {
                "meta_pages_synced": len(pages),
                "token_target_ids_count": len(target_ids) if grants.scopes is not None else None,
                "missing_permissions": grants.missing_permissions,
                "inventory_status": "complete" if response_is_complete else "partial",
                "synced_at": timezone.now().isoformat() if response_is_complete else previous_sync.get("synced_at"),
                "generation": str(generation),
            },
            timeout=SYNC_STATUS_TTL,
        )
        cache.set(resync_cache_key(user_id, generation), {"status": "pending"}, timeout=SYNC_STATUS_TTL)
        cache.delete_many([
            f"meta_user_access_token:{user_id}", f"meta_pages_catalog:{user_id}", f"accounts_list_v1:{user_id}",
            f"{TOKEN_HEALTH_CACHE_KEY}:{user_id}",
        ])
        transaction.on_commit(queue_resync)

    # Encrypt at rest in the session — with the cache-backed session engine this token
    # would otherwise sit in plaintext in shared Redis, unlike its encrypted DB/cache copies.
    request.session[META_USER_SESSION_TOKEN_KEY] = encrypt_text(user_access_token)
    request.session.modified = True

    logger.info("Meta accounts connected. total_pages=%s", len(pages))
    queue_pixel_event(
        request,
        "MetaAccountConnected",
        {
            "facebook_pages": ConnectedAccount.objects.filter(user=request.user, is_active=True, platform=FACEBOOK).count(),
            "instagram_accounts": ConnectedAccount.objects.filter(user=request.user, is_active=True, platform=INSTAGRAM).count(),
        },
        custom=True,
    )
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
            "profile_picture_url",
            "is_active",
            "created_at",
            "updated_at",
            "token_expires_at",
        )
    )
    last_post_map = _latest_published_post_times([row["id"] for row in account_rows])
    stale_cutoff = timezone.now() - timedelta(hours=24)
    # Resolve once: recent_sync_time is the same for every account, so computing it inside
    # the loop was one redundant cache/DB hit per account (N+1) — the bulk of this view's
    # latency for users with many accounts.
    recent_sync_time = get_recent_sync_time(user_id)
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
            token_expires_at=row["token_expires_at"],
        )
        sync_state = build_account_sync_state(account, user_id, recent_sync_time=recent_sync_time)
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
    generation = MetaUserToken.objects.filter(user=request.user).values_list("generation", flat=True).first()
    if data.get("generation") and data["generation"] != str(generation):
        data = {}
    resync = (cache.get(resync_cache_key(request.user.id, generation)) or {}) if generation else {}
    fb_total = ConnectedAccount.objects.filter(is_active=True, user=request.user, platform="facebook").count()
    ig_total = ConnectedAccount.objects.filter(is_active=True, user=request.user, platform="instagram").count()
    latest_updated = (
        ConnectedAccount.objects.filter(is_active=True, user=request.user).aggregate(latest=Max("updated_at")).get("latest")
    )
    synced = data.get("meta_pages_synced")
    inventory_status = resync.get("inventory_status") or data.get("inventory_status")
    missing_permissions = resync.get("missing_permissions", data.get("missing_permissions", []))
    warnings = []
    if inventory_status == "partial":
        warnings.append("Meta did not verify the full profile inventory. Profiles with unverified access were kept connected.")
    if missing_permissions:
        warnings.append(f"Meta permissions missing: {', '.join(missing_permissions)}.")
    if resync.get("status") == "retrying":
        warnings.append("Meta token resync is incomplete. Another attempt is queued.")
    elif resync.get("status") == "partial":
        warnings.append("Some profile tokens could not be refreshed. Reconnect or check Meta profile permissions.")
    data = {
        "meta_pages_synced": synced if synced is not None else fb_total,
        "facebook_connected_total": data.get("facebook_connected_total", fb_total),
        "instagram_connected_total": data.get("instagram_connected_total", ig_total),
        "token_target_ids_count": data.get("token_target_ids_count"),
        "missing_permissions": missing_permissions,
        "inventory_status": inventory_status,
        "resync_status": resync.get("status"),
        "warning": " ".join(warnings) or data.get("warning"),
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
    catalog_ttl = getattr(settings, "META_PAGES_CATALOG_CACHE_TTL", 3600)
    if not accounts:
        payload = {"total_pages": 0, "connected_pages": 0, "rows": []}
        cache.set(cache_key, payload, timeout=catalog_ttl)
        return JsonResponse(payload)

    seed_account = next((a for a in accounts if a.platform == "facebook"), accounts[0])
    connected_ids = {str(a.page_id) for a in accounts}
    credential = _resolve_user_access_token(request, request.user.id)
    user_access_token = credential.access_token if credential else ""
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

        live_lookups = 0
        max_lookups = int(getattr(settings, "MAX_CATALOG_DETAIL_LOOKUPS", 25) or 25)
        for target_id in target_ids:
            if target_id in seen_ids:
                continue
            page_name = None
            platform = INSTAGRAM if target_id in fb_by_ig_id else FACEBOOK
            reason = "Asset is visible in token target_ids but not returned by /me/accounts."
            connectability = "not_connectable"
            profile_picture_url = None
            # Bound live Meta detail lookups per request — a token granting hundreds of
            # pages would otherwise make hundreds of blocking round-trips (worker/gateway
            # timeout + wasted rate budget). Remaining assets list as catalog-only.
            if live_lookups >= max_lookups:
                rows.append({
                    "page_id": target_id,
                    "page_name": "(refresh to resolve)",
                    "status": "catalog-only",
                    "connectability": "catalog-only",
                    "reason": "Not resolved this round (catalog lookup cap reached). Refresh again to resolve more pages.",
                    "platform": platform,
                    "profile_picture_url": None,
                })
                seen_ids.add(target_id)
                continue
            live_lookups += 1
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
                                synced = _sync_catalog_account(
                                    credential=credential,
                                    platform=INSTAGRAM,
                                    page_id=target_id,
                                    linked_fb=linked_fb,
                                    defaults={
                                        "page_name": page_name or f"{linked_fb.page_name} (IG)",
                                        "ig_user_id": target_id,
                                        "access_token": linked_fb.access_token,
                                        "is_active": True,
                                    },
                                )
                                if synced:
                                    reason = "Instagram profile is linked and has been synced in app."
                                    connectability = "connected"
                                else:
                                    reason = "Meta could not confirm this profile for the current connection. Refresh again."
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
                            synced = _sync_catalog_account(
                                credential=credential,
                                platform=FACEBOOK,
                                page_id=target_id,
                                defaults={
                                    "page_name": page_name or "(name unavailable)",
                                    "access_token": page_data.get("access_token"),
                                    "ig_user_id": ig_id,
                                    "is_active": True,
                                },
                            )
                            if synced:
                                if ig_id:
                                    fb_by_ig_id[str(ig_id)] = synced
                                reason = "Page token was available from page node and has been synced in app."
                                connectability = "connected"
                            else:
                                reason = "Meta could not confirm this page for the current connection. Refresh again."
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
    except MetaAPIError as exc:
        logger.warning("meta_pages_catalog: debug_token or target lookup failed user=%s error=%s", request.user.id, exc)

    rows.sort(key=lambda r: (0 if r["status"] == "connected" else 1, (r.get("page_name") or "").lower()))
    payload = {
        "total_pages": len(rows),
        "connected_pages": sum(1 for r in rows if r.get("status") == "connected"),
        "rows": rows,
    }
    cache.set(cache_key, payload, timeout=catalog_ttl)
    return JsonResponse(payload)
