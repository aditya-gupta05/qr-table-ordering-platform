import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app import events
from app.chat.service import close_chat_deps
from app.config import get_settings
from app.db import engine
from app.logging import configure_logging
from app.middleware import RequestContextMiddleware
from app.retrieval.settings import get_retrieval_settings
from app.routers import analytics, chat, health, kitchen, menu, orders, search, waiter

configure_logging(get_settings().log_level)
log = structlog.get_logger("app")


async def _warmup() -> None:
    """Per worker, in the background: loads the embedder and reranker ONNX sessions so the first
    real query does not pay for it. Startup is not blocked; failures are logged, not fatal."""
    from app.retrieval.config import RetrievalConfig
    from app.retrieval.embedder import get_embedder
    from app.retrieval.rerank import rerank_scores

    try:
        cfg = RetrievalConfig()
        emb = get_embedder(cfg.embed_model)
        await asyncio.to_thread(emb.embed_query, "warm up")
        await rerank_scores(get_retrieval_settings().rerank_model, "warm up", ["warm up"])
        log.info("retrieval_warmup_done")
    except Exception:
        log.exception("retrieval_warmup_failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("api_startup")
    warm = asyncio.create_task(_warmup()) if get_retrieval_settings().warmup else None
    yield
    if warm:
        warm.cancel()
    await close_chat_deps()
    await events.close_redis()
    await engine.dispose()
    log.info("api_shutdown")


app = FastAPI(title="QR Table Ordering API", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)
app.include_router(health.router)
app.include_router(menu.router)
app.include_router(orders.router)
app.include_router(kitchen.router)
app.include_router(search.router)
app.include_router(waiter.router)
app.include_router(analytics.router)
app.include_router(chat.router)
