from __future__ import annotations

from datetime import timedelta

from mcp.server.fastmcp import FastMCP

from .common import bootstrap_django, today_daily_heavy_status


server = FastMCP(
    name="social-redis-celery",
    instructions=(
        "Inspect Redis, Celery workers, scheduled automations, and publishing pipeline health "
        "for the Social Media Automation Django project."
    ),
)


def build_redis_queue_status(max_keys: int = 20) -> dict:
    bootstrap_django()

    from django.conf import settings
    from redis import Redis

    client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    patterns = ["celery*", "unacked*", "*insight*", "*publish*"]
    seen_keys = set()
    queue_keys = []
    for pattern in patterns:
        for key in client.scan_iter(match=pattern):
            if key in seen_keys:
                continue
            seen_keys.add(key)
            queue_keys.append(key)
            if len(queue_keys) >= max(max_keys, 1):
                break
        if len(queue_keys) >= max(max_keys, 1):
            break

    rows = []
    for key in sorted(queue_keys):
        key_type = client.type(key)
        size = None
        if key_type == "list":
            size = client.llen(key)
        elif key_type == "zset":
            size = client.zcard(key)
        elif key_type == "set":
            size = client.scard(key)
        elif key_type == "hash":
            size = client.hlen(key)
        elif key_type == "stream":
            size = client.xlen(key)
        elif key_type == "string":
            size = client.strlen(key)
        rows.append({"key": key, "type": key_type, "size": size})

    return {
        "redis_url": settings.REDIS_URL,
        "ping": bool(client.ping()),
        "dbsize": client.dbsize(),
        "keys": rows,
    }


def build_celery_overview(timeout_seconds: int = 3) -> dict:
    bootstrap_django()

    from social_automation.celery import app as celery_app

    inspector = celery_app.control.inspect(timeout=max(timeout_seconds, 1))
    ping = inspector.ping() or {}
    active = inspector.active() or {}
    reserved = inspector.reserved() or {}
    scheduled = inspector.scheduled() or {}
    active_queues = inspector.active_queues() or {}

    return {
        "worker_count": len(ping),
        "reachable_workers": sorted(ping.keys()),
        "active_task_counts": {worker: len(tasks or []) for worker, tasks in active.items()},
        "reserved_task_counts": {worker: len(tasks or []) for worker, tasks in reserved.items()},
        "scheduled_task_counts": {worker: len(tasks or []) for worker, tasks in scheduled.items()},
        "active_queue_names": {
            worker: [queue.get("name") for queue in (queues or [])]
            for worker, queues in active_queues.items()
        },
    }


def build_publishing_pipeline_status(limit: int = 20) -> dict:
    bootstrap_django()

    from core.constants import POST_STATUS_FAILED, POST_STATUS_PENDING, POST_STATUS_PROCESSING, POST_STATUS_PUBLISHED
    from django.db.models import Count
    from django.utils import timezone
    from publishing.models import ScheduledPost

    now = timezone.now()
    processing_cutoff = now - timedelta(minutes=30)
    upcoming_cutoff = now + timedelta(hours=24)

    status_counts = {
        row["status"]: row["count"]
        for row in ScheduledPost.objects.values("status").order_by("status").annotate(count=Count("id"))
    }

    failed_rows = list(
        ScheduledPost.objects.select_related("account")
        .filter(status=POST_STATUS_FAILED)
        .order_by("-updated_at")[: max(limit, 1)]
    )
    failed_posts = [
        {
            "post_id": post.id,
            "account_id": post.account_id,
            "page_name": post.account.page_name,
            "platform": post.platform,
            "scheduled_for": post.scheduled_for.isoformat(),
            "updated_at": post.updated_at.isoformat(),
            "error_message": post.error_message,
        }
        for post in failed_rows
    ]

    return {
        "status_counts": {
            "pending": status_counts.get(POST_STATUS_PENDING, 0),
            "processing": status_counts.get(POST_STATUS_PROCESSING, 0),
            "published": status_counts.get(POST_STATUS_PUBLISHED, 0),
            "failed": status_counts.get(POST_STATUS_FAILED, 0),
        },
        "overdue_pending": ScheduledPost.objects.filter(status=POST_STATUS_PENDING, scheduled_for__lte=now).count(),
        "stuck_processing": ScheduledPost.objects.filter(status=POST_STATUS_PROCESSING, updated_at__lt=processing_cutoff).count(),
        "published_last_24h": ScheduledPost.objects.filter(
            status=POST_STATUS_PUBLISHED,
            published_at__gte=now - timedelta(hours=24),
        ).count(),
        "upcoming_next_24h": ScheduledPost.objects.filter(
            status=POST_STATUS_PENDING,
            scheduled_for__gt=now,
            scheduled_for__lte=upcoming_cutoff,
        ).count(),
        "failed_posts": failed_posts,
    }


@server.tool(description="Inspect Redis queue keys and sizes used by Celery and analytics jobs.")
def redis_queue_status(max_keys: int = 20) -> dict:
    try:
        return build_redis_queue_status(max_keys=max_keys)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


@server.tool(description="Inspect live Celery worker connectivity, active queues, and task counts.")
def celery_overview(timeout_seconds: int = 3) -> dict:
    try:
        return build_celery_overview(timeout_seconds=timeout_seconds)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


@server.tool(description="Summarize today's daily heavy insights automation progress.")
def daily_heavy_refresh_status() -> dict:
    try:
        return today_daily_heavy_status()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


@server.tool(description="Inspect scheduled publishing pipeline counts and recent failed jobs.")
def publishing_pipeline_status(limit: int = 20) -> dict:
    try:
        return build_publishing_pipeline_status(limit=limit)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


if __name__ == "__main__":
    server.run(transport="stdio")
