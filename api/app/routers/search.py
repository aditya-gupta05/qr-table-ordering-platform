"""GET /api/search: read-only retrieval debug endpoint (per-stage scores + timings)."""

from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.db import engine
from app.retrieval.config import Filters, RetrievalConfig
from app.retrieval.ingest import load_chunk_configs
from app.retrieval.parse import SOURCE_TYPES
from app.retrieval.search import retrieve
from app.routers.menu import parse_csv
from app.vocab import ALLERGENS, DIETARY_TAGS

router = APIRouter(prefix="/api", tags=["search"])
HIT_TEXT_CHARS = 500


class HitOut(BaseModel):
    kind: Literal["menu_item", "chunk"]
    id: int
    menu_item_id: int | None
    document_id: int | None
    chunk_index: int | None
    text: str
    score: float
    ranks: dict[str, int]
    scores: dict[str, float]
    meta: dict


class SearchOut(BaseModel):
    query: str
    mode: str
    config_name: str
    cache_hit: bool | None
    timings_ms: dict[str, float]
    hits: list[HitOut]


@lru_cache
def _chunk_configs() -> tuple[dict, str]:
    cfgs, default = load_chunk_configs()
    return cfgs, default


@router.get("/search", response_model=SearchOut)
async def search(
    q: str = Query(..., min_length=1, max_length=1000),
    mode: Literal["vector", "fts", "hybrid"] = "hybrid",
    k: int = Query(5, ge=1, le=50),
    rerank: bool = False,
    exclude_allergens: str | None = Query(None, description="comma-separated allergens to avoid"),
    dietary: str | None = Query(None, description="comma-separated tags; item must have all"),
    category: str | None = Query(None, description="comma-separated menu category names"),
    source_type: str | None = Query(None, description="comma-separated document source types"),
    scope: str = Query("menu,docs", description="comma-separated: menu, docs"),
    config: str | None = Query(None, description="chunk config name; default from chunking.toml"),
):
    avoid = parse_csv(exclude_allergens, ALLERGENS, "allergen")
    need = parse_csv(dietary, DIETARY_TAGS, "dietary")
    sources = parse_csv(source_type, SOURCE_TYPES, "source_type")
    scopes = set(parse_csv(scope, ("menu", "docs"), "scope")) or {"menu", "docs"}
    cats = [c.strip() for c in category.split(",") if c.strip()] if category else []
    cfgs, default = _chunk_configs()
    if config is not None and config not in cfgs:
        raise HTTPException(
            422,
            detail={"message": "unknown config", "invalid": [config], "allowed": sorted(cfgs)},
        )
    rcfg = RetrievalConfig(
        mode=mode, k=k, rerank=rerank, chunk_config=config or default, candidate_k=max(50, k)
    )
    filters = Filters(
        exclude_allergens=tuple(avoid),
        dietary=tuple(need),
        categories=tuple(cats),
        source_types=tuple(sources),
    )
    async with engine.connect() as conn:
        res = await retrieve(conn, q, filters, rcfg, scopes)
    return SearchOut(
        query=q,
        mode=res.mode,
        config_name=res.config_name,
        cache_hit=res.cache_hit,
        timings_ms=res.timings_ms,
        hits=[
            HitOut(
                kind=h.kind,
                id=h.id,
                menu_item_id=h.menu_item_id,
                document_id=h.document_id,
                chunk_index=h.chunk_index,
                text=h.text[:HIT_TEXT_CHARS],
                score=h.score,
                ranks=h.ranks,
                scores=h.scores,
                meta=h.meta,
            )
            for h in res.hits
        ],
    )
