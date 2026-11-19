import logging

from dataclasses import dataclass, field

from django.core.cache import cache
from django.db import transaction

from core.constants import FACEBOOK, INSTAGRAM, META_SCOPES
from core.exceptions import MetaAPIError, MetaTransientError
from .models import ConnectedAccount, MetaUserToken

logger = logging.getLogger("integrations.services")

TOKEN_HEALTH_CACHE_KEY = "meta_token_health_summary_v1"


def deactivate_if_current_oauth_failure(account: ConnectedAccount, error: MetaAPIError) -> None:
    error_data = error.payload.get("error", {})
    if not isinstance(error_data, dict) or str(error_data.get("code")) != "190":
        return
    changed = ConnectedAccount.objects.filter(
        pk=account.pk, user_id=account.user_id, is_active=True, token_generation=account.token_generation,
    ).update(is_active=False, token_expires_at=None)
    if changed:
        cache.delete_many([f"accounts_list_v1:{account.user_id}", f"{TOKEN_HEALTH_CACHE_KEY}:{account.user_id}"])
        logger.warning("deactivated account_id=%s after Meta rejected its current token", account.pk)


def _facebook_picture_url(page: dict) -> str:
    return ((page.get("picture") or {}).get("data") or {}).get("url") or ""


@dataclass
class MetaGrants:
    scopes: set[str] | None = None
    targets: dict[str, set[str]] = field(default_factory=dict)
    retryable: bool = False

    def inventory(self, scope: str) -> set[str] | None:
        if self.scopes is not None and scope not in self.scopes:
            return set()
        return self.targets.get(scope)

    @property
    def missing_permissions(self) -> list[str]:
        return sorted(set(META_SCOPES) - self.scopes) if self.scopes is not None else []

    def can_publish(self, platform: str, *target_ids: str) -> bool | None:
        if self.scopes is None:
            return None
        scope = "pages_manage_posts" if platform == FACEBOOK else "instagram_content_publish"
        if scope not in self.scopes:
            return False
        targets = self.targets.get(scope)
        return targets is None or not targets.isdisjoint(target_ids)


def read_meta_grants(client, user_access_token: str) -> MetaGrants:
    try:
        payload = client.debug_token(user_access_token)
    except MetaTransientError:
        return MetaGrants(retryable=True)
    except MetaAPIError:
        return MetaGrants()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return MetaGrants(retryable=True)
    scopes = data.get("scopes")
    if data.get("is_valid") is not True or not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
        return MetaGrants(retryable=data.get("is_valid") is not False)

    grants = MetaGrants(scopes=set(scopes))
    granular_scopes = data.get("granular_scopes")
    if isinstance(granular_scopes, list):
        for item in granular_scopes:
            if not isinstance(item, dict) or not isinstance(item.get("scope"), str):
                continue
            ids = item.get("target_ids")
            if isinstance(ids, list) and all(type(value) in (str, int) and str(value).strip() for value in ids):
                grants.targets.setdefault(item["scope"], set()).update(str(tid) for tid in ids)
    return grants


def upsert_connected_accounts(pages: list[dict], user, grants: MetaGrants | None = None) -> None:
    ig_grants = grants.inventory("instagram_basic") if grants is not None else None
    with transaction.atomic():
        for page in pages:
            if not page.get("id") or not page.get("access_token"):
                continue
            ig_data = page.get("instagram_business_account") or {}
            page_id = str(page["id"])
            fb_defaults = {
                "page_name": page.get("name") or "(name unavailable)",
                "ig_user_id": ig_data.get("id"),
                "access_token": page["access_token"],
                "is_active": True,
                "token_expires_at": None,
            }
            if grants is not None and grants.scopes is not None:
                fb_defaults["publishing_permission_granted"] = grants.can_publish(FACEBOOK, page_id)
            fb_picture = _facebook_picture_url(page)
            if fb_picture:
                fb_defaults["profile_picture_url"] = fb_picture
            ConnectedAccount.objects.update_or_create(
                user=user,
                platform=FACEBOOK,
                page_id=page_id,
                defaults=fb_defaults,
            )

            ig_id = str(ig_data["id"]) if ig_data.get("id") else None
            if ig_id and (ig_grants is None or ig_id in ig_grants or page_id in ig_grants):
                ig_defaults = {
                    "page_name": f"{fb_defaults['page_name']} (IG)",
                    "ig_user_id": ig_id,
                    "access_token": page["access_token"],
                    "is_active": True,
                    "token_expires_at": None,
                }
                if grants is not None and grants.scopes is not None:
                    ig_defaults["publishing_permission_granted"] = grants.can_publish(INSTAGRAM, page_id, ig_id)
                ig_picture = ig_data.get("profile_picture_url") or ""
                if ig_picture:
                    ig_defaults["profile_picture_url"] = ig_picture
                ConnectedAccount.objects.update_or_create(
                    user=user,
                    platform=INSTAGRAM,
                    page_id=ig_id,
                    defaults=ig_defaults,
                )


