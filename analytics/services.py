from core.constants import FACEBOOK
from core.services.meta_client import MetaClient
from integrations.models import ConnectedAccount

from .models import InsightSnapshot


def fetch_and_store_insights(account: ConnectedAccount) -> dict:
    client = MetaClient()

    if account.platform == FACEBOOK:
        insights = client.fetch_facebook_insights(account.page_id, account.access_token)
        platform = FACEBOOK
    else:
        insights = client.fetch_instagram_insights(account.ig_user_id or account.page_id, account.access_token)
        platform = "instagram"

    snapshot = InsightSnapshot.objects.create(
        account=account,
        platform=platform,
        payload={"insights": insights},
    )

    return {
        "platform": platform,
        "insights": insights,
        "snapshot_id": snapshot.id,
        "fetched_at": snapshot.fetched_at.isoformat(),
    }
