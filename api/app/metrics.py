from prometheus_client import Counter, Histogram

ORDERS_CREATED = Counter("orders_created_total", "Orders created (excludes idempotent replays)")
ORDER_STATUS_CHANGES = Counter("order_status_changes_total", "Order status transitions", ["to"])
ORDER_CREATE_SECONDS = Histogram(
    "order_create_seconds",
    "Time to price, insert and commit a new order",
    buckets=(0.002, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1),
)
EVENT_PUBLISH_SECONDS = Histogram(
    "order_event_publish_seconds",
    "Time to PUBLISH an order event to Redis",
    buckets=(0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.5),
)
EVENT_PUBLISH_FAILURES = Counter(
    "order_events_publish_failures_total", "Order events that failed to publish", ["channel"]
)