def apply_meta_grants(user, pages: list[dict], grants: MetaGrants, pages_complete: bool) -> bool:
    """Apply positively known grant changes and return whether the inventory was complete."""
    accounts = ConnectedAccount.objects.filter(user=user, is_active=True)
    fb_pages = {str(page["id"]) for page in pages if page.get("id")}
    fb_grants = grants.inventory("pages_show_list")
    ig_grants = grants.inventory("instagram_basic")
    if fb_grants is not None:
        fb_pages.update(fb_grants)

    linked_ig = dict(
        accounts.filter(platform=FACEBOOK, page_id__in=fb_pages, ig_user_id__isnull=False)
        .exclude(ig_user_id="").values_list("page_id", "ig_user_id")
    )

    fb_complete = pages_complete and fb_grants is not None
    if fb_complete:
        accounts.filter(platform=FACEBOOK).exclude(page_id__in=fb_pages).update(is_active=False, token_expires_at=None)

    ig_complete = pages_complete and ig_grants is not None
    if grants.scopes is not None and "instagram_basic" not in grants.scopes:
        accounts.filter(platform=INSTAGRAM).update(is_active=False, token_expires_at=None)
    elif ig_complete:
        # Instagram granular scopes may list the parent Page ID. Preserve linked
        # IG profiles when Meta has not resolved every granted parent Page yet.
        known_ig = set(accounts.filter(platform=INSTAGRAM).values_list("page_id", flat=True)) | set(linked_ig.values())
        unresolved = ig_grants - set(linked_ig) - known_ig
        if unresolved:
            ig_complete = False
        else:
            allowed_ig = ig_grants | {ig_id for page_id, ig_id in linked_ig.items() if page_id in ig_grants}
            accounts.filter(platform=INSTAGRAM).exclude(page_id__in=allowed_ig).update(
                is_active=False, token_expires_at=None,
            )

    if grants.scopes is not None:
        for platform, scope in ((FACEBOOK, "pages_manage_posts"), (INSTAGRAM, "instagram_content_publish")):
            platform_accounts = accounts.filter(platform=platform)
            targets = grants.inventory(scope)
            if targets is None:
                platform_accounts.exclude(publishing_permission_granted=True).update(publishing_permission_granted=True)
                continue
            allowed = targets
            if platform == INSTAGRAM:
                allowed = targets | {ig_id for page_id, ig_id in linked_ig.items() if page_id in targets}
            platform_accounts.filter(page_id__in=allowed).exclude(publishing_permission_granted=True).update(
                publishing_permission_granted=True,
            )
            denied = platform_accounts.exclude(page_id__in=allowed)
            if platform == INSTAGRAM and not ig_complete and scope in grants.scopes:
                denied = denied.filter(page_id__in=linked_ig.values())
            denied.exclude(publishing_permission_granted=False).update(publishing_permission_granted=False)

    return fb_complete and ig_complete

