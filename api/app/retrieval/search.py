"""retrieve(): up to four ranked lists (menu vector, menu FTS, chunk vector, chunk FTS) fused with
RRF, optionally reranked by a cross-encoder.

Safety: every filter is a SQL WHERE clause on the live structured columns of `menu_items`. Document
chunks that mention a menu item (chunks.menu_item_ids) are dropped when that item violates the
filters. Similarity never decides whether something is allowed through.
"""

import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.retrieval.cache import QueryEmbedCache, embed_query_cached
from app.retrieval.config import Filters, RetrievalConfig, Scope
from app.retrieval.embedder import MODELS, Embedder, get_embedder, to_pgvector
from app.retrieval.fusion import rrf
from app.retrieval.metrics import RETRIEVAL_STAGE_SECONDS
from app.retrieval.rerank import rerank_scores
from app.retrieval.settings import get_retrieval_settings

_TOKEN = re.compile(r"[a-z0-9]+")
MAX_QUERY_CHARS = 1000
PLANNER_FLAGS = ("enable_indexscan", "enable_bitmapscan", "enable_seqscan", "enable_sort")


@dataclass
class Hit:
    kind: Literal["menu_item", "chunk"]
    id: int  # menu_items.id or chunks.id
    menu_item_id: int | None
    document_id: int | None
    chunk_index: int | None
    text: str
    score: float
    ranks: dict[str, int] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalResult:
    hits: list[Hit]
    timings_ms: dict[str, float]
    config_name: str
    mode: str
    cache_hit: bool | None = None


# --- SQL builders --------------------------------------------------------------------------------


def _item_clause(alias: str, f: Filters, params: dict) -> str:
    """Conditions an item row (`alias`) must satisfy. Empty string when no restriction."""
    parts = []
    if f.available_only:
        parts.append(f"{alias}.is_available")
    if f.exclude_allergens:
        parts.append(f"NOT ({alias}.allergens && CAST(:ex AS text[]))")
        params["ex"] = list(f.exclude_allergens)
    if f.dietary:
        parts.append(f"{alias}.dietary_tags @> CAST(:diet AS text[])")
        params["diet"] = list(f.dietary)
    return " AND ".join(parts)


def _violation_clause(f: Filters, params: dict) -> str:
    """True for a menu_items row `mi` that violates the filters (used to drop linked chunks)."""
    parts = []
    if f.available_only:
        parts.append("NOT mi.is_available")
    if f.exclude_allergens:
        parts.append("mi.allergens && CAST(:ex AS text[])")
        params["ex"] = list(f.exclude_allergens)
    if f.dietary:
        parts.append("NOT (mi.dietary_tags @> CAST(:diet AS text[]))")
        params["diet"] = list(f.dietary)
    return " OR ".join(parts)


def _menu_where(f: Filters, params: dict) -> str:
    conds = []
    c = _item_clause("m", f, params)
    if c:
        conds.append(c)
    if f.categories:
        conds.append("c.name = ANY(CAST(:cats AS text[]))")
        params["cats"] = list(f.categories)
    return (" AND " + " AND ".join(conds)) if conds else ""


def _chunk_where(f: Filters, params: dict) -> str:
    conds = []
    if f.source_types:
        conds.append("d.source_type = ANY(CAST(:st AS text[]))")
        params["st"] = list(f.source_types)
    if f.restricts_items:
        v = _violation_clause(f, params)
        if v:
            conds.append(
                f"NOT EXISTS (SELECT 1 FROM menu_items mi WHERE mi.id = ANY(ch.menu_item_ids) "
                f"AND ({v}))"
            )
    return (" AND " + " AND ".join(conds)) if conds else ""


def _model_literal(model: str) -> str:
    # Inlined (not bound) so the planner sees `model = '<const>'` and can use the partial HNSW
    # index; a bound parameter in a generic prepared plan would not match the index predicate.
    # Safe: only whitelisted names reach SQL.
    if model not in MODELS or "'" in model:
        raise ValueError(f"unsupported embedding model {model!r}")
    return "'" + model + "'"


def menu_vector_sql(cfg: RetrievalConfig, f: Filters) -> tuple[str, dict]:
    params: dict = {"q": "", "n": cfg.candidate_k, "model": cfg.embed_model}
    sql = (
        "SELECT m.id, m.name, m.description, m.price_cents, m.allergens, m.dietary_tags, "
        "m.spice_level, m.is_available, c.name AS category, "
        "e.embedding <=> CAST(:q AS vector) AS dist "
        "FROM menu_item_embeddings e JOIN menu_items m ON m.id = e.menu_item_id "
        "JOIN menu_categories c ON c.id = m.category_id "
        f"WHERE e.model = :model{_menu_where(f, params)} "
        "ORDER BY dist LIMIT :n"
    )
    return sql, params


