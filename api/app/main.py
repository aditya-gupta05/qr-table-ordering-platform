from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app import events
from app.config import get_settings
from app.db import engine
from app.logging import configure_logging
from app.middleware import RequestContextMiddleware
from app.routers import health, kitchen, menu, orders

configure_logging(get_settings().log_level)
log = structlog.get_logger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("api_startup")
    yield
    await events.close_redis()
    await engine.dispose()
    log.info("api_shutdown")


app = FastAPI(title="QR Table Ordering API", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)
app.include_router(health.router)
app.include_router(menu.router)
app.include_router(orders.router)
app.include_router(kitchen.router)