# FB-page-safe field set only: requesting IG-only fields (username/profile_picture_url)
# on a Page node errors the whole Graph batch, so we keep this to Page fields.
_ASSET_FIELDS = "id,name,access_token,picture,instagram_business_account{id}"


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
            for target_id in chunk:
                node = data.get(target_id) if isinstance(data, dict) else None
                if not isinstance(node, dict):
                    stats["deferred"] += 1
                elif node.get("id"):
                    assets[target_id] = node
                elif isinstance(node.get("error"), dict):
                    error = node["error"]
                    if error.get("is_transient") or str(error.get("code")) in {"2", "4", "17", "32", "613", "80004"}:
                        stats["deferred"] += 1
                    else:
                        stats["errors"] += 1
                else:
                    stats["deferred"] += 1
        except MetaTransientError:
            stats["deferred"] += len(chunk)
        except MetaAPIError:
            # A single bad id can fail the whole batch — fall back to per-id lookups.
            logger.warning("resync: batch of %s failed, retrying per-id", len(chunk))
            for tid in chunk:
                if meta_app_over_budget():
                    stats["skipped_budget"] += 1
                    continue
                try:
                    node = client._get(f"/{tid}", {"access_token": user_access_token, "fields": _ASSET_FIELDS})
                    if isinstance(node, dict) and node.get("id"):
                        assets[str(tid)] = node
                    else:
                        stats["deferred"] += 1
                except MetaTransientError:
                    stats["deferred"] += 1
                except MetaAPIError:
                    stats["errors"] += 1
    return assets


def resync_all_page_tokens(user, user_access_token: str, client=None, stdout=None, *, generation=None) -> dict:
    """Re-mint fresh per-page tokens for ALL of a user's granted Meta assets.

    Apply grant changes and tokens only if the reconnect generation is still current.
    """
    from core.services.meta_client import MetaClient, meta_app_over_budget

    client = client or MetaClient()
    stats = {
        "pages_direct": 0,
        "resynced_fb": 0,
        "resynced_ig": 0,
        "unresolved": 0,
        "skipped_budget": 0,
        "errors": 0,
        "deferred": 0,
    }

    def _log(msg: str) -> None:
        logger.info("resync[user=%s]: %s", getattr(user, "id", None), msg)
        if stdout is not None:
            stdout.write(msg)

    if not user_access_token:
        _log("no user access token — cannot resync")
        return stats
    if meta_app_over_budget():
        stats["skipped_budget"] = 1
        return stats

    pages_complete = False
    try:
        pages = client.get_managed_pages(user_access_token)
        pages_complete = True
    except MetaTransientError:
        stats["deferred"] += 1
        pages = []
    except MetaAPIError:
        stats["errors"] += 1
        pages = []
    stats["pages_direct"] = len(pages)
    _log(f"/me/accounts returned {len(pages)} directly-managed page(s)")

    if meta_app_over_budget():
        stats["skipped_budget"] += 1
        grants = MetaGrants()
        remaining_fb = []
    else:
        grants = read_meta_grants(client, user_access_token)
        if grants.retryable:
            stats["deferred"] += 1
        target_ids = grants.inventory("pages_show_list")
        if target_ids is None:
            # Without enumerated grants, refresh existing active Business Manager Pages.
            target_ids = set(ConnectedAccount.objects.filter(
                user=user, platform=FACEBOOK, is_active=True,
            ).values_list("page_id", flat=True))
        covered = {str(page["id"]) for page in pages if page.get("id") and page.get("access_token")}
        remaining_fb = sorted(target_ids - covered)

    assets = _batch_fetch_assets(client, remaining_fb, user_access_token, stats)
    fresh_assets = [node for tid, node in assets.items() if str(node["id"]) == tid and node.get("access_token")]
    stats["unresolved"] += len(assets) - len(fresh_assets)

    with transaction.atomic():
        if generation is not None and not MetaUserToken.objects.select_for_update().filter(
            user=user, generation=generation,
        ).first():
            return {**stats, "superseded": True}
        upsert_connected_accounts(pages + fresh_assets, user, grants)
        complete = apply_meta_grants(user, pages + fresh_assets, grants, pages_complete=pages_complete)
        stats["inventory_status"] = "complete" if complete else "partial"
        stats["missing_permissions"] = grants.missing_permissions
    stats["resynced_fb"] = len(fresh_assets)
    if grants.scopes is None or "instagram_basic" in grants.scopes:
        stats["resynced_ig"] = sum(
            bool((node.get("instagram_business_account") or {}).get("id")) for node in fresh_assets
        )

    _log(f"done: {stats}")
    return stats
