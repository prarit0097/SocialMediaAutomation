from __future__ import annotations

from datetime import timedelta

from mcp.server.fastmcp import FastMCP

from .common import (
    bootstrap_django,
    hours_since,
    iso_or_none,
    latest_post_times_by_account,
    load_cached_snapshot_response,
    normalize_platform_filter,
    resolve_linked_account,
)


server = FastMCP(
    name="social-meta-insights",
    instructions=(
        "Use stored Django insight snapshots to summarize connected profile performance, "
        "flag stale profiles, detect posting gaps, and compare linked Facebook and Instagram accounts. "
        "Do not make live Meta API calls; rely on cached snapshots and application data."
    ),
)


def build_latest_snapshot_rows(limit: int = 20, platform: str | None = None) -> list[dict]:
    bootstrap_django()

    from django.utils import timezone
    from integrations.models import ConnectedAccount

    platform = normalize_platform_filter(platform)
    accounts = ConnectedAccount.objects.order_by("id")
    if platform:
        accounts = accounts.filter(platform=platform)

    now = timezone.now()
    last_post_map = latest_post_times_by_account(accounts.values_list("id", flat=True))
    rows = []
    for account in accounts:
        bundle = load_cached_snapshot_response(account)
        last_post_at = last_post_map.get(account.id)
        row = {
            "account_id": account.id,
            "platform": account.platform,
            "page_name": account.page_name,
            "page_id": account.page_id,
            "ig_user_id": account.ig_user_id,
            "last_post_at": iso_or_none(last_post_at),
            "last_post_hours_ago": hours_since(last_post_at, now),
            "has_snapshot": bundle is not None,
        }
        if bundle:
            payload = bundle.snapshot.payload or {}
            metadata = payload.get("metadata") or {}
            row.update(
                {
                    "snapshot_id": bundle.snapshot.id,
                    "fetched_at": iso_or_none(bundle.snapshot.fetched_at),
                    "fetched_hours_ago": hours_since(bundle.snapshot.fetched_at, now),
                    "collection_mode": metadata.get("collection_mode"),
                    "collection_source": metadata.get("collection_source"),
                    "collection_local_date": metadata.get("collection_local_date"),
                    "summary": bundle.response.get("summary") or {},
                    "published_posts_cached": len(payload.get("published_posts") or []),
                }
            )
        rows.append(row)

    rows.sort(key=lambda row: row.get("fetched_at") or "", reverse=True)
    return rows[: max(limit, 1)]


def find_stale_profile_rows(snapshot_age_hours: int = 24, post_gap_hours: int = 24, limit: int = 50) -> list[dict]:
    bootstrap_django()

    from django.utils import timezone
    from integrations.models import ConnectedAccount

    now = timezone.now()
    snapshot_cutoff = now - timedelta(hours=max(snapshot_age_hours, 1))
    post_cutoff = now - timedelta(hours=max(post_gap_hours, 1))
    accounts = list(ConnectedAccount.objects.order_by("platform", "page_name"))
    last_post_map = latest_post_times_by_account(account.id for account in accounts)

    rows = []
    for account in accounts:
        bundle = load_cached_snapshot_response(account)
        last_post_at = last_post_map.get(account.id)
        reasons = []
        if not bundle:
            reasons.append("missing_snapshot")
        elif bundle.snapshot.fetched_at < snapshot_cutoff:
            reasons.append("stale_snapshot")
        if not last_post_at:
            reasons.append("missing_post_history")
        elif last_post_at < post_cutoff:
            reasons.append("posting_gap")
        if not reasons:
            continue
        rows.append(
            {
                "account_id": account.id,
                "platform": account.platform,
                "page_name": account.page_name,
                "page_id": account.page_id,
                "snapshot_id": bundle.snapshot.id if bundle else None,
                "snapshot_fetched_at": iso_or_none(bundle.snapshot.fetched_at) if bundle else None,
                "snapshot_hours_ago": hours_since(bundle.snapshot.fetched_at, now) if bundle else None,
                "last_post_at": iso_or_none(last_post_at),
                "last_post_hours_ago": hours_since(last_post_at, now),
                "reasons": reasons,
            }
        )

    rows.sort(
        key=lambda row: (
            row["snapshot_hours_ago"] is None,
            -(row["snapshot_hours_ago"] or 0),
            row["last_post_hours_ago"] is None,
            -(row["last_post_hours_ago"] or 0),
        )
    )
    return rows[: max(limit, 1)]


