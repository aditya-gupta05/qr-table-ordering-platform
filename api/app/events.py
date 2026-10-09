"""Order event publisher. Contract: docs/contracts/events.md.

Callers must publish only AFTER the DB transaction has committed. Publishing is best-effort:
failures are logged and counted but never propagate to the diner (the order is already durable
and the kitchen board recovers via GET /api/kitchen/orders).
"""

import json
import time

import redis.asyncio as aioredis
import structlog

from app.config import get_settings
from app.metrics import EVENT_PUBLISH_FAILURES, EVENT_PUBLISH_SECONDS
from app.schemas import OrderOut

log = structlog.get_logger("events")

CHANNEL_NEW = "orders.new"
CHANNEL_STATUS = "orders.status"

_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(get_settings().redis_url)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def build_envelope(event_type: str, order: OrderOut) -> dict:
    return {
        "type": event_type,
        "version": 1,
        "request_id": structlog.contextvars.get_contextvars().get("request_id"),
        "published_at_us": time.time_ns() // 1000,
        "order": order.model_dump(mode="json"),
    }


async def publish(channel: str, event_type: str, order: OrderOut) -> bool:
    """Publish an event; returns True on success. Never raises."""
    envelope = build_envelope(event_type, order)
    start = time.perf_counter()
    try:
        await get_redis().publish(channel, json.dumps(envelope, separators=(",", ":")))
        return True
    except Exception as exc:
        EVENT_PUBLISH_FAILURES.labels(channel).inc()
        log.error(
            "order_event_publish_failed",
            channel=channel,
            order_id=order.id,
            event_type=event_type,
            error=str(exc),
        )
        return False
    finally:
        EVENT_PUBLISH_SECONDS.observe(time.perf_counter() - start)