def menu_fts_sql(cfg: RetrievalConfig, f: Filters) -> tuple[str, dict]:
    params: dict = {"tsq": "", "n": cfg.candidate_k}
    sql = (
        "SELECT m.id, m.name, m.description, m.price_cents, m.allergens, m.dietary_tags, "
        "m.spice_level, m.is_available, c.name AS category, ts_rank_cd(m.tsv, q.q) AS rank "
        "FROM menu_items m JOIN menu_categories c ON c.id = m.category_id, "
        "to_tsquery('english', :tsq) AS q(q) "
        f"WHERE m.tsv @@ q.q{_menu_where(f, params)} "
        "ORDER BY rank DESC, m.id LIMIT :n"
    )
    return sql, params


_CHUNK_COLS = (
    "ch.id, ch.document_id, ch.chunk_index, ch.content, ch.section_path, ch.page, "
    "ch.menu_item_ids, ch.token_count, d.slug, d.source_type"
)
_CFG_ID = "(SELECT id FROM chunk_configs WHERE name = :cc)"


def chunk_vector_sql(cfg: RetrievalConfig, f: Filters) -> tuple[str, dict]:
    params: dict = {"q": "", "n": cfg.candidate_k, "cc": cfg.chunk_config}
    lit = _model_literal(cfg.embed_model)
    inner = (
        f"SELECT {_CHUNK_COLS}, e.embedding <=> CAST(:q AS vector) AS dist "
        "FROM chunk_embeddings e JOIN chunks ch ON ch.id = e.chunk_id "
        "JOIN documents d ON d.id = ch.document_id "
        f"WHERE e.model = {lit} AND ch.chunk_config_id = {_CFG_ID}{_chunk_where(f, params)} "
        "ORDER BY e.embedding <=> CAST(:q AS vector) LIMIT :n"
    )
    # relaxed_order iterative scans can return rows slightly out of order: re-sort the survivors.
    # The leading comment keeps the exact and HNSW variants as DIFFERENT statement texts: asyncpg
    # caches prepared statements by text and Postgres switches a statement to a generic plan after 5
    # executions, so a seq-scan plan built under `exact` steering would otherwise be reused when the
    # same text runs under HNSW steering (found in bench/retrieval_index.py: fake 100% recall).
    return f"/* chunk_vec:{cfg.vector_index} */ SELECT * FROM ({inner}) x ORDER BY dist", params


def chunk_fts_sql(cfg: RetrievalConfig, f: Filters) -> tuple[str, dict]:
    params: dict = {"tsq": "", "n": cfg.candidate_k, "cc": cfg.chunk_config}
    sql = (
        f"SELECT {_CHUNK_COLS}, ts_rank_cd(ch.tsv, q.q) AS rank "
        "FROM chunks ch JOIN documents d ON d.id = ch.document_id, "
        "to_tsquery('english', :tsq) AS q(q) "
        f"WHERE ch.chunk_config_id = {_CFG_ID} AND ch.tsv @@ q.q{_chunk_where(f, params)} "
        "ORDER BY rank DESC, ch.id LIMIT :n"
    )
    return sql, params


def build_tsquery(query: str, operator: Literal["or", "and"]) -> str:
    """'is the fryer shared' -> 'is | the | fryer | shared'. Postgres drops stop words and stems.
    Tokens are [a-z0-9] only, so nothing can inject tsquery syntax."""
    toks = list(dict.fromkeys(_TOKEN.findall(query.lower())))[:32]
    return (" | " if operator == "or" else " & ").join(toks)


# --- execution -----------------------------------------------------------------------------------


@asynccontextmanager
async def _tx(conn: AsyncConnection) -> AsyncIterator[None]:
    """SET LOCAL needs a transaction. Join the caller's if there is one."""
    if conn.in_transaction():
        yield
    else:
        async with conn.begin():
            yield


async def _set_local(conn: AsyncConnection, settings: dict[str, str]) -> None:
    """One round trip for several SET LOCALs (set_config(..., is_local=true))."""
    names = list(settings)
    sql = "SELECT " + ", ".join(f"set_config(:n{i}, :v{i}, true)" for i in range(len(names)))
    params = {f"n{i}": n for i, n in enumerate(names)} | {
        f"v{i}": settings[n] for i, n in enumerate(names)
    }
    await conn.execute(text(sql), params)


