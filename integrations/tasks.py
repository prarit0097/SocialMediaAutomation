import logging

from celery import shared_task
from django.contrib.auth import get_user_model
from django.core.cache import cache

from .models import MetaUserToken
from .services import TOKEN_HEALTH_CACHE_KEY, resync_all_page_tokens
from .sync_state import SYNC_STATUS_TTL, resync_cache_key

logger = logging.getLogger("integrations.tasks")


@shared_task(bind=True, max_retries=2, default_retry_delay=600)
def resync_page_tokens_task(self, user_id: int, generation: str | None = None):
    """Re-mint fresh per-page Meta tokens for every one of a user's granted assets.

    Enqueued after a reconnect so Business-Manager pages (which /me/accounts does
    not return) get their stale per-page tokens rewritten and stop failing with
    OAuth code 190. Reads the freshly-persisted user token from MetaUserToken.
    """
    credential = MetaUserToken.objects.filter(user_id=user_id).first()
    if not credential or not credential.access_token:
        logger.warning("resync_page_tokens_task: no stored user token for user_id=%s", user_id)
        return {"status": "no_token", "user_id": user_id}

    user = get_user_model().objects.filter(id=user_id).first()
    if not user:
        return {"status": "no_user", "user_id": user_id}
    if generation is not None and generation != str(credential.generation):
        return {"status": "superseded", "user_id": user_id}

    stats = resync_all_page_tokens(user, credential.access_token, generation=credential.generation)
    if stats.get("superseded"):
        return {"status": "superseded", "user_id": user_id}

    # Fresh tokens changed the active-account set / row data — drop stale caches so
    # the Accounts UI reflects the resync immediately.
    cache.delete_many([
        f"accounts_list_v1:{user_id}", f"meta_pages_catalog:{user_id}", f"{TOKEN_HEALTH_CACHE_KEY}:{user_id}",
    ])

    deferred = stats["deferred"] or stats["skipped_budget"]
    partial = deferred or stats["errors"] or stats["unresolved"] or stats.get("inventory_status") == "partial"
    retries = self.request.retries
    status = "partial" if partial else "done"
    if deferred and retries < self.max_retries:
        status = "retrying"
    cache.set(
        resync_cache_key(user_id, credential.generation),
        {"status": status, **stats},
        timeout=SYNC_STATUS_TTL,
    )
    if status == "retrying":
        raise self.retry(
            args=(user_id, str(credential.generation)),
            kwargs={},
            countdown=self.default_retry_delay * 2**retries,
        )

    logger.info("resync_page_tokens_task user_id=%s status=%s stats=%s", user_id, status, stats)
    return {"status": status, "user_id": user_id, "retries": retries, **stats}
