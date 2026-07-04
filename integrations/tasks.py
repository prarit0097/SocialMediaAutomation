import logging

from celery import shared_task
from django.contrib.auth import get_user_model
from django.core.cache import cache

from .models import MetaUserToken
from .services import resync_all_page_tokens

logger = logging.getLogger("integrations.tasks")


@shared_task(bind=True, max_retries=2, default_retry_delay=600)
def resync_page_tokens_task(self, user_id: int):
    """Re-mint fresh per-page Meta tokens for every one of a user's granted assets.

    Enqueued after a reconnect so Business-Manager pages (which /me/accounts does
    not return) get their stale per-page tokens rewritten and stop failing with
    OAuth code 190. Reads the freshly-persisted user token from MetaUserToken.
    """
    token = (
        MetaUserToken.objects.filter(user_id=user_id)
        .values_list("access_token", flat=True)
        .first()
    )
    token = str(token or "").strip()
    if not token:
        logger.warning("resync_page_tokens_task: no stored user token for user_id=%s", user_id)
        return {"status": "no_token", "user_id": user_id}

    user = get_user_model().objects.filter(id=user_id).first()
    if not user:
        return {"status": "no_user", "user_id": user_id}

    stats = resync_all_page_tokens(user, token)

    # Fresh tokens changed the active-account set / row data — drop stale caches so
    # the Accounts UI reflects the resync immediately.
    cache.delete(f"accounts_list_v1:{user_id}")
    cache.delete(f"meta_pages_catalog:{user_id}")

    logger.info("resync_page_tokens_task done user_id=%s stats=%s", user_id, stats)
    return {"status": "done", "user_id": user_id, **stats}