def _menu_hit_text(r) -> str:
    return f"{r.name}. {r.description}"


def _row_menu_meta(r) -> dict:
    return {
        "name": r.name,
        "category": r.category,
        "price_cents": r.price_cents,
        "allergens": list(r.allergens),
        "dietary_tags": list(r.dietary_tags),
        "spice_level": r.spice_level,
        "is_available": r.is_available,
    }


def _row_chunk_meta(r) -> dict:
    return {
        "slug": r.slug,
        "source_type": r.source_type,
        "section_path": r.section_path,
        "page": r.page,
        "menu_item_ids": list(r.menu_item_ids),
        "token_count": r.token_count,
    }


async def retrieve(
    conn: AsyncConnection,
    query: str,
    filters: Filters | None = None,
    cfg: RetrievalConfig | None = None,
    scope: set[Scope] | None = None,
    *,
    embedder: Embedder | None = None,
    cache: QueryEmbedCache | None = None,
) -> RetrievalResult:
    filters = filters or Filters()
    cfg = cfg or RetrievalConfig()
    scope = scope or {"menu", "docs"}
    query = query.strip()[:MAX_QUERY_CHARS]
    t_start = time.perf_counter()
    timings = dict.fromkeys(("embed", "vector", "fts", "fuse", "rerank"), 0.0)
    ranked: dict[str, list[tuple[str, int]]] = {}
    rows: dict[tuple[str, int], Any] = {}
    raw_scores: dict[tuple[str, int], dict[str, float]] = {}
    cache_hit: bool | None = None

    def collect(source: str, kind: str, result_rows, score_attr: str, sim: bool) -> None:
        keys = []
        for r in result_rows:
            key = (kind, r.id)
            keys.append(key)
            rows.setdefault(key, r)
            val = float(getattr(r, score_attr))
            raw_scores.setdefault(key, {})[source] = 1.0 - val if sim else val
        ranked[source] = keys

    if not query:
        return RetrievalResult([], {**timings, "total": 0.0}, cfg.chunk_config, cfg.mode, None)

    use_vec = cfg.mode in ("vector", "hybrid")
    use_fts = cfg.mode in ("fts", "hybrid")

    qvec: str | None = None
    if use_vec:
        t = time.perf_counter()
        emb = embedder or get_embedder(cfg.embed_model)
        vec, cache_hit = await embed_query_cached(emb, query, cfg.query_prefix, cache)
        qvec = to_pgvector(vec)
        timings["embed"] = (time.perf_counter() - t) * 1000

    async with _tx(conn):
        if use_vec:
            t = time.perf_counter()
            if "menu" in scope:  # exact scan over ~42 filtered rows; planner flags stay default
                sql, p = menu_vector_sql(cfg, filters)
                p["q"] = qvec
                collect("menu_vec", "menu", (await conn.execute(text(sql), p)).all(), "dist", True)
            if "docs" in scope:
                local = {
                    "hnsw.ef_search": str(int(cfg.ef_search)),
                    "hnsw.iterative_scan": cfg.iterative_scan,
                }
                # At ~1e3 rows the planner would rightly pick a seq scan + sort; this study is
                # about the ANN path, so it is told which path to take. Reset below.
                if cfg.vector_index == "exact":
                    local |= {"enable_indexscan": "off", "enable_bitmapscan": "off"}
                else:
                    local |= {"enable_seqscan": "off", "enable_sort": "off"}
                await _set_local(conn, local)
                try:
                    sql, p = chunk_vector_sql(cfg, filters)
                    p["q"] = qvec
                    res = (await conn.execute(text(sql), p)).all()
                finally:
                    # SET LOCAL lasts until the caller's tx ends; do not leak planner flags.
                    await _set_local(conn, dict.fromkeys(PLANNER_FLAGS, "on"))
                collect("chunk_vec", "chunk", res, "dist", True)
            timings["vector"] = (time.perf_counter() - t) * 1000
        if use_fts:
            t = time.perf_counter()
            tsq = build_tsquery(query, cfg.fts_operator)
            if tsq:
                if "menu" in scope:
                    sql, p = menu_fts_sql(cfg, filters)
                    p["tsq"] = tsq
                    collect(
                        "menu_fts", "menu", (await conn.execute(text(sql), p)).all(), "rank", False
                    )
                if "docs" in scope:
                    sql, p = chunk_fts_sql(cfg, filters)
                    p["tsq"] = tsq
                    collect(
                        "chunk_fts",
                        "chunk",
                        (await conn.execute(text(sql), p)).all(),
                        "rank",
                        False,
                    )
            timings["fts"] = (time.perf_counter() - t) * 1000

    t = time.perf_counter()
    fused = rrf(ranked, cfg.rrf_k)
    timings["fuse"] = (time.perf_counter() - t) * 1000

    n_keep = cfg.rerank_top_n if cfg.rerank else cfg.k
    hits = [_make_hit(f, rows, raw_scores) for f in fused[:n_keep]]

    if cfg.rerank and hits:
        t = time.perf_counter()
        scores = await rerank_scores(
            get_retrieval_settings().rerank_model, query, [h.text for h in hits]
        )
        for h, s in zip(hits, scores, strict=True):
            h.scores["rerank"] = s
            h.score = s
        hits.sort(key=lambda h: -h.score)  # stable: fused order breaks ties
        timings["rerank"] = (time.perf_counter() - t) * 1000
    hits = hits[: cfg.k]

    timings["total"] = (time.perf_counter() - t_start) * 1000
    for stage, ms in timings.items():
        if ms or stage == "total":
            RETRIEVAL_STAGE_SECONDS.labels(stage, cfg.mode).observe(ms / 1000)
    return RetrievalResult(
        hits, {k: round(v, 3) for k, v in timings.items()}, cfg.chunk_config, cfg.mode, cache_hit
    )


