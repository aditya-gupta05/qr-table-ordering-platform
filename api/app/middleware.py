import time
import uuid

import structlog
from prometheus_client import Counter, Histogram
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = structlog.get_logger("http")

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route", "status"],
    buckets=(0.0025, 0.005, 0.0075, 0.01, 0.015, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)


class RequestContextMiddleware:
    """Pure ASGI: request-id binding, access log line, Prometheus metrics.

    Pure ASGI (not BaseHTTPMiddleware) so contextvars propagate and streaming isn't buffered.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope["headers"])
        request_id = headers.get(b"x-request-id", b"").decode() or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        status_code = 500
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - start
            # Route template is only known after routing; fall back to a constant for 404s.
            route = scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            if template != "/metrics":
                HTTP_REQUESTS.labels(scope["method"], template, str(status_code)).inc()
                HTTP_LATENCY.labels(scope["method"], template, str(status_code)).observe(elapsed)
            log.info(
                "http_request",
                method=scope["method"],
                path=scope["path"],
                status=status_code,
                duration_ms=round(elapsed * 1000, 2),
            )
            structlog.contextvars.clear_contextvars()
