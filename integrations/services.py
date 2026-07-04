import logging

from django.db import transaction

from core.constants import FACEBOOK, INSTAGRAM
from core.exceptions import MetaAPIError
from .models import ConnectedAccount

logger = logging.getLogger("integrations.services")


def _facebook_picture_url(page: dict) -> str:
    return ((page.get("picture") or {}).get("data") or {}).get("url") or ""


def upsert_connected_accounts(pages: list[dict], user) -> None:
    with transaction.atomic():
        for page in pages:
            ig_data = page.get("instagram_business_account") or {}
            fb_defaults = {
                "page_name": page["name"],
                "ig_user_id": ig_data.get("id"),
                "access_token": page["access_token"],
                "is_active": True,
            }
            fb_picture = _facebook_picture_url(page)
            if fb_picture:
                fb_defaults["profile_picture_url"] = fb_picture
            ConnectedAccount.objects.update_or_create(
                user=user,
                platform=FACEBOOK,
                page_id=page["id"],
                defaults=fb_defaults,
            )

            ig_id = ig_data.get("id")
            if ig_id:
                ig_defaults = {
                    "page_name": f"{page['name']} (IG)",
                    "ig_user_id": ig_id,
                    "access_token": page["access_token"],
                    "is_active": True,
                }
                ig_picture = ig_data.get("profile_picture_url") or ""
                if ig_picture:
                    ig_defaults["profile_picture_url"] = ig_picture
                ConnectedAccount.objects.update_or_create(
                    user=user,
                    platform=INSTAGRAM,
                    page_id=ig_id,
                    defaults=ig_defaults,
                )


# --- Full page-token resync -------------------------------------------------
# /me/accounts only returns a user's DIRECTLY-managed pages. Business-Manager
# (catalog-discovered) pages are exposed only via the token's granular_scopes
# target_ids, and reconnect never rewrites their per-page access_token — so they
# keep pre-reconnect tokens that fail insight/publish with OAuth code 190. This
# walks the FULL granted asset set and re-mints every ConnectedAccount token from
# the current user token. It is additive: it never deactivates rows (a definitive
# 190 during the insight pull handles that), so a transient fetch gap here can't
# wrongly disable a live account.

# FB-page-safe field set only: requesting IG-only fields (username/profile_picture_url)
# on a Page node errors the whole Graph batch, so we keep this to Page fields.
_ASSET_FIELDS = "id,name,access_token,picture,instagram_business_account{id}"


def _collect_target_ids(client, pages: list[dict], user_access_token: str, user) -> list[str]:
    """All asset ids the token grants (from debug_token granular_scopes)."""
    seed_tokens = []
    if pages and pages[0].get("access_token"):
        seed_tokens.append(pages[0]["access_token"])
    if user_access_token:
        seed_tokens.append(user_access_token)

    ids: list[str] = []
    for token in seed_tokens:
        try:
            data = client.debug_token(token).get("data", {})
        except MetaAPIError:
            continue
        for scope_item in (data.get("granular_scopes") or []):
            for tid in (scope_item.get("target_ids") or []):
                sid = str(tid)
                if sid not in ids:
                    ids.append(sid)
        if ids:
            break

    # If debug_token gave nothing, fall back to the rows we already know about so
    # a scopes hiccup still lets us re-token existing accounts.
    if not ids:
        ids = [
            str(pid)
            for pid in ConnectedAccount.objects.filter(user=user).values_list("page_id", flat=True)
        ]
    return ids


def _batch_fetch_assets(client, target_ids: list[str], user_access_token: str, stats: dict) -> dict:
    """Fetch asset nodes in batches of 50 (quota-friendly) with per-id fallback.

    Stops early if Meta reports us over the app-usage budget so this resync can't
    re-trigger the app-level rate-limit exhaustion it is meant to recover from.
    """
    from core.services.meta_client import meta_app_over_budget

    assets: dict = {}
    for i in range(0, len(target_ids), 50):
        if meta_app_over_budget():
            stats["skipped_budget"] += len(target_ids) - i
            logger.warning("resync: Meta app over budget — stopped after %s/%s assets", i, len(target_ids))
            break
        chunk = target_ids[i:i + 50]
        try:
            data = client._get(
                "",
                {"ids": ",".join(chunk), "access_token": user_access_token, "fields": _ASSET_FIELDS},
            )
            if isinstance(data, dict):
                assets.update({str(k): v for k, v in data.items() if isinstance(v, dict)})
        except MetaAPIError as exc:
            # A single bad id can fail the whole batch — fall back to per-id lookups.
            logger.warning("resync: batch of %s failed, retrying per-id: %s", len(chunk), exc)
            for tid in chunk:
                if meta_app_over_budget():
                    stats["skipped_budget"] += 1
                    continue
                try:
                    node = client._get(f"/{tid}", {"access_token": user_access_token, "fields": _ASSET_FIELDS})
                    if isinstance(node, dict) and node.get("id"):
                        assets[str(tid)] = node
                except MetaAPIError:
                    stats["errors"] += 1
    return assets