def _make_hit(f, rows, raw_scores) -> Hit:
    kind, id_ = f.key
    r = rows[f.key]
    if kind == "menu":
        return Hit(
            "menu_item", id_, id_, None, None, _menu_hit_text(r), f.score,
            dict(f.ranks), raw_scores[f.key], _row_menu_meta(r),
        )  # fmt: skip
    return Hit(
        "chunk", id_, None, r.document_id, r.chunk_index, r.content, f.score,
        dict(f.ranks), raw_scores[f.key], _row_chunk_meta(r),
    )  # fmt: skip


MenuSort = Literal["price_asc", "price_desc", "name"]
_MENU_SORT_SQL = {
    "price_asc": "m.price_cents ASC, m.id",
    "price_desc": "m.price_cents DESC, m.id",
    "name": "m.name, m.id",
}


async def list_menu_items(
    conn: AsyncConnection,
    filters: Filters | None = None,
    sort: MenuSort = "price_asc",
    max_price_cents: int | None = None,
    limit: int = 8,
) -> list[Hit]:
    """Plain SQL listing for questions that are about ordering, not relevance ("cheapest vegan
    dish", "desserts under $10"). Reuses `_menu_where`, so availability, allergen exclusion, dietary
    tags and categories are the same WHERE clauses as `retrieve()`. Returns menu_item Hits whose
    `score` is 0.0 (there is no relevance signal) in the requested order."""
    if sort not in _MENU_SORT_SQL:
        raise ValueError(f"bad sort {sort!r}")
    filters = filters or Filters()
    params: dict = {"n": max(1, min(int(limit), 100))}
    where = _menu_where(filters, params)
    if max_price_cents:
        where += " AND m.price_cents <= :maxp"
        params["maxp"] = int(max_price_cents)
    sql = (
        "SELECT m.id, m.name, m.description, m.price_cents, m.allergens, m.dietary_tags, "
        "m.spice_level, m.is_available, c.name AS category "
        "FROM menu_items m JOIN menu_categories c ON c.id = m.category_id "
        f"WHERE TRUE{where} ORDER BY {_MENU_SORT_SQL[sort]} LIMIT :n"
    )
    rows = (await conn.execute(text(sql), params)).all()
    return [
        Hit("menu_item", r.id, r.id, None, None, _menu_hit_text(r), 0.0, {}, {}, _row_menu_meta(r))
        for r in rows
    ]


async def explain_chunk_vector(
    conn: AsyncConnection, cfg: RetrievalConfig, filters: Filters | None = None
) -> list[str]:
    """EXPLAIN (not ANALYZE) of the chunk vector query, with a dummy query vector."""
    sql, p = chunk_vector_sql(cfg, filters or Filters())
    p["q"] = to_pgvector([0.0] * 383 + [1.0])
    async with _tx(conn):
        local = {"hnsw.iterative_scan": cfg.iterative_scan}
        if cfg.vector_index == "exact":  # same planner steering as retrieve()
            local |= {"enable_indexscan": "off", "enable_bitmapscan": "off"}
        else:
            local |= {"enable_seqscan": "off", "enable_sort": "off"}
        await _set_local(conn, local)
        try:
            res = await conn.execute(text("EXPLAIN " + sql), p)
            return [r[0] for r in res]
        finally:
            await _set_local(conn, dict.fromkeys(PLANNER_FLAGS, "on"))