def build_posting_gap_rows(min_gap_hours: int = 24, limit: int = 50) -> list[dict]:
    bootstrap_django()

    from django.utils import timezone
    from integrations.models import ConnectedAccount

    now = timezone.now()
    cutoff = now - timedelta(hours=max(min_gap_hours, 1))
    accounts = list(ConnectedAccount.objects.order_by("platform", "page_name"))
    last_post_map = latest_post_times_by_account(account.id for account in accounts)
    rows = []

    for account in accounts:
        last_post_at = last_post_map.get(account.id)
        if last_post_at and last_post_at >= cutoff:
            continue
        rows.append(
            {
                "account_id": account.id,
                "platform": account.platform,
                "page_name": account.page_name,
                "page_id": account.page_id,
                "last_post_at": iso_or_none(last_post_at),
                "gap_hours": hours_since(last_post_at, now),
                "status": "never_detected" if last_post_at is None else "gap_detected",
            }
        )

    rows.sort(key=lambda row: (row["gap_hours"] is None, -(row["gap_hours"] or 0)))
    return rows[: max(limit, 1)]


def build_fb_ig_comparison(account_id: int) -> dict:
    bootstrap_django()

    from analytics.views import _build_combined_response
    from integrations.models import ConnectedAccount

    account = ConnectedAccount.objects.filter(id=account_id).first()
    if not account:
        return {"error": "Connected account not found", "account_id": account_id}

    linked_account = resolve_linked_account(account)
    if not linked_account:
        return {
            "error": "No linked Facebook/Instagram counterpart found for this account",
            "account_id": account.id,
            "platform": account.platform,
            "page_name": account.page_name,
        }

    primary = load_cached_snapshot_response(account)
    secondary = load_cached_snapshot_response(linked_account)
    if not primary or not secondary:
        missing = []
        if not primary:
            missing.append(account.id)
        if not secondary:
            missing.append(linked_account.id)
        return {
            "error": "Missing cached insight snapshot for one or more linked accounts",
            "missing_account_ids": missing,
            "requested_account_id": account.id,
            "linked_account_id": linked_account.id,
        }

    combined = _build_combined_response(primary.response, secondary.response)
    return {
        "requested_account_id": account.id,
        "facebook_account_id": next((row["account_id"] for row in combined["accounts"] if row["platform"] == "facebook"), None),
        "instagram_account_id": next((row["account_id"] for row in combined["accounts"] if row["platform"] == "instagram"), None),
        "page_name": combined.get("page_name"),
        "summary": combined.get("summary"),
        "comparison_rows": combined.get("comparison_rows"),
        "snapshot_id": combined.get("snapshot_id"),
        "fetched_at": combined.get("fetched_at"),
        "published_posts_count": len(combined.get("published_posts") or []),
    }


@server.tool(description="Summarize the latest cached insight snapshot for connected profiles.")
def latest_snapshots_summary(limit: int = 20, platform: str = "all") -> dict:
    try:
        rows = build_latest_snapshot_rows(limit=limit, platform=platform)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    return {"count": len(rows), "rows": rows}


@server.tool(description="List profiles with stale snapshots or stale posting activity.")
def stale_profiles(snapshot_age_hours: int = 24, post_gap_hours: int = 24, limit: int = 50) -> dict:
    try:
        rows = find_stale_profile_rows(snapshot_age_hours=snapshot_age_hours, post_gap_hours=post_gap_hours, limit=limit)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    return {"count": len(rows), "rows": rows}


@server.tool(description="Detect profiles with a posting gap beyond the specified threshold.")
def posting_gaps(min_gap_hours: int = 24, limit: int = 50) -> dict:
    try:
        rows = build_posting_gap_rows(min_gap_hours=min_gap_hours, limit=limit)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    return {"count": len(rows), "rows": rows}


@server.tool(description="Build the cached Facebook versus Instagram comparison table for a linked profile.")
def fb_ig_comparison(account_id: int) -> dict:
    try:
        return build_fb_ig_comparison(account_id=account_id)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "account_id": account_id}


if __name__ == "__main__":
    server.run(transport="stdio")