def resync_all_page_tokens(user, user_access_token: str, client=None, stdout=None) -> dict:
    """Re-mint fresh per-page tokens for ALL of a user's granted Meta assets.

    Returns a stats dict. Never deactivates rows (safe to re-run any time).
    """
    from core.services.meta_client import MetaClient

    client = client or MetaClient()
    stats = {
        "pages_direct": 0,
        "resynced_fb": 0,
        "resynced_ig": 0,
        "unresolved": 0,
        "skipped_budget": 0,
        "errors": 0,
    }

    def _log(msg: str) -> None:
        logger.info("resync[user=%s]: %s", getattr(user, "id", None), msg)
        if stdout is not None:
            stdout.write(msg)

    if not user_access_token:
        _log("no user access token — cannot resync")
        return stats

    # 1. Directly-managed pages carry their fresh tokens straight from /me/accounts.
    pages = client.get_managed_pages(user_access_token)
    upsert_connected_accounts(pages, user)
    stats["pages_direct"] = len(pages)
    _log(f"/me/accounts returned {len(pages)} directly-managed page(s)")

    covered: set[str] = set()
    for p in pages:
        covered.add(str(p["id"]))
        ig = (p.get("instagram_business_account") or {}).get("id")
        if ig:
            covered.add(str(ig))

    # 2. Everything /me/accounts did NOT already refresh. Classify remaining ids into
    # Facebook pages vs Instagram accounts using the rows we already have: requesting
    # `access_token` on an IG node errors the whole Graph batch, so we only batch-fetch
    # the FB pages and derive each IG row from its linked FB page's fresh token.
    known_ig_ids = {
        str(pid)
        for pid in ConnectedAccount.objects.filter(user=user, platform=INSTAGRAM).values_list("page_id", flat=True)
    }
    target_ids = _collect_target_ids(client, pages, user_access_token, user)
    remaining = [tid for tid in target_ids if tid not in covered]
    remaining_fb = [tid for tid in remaining if tid not in known_ig_ids]
    _log(
        f"{len(target_ids)} granted asset(s); {len(remaining)} need resync "
        f"({len(remaining_fb)} FB page lookups)"
    )

    # 3. Batch-fetch fresh FB page tokens, then upsert each page AND its linked IG row
    # with that token (same convention as upsert_connected_accounts).
    assets = _batch_fetch_assets(client, remaining_fb, user_access_token, stats)
    for tid in remaining_fb:
        node = assets.get(tid)
        if not node or not node.get("access_token"):
            if node is not None:
                # Fetched a real node but got no page token (no admin role / dead) —
                # can't refresh. Left active; a live 190 later self-heals it.
                stats["unresolved"] += 1
            continue
        token = node["access_token"]
        ig_id = (node.get("instagram_business_account") or {}).get("id")
        fb_name = node.get("name") or "(name unavailable)"
        fb_defaults = {"page_name": fb_name, "access_token": token, "ig_user_id": ig_id, "is_active": True}
        pic = ((node.get("picture") or {}).get("data") or {}).get("url") or ""
        if pic:
            fb_defaults["profile_picture_url"] = pic
        ConnectedAccount.objects.update_or_create(
            user=user, platform=FACEBOOK, page_id=str(tid), defaults=fb_defaults,
        )
        stats["resynced_fb"] += 1

        if ig_id:
            ConnectedAccount.objects.update_or_create(
                user=user,
                platform=INSTAGRAM,
                page_id=str(ig_id),
                defaults={
                    "page_name": f"{fb_name} (IG)",
                    "ig_user_id": str(ig_id),
                    "access_token": token,
                    "is_active": True,
                },
            )
            stats["resynced_ig"] += 1

    _log(f"done: {stats}")
    return stats
