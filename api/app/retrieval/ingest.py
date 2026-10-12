"""Ingestion: corpus files -> documents -> chunks (per chunk config) -> embeddings (per model).

    python -m app.retrieval.ingest [--config NAME | --all-configs] [--model M ...]
                                   [--docs | --menu] [--prune] [--corpus-dir DIR]

Idempotent: a document is re-chunked only when its content hash (cleaned text + PIPELINE_VERSION)
changes, a chunk config is re-chunked only when its parameters change, and embeddings are computed
only for (chunk, model) / (menu item, model) pairs that are missing or stale. A second run reports
zero changes. Chunking and embedding are CPU-bound and run in a thread; each document is written
in one transaction (chunks + embeddings + links) so a crash never leaves a half-ingested document.
"""

import argparse
import asyncio
import hashlib
import json
import re
import sys
import time
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.config import get_settings
from app.logging import configure_logging
from app.retrieval.chunking import Chunk, ChunkSpec, TokenCounter, chunk_document, embed_text
from app.retrieval.embedder import (
    CHUNK_TOKENIZER_MODEL,
    MODELS,
    Embedder,
    embed_text_hash,
    get_embedder,
    load_chunk_tokenizer,
    to_pgvector,
)
from app.retrieval.parse import ParsedDoc, load_corpus
from app.retrieval.settings import get_retrieval_settings

log = structlog.get_logger("retrieval.ingest")
TOKENIZER_LABEL = "bge-small-en-v1.5-wordpiece"
SPICE_WORDS = {0: "none", 1: "mild", 2: "medium", 3: "hot"}


@dataclass(frozen=True)
class ChunkConfigDef:
    name: str
    strategy: str
    max_tokens: int
    overlap_tokens: int
    prepend_headings: bool = False

    @property
    def spec(self) -> ChunkSpec:
        return ChunkSpec(self.strategy, self.max_tokens, self.overlap_tokens, self.prepend_headings)  # type: ignore[arg-type]

    @property
    def params(self) -> dict:
        return {"prepend_headings": self.prepend_headings}


def load_chunk_configs(path: Path | None = None) -> tuple[dict[str, ChunkConfigDef], str]:
    path = path or get_retrieval_settings().chunking_config_path
    raw = tomllib.loads(path.read_text())
    cfgs = {c["name"]: ChunkConfigDef(**c) for c in raw["config"]}
    return cfgs, raw["default"]


@dataclass
class IngestReport:
    documents_inserted: int = 0
    documents_updated: int = 0
    documents_unchanged: int = 0
    chunk_configs_changed: int = 0
    chunks_inserted: int = 0
    chunks_deleted: int = 0
    embeddings_inserted: int = 0
    menu_embeddings_upserted: int = 0
    links_updated: int = 0
    pruned_configs: int = 0
    pruned_embeddings: int = 0
    seconds: float = 0.0
    per_doc: list[dict] = field(default_factory=list)

    @property
    def total_changes(self) -> int:
        return (
            self.documents_inserted
            + self.documents_updated
            + self.chunk_configs_changed
            + self.chunks_inserted
            + self.chunks_deleted
            + self.embeddings_inserted
            + self.menu_embeddings_upserted
            + self.links_updated
            + self.pruned_configs
            + self.pruned_embeddings
        )


# --- menu items ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class MenuRow:
    id: int
    name: str
    category: str
    description: str
    dietary_tags: tuple[str, ...]
    spice_level: int


def menu_embed_text(m: MenuRow) -> str:
    """Allergens are deliberately NOT in the text: allergen safety comes from SQL filters, never
    from vector similarity."""
    tags = ", ".join(t.replace("_", " ") for t in m.dietary_tags) or "none"
    return (
        f"{m.name}. {m.category}. {m.description} Dietary: {tags}. "
        f"Spice: {SPICE_WORDS.get(m.spice_level, str(m.spice_level))}."
    )


async def load_menu(conn: AsyncConnection) -> list[MenuRow]:
    rows = await conn.execute(
        text(
            "SELECT m.id, m.name, c.name AS category, m.description, m.dietary_tags, m.spice_level "
            "FROM menu_items m JOIN menu_categories c ON c.id = m.category_id ORDER BY m.id"
        )
    )
    return [
        MenuRow(r.id, r.name, r.category, r.description, tuple(r.dietary_tags), r.spice_level)
        for r in rows
    ]


def build_linker(menu: list[MenuRow]):
    """Case-insensitive exact name match (with and without a parenthetical suffix, so
    'Pinot Grigio (glass)' also matches 'Pinot Grigio'). Deliberately over-links: a chunk that
    merely mentions a dish is treated as being about it, so filters err on the safe side."""
    pats: list[tuple[int, re.Pattern]] = []
    for m in menu:
        aliases = {m.name.lower(), re.sub(r"\s*\(.*?\)\s*", " ", m.name).strip().lower()}
        for a in aliases:
            pats.append((m.id, re.compile(r"(?<![a-z0-9])" + re.escape(a) + r"(?![a-z0-9])")))

    def link(content: str) -> list[int]:
        low = content.lower()
        return sorted({mid for mid, p in pats if p.search(low)})

    return link


# --- helpers -------------------------------------------------------------------------------------


def _page_for(doc: ParsedDoc, pos: int) -> int | None:
    for s, e, page in doc.metadata.get("page_spans", []):
        if s <= pos <= e:
            return page
    return None


@dataclass
class _PlannedChunks:
    cfg_name: str
    chunks: list[Chunk]
    # model -> list of (embed_text, vector); aligned with chunks
    vectors: dict[str, list[list[float]]]


def _chunk_and_embed(
    doc: ParsedDoc,
    cfgs: list[ChunkConfigDef],
    models: list[str],
    embedders: dict[str, Embedder],
    counter: TokenCounter,
) -> list[_PlannedChunks]:
    out = []
    for cfg in cfgs:
        chunks = chunk_document(doc.content, cfg.spec, counter)
        texts = [embed_text(c, cfg.prepend_headings) for c in chunks]
        vectors = {m: embedders[m].embed_documents(texts) for m in models}
        out.append(_PlannedChunks(cfg.name, chunks, vectors))
    return out


# --- core ----------------------------------------------------------------------------------------


async def _sync_configs(
    conn: AsyncConnection, cfgs: list[ChunkConfigDef], rep: IngestReport
) -> dict[str, int]:
    ids: dict[str, int] = {}
    for c in cfgs:
        row = (
            await conn.execute(
                text(
                    "SELECT id, strategy, max_tokens, overlap_tokens, params, tokenizer "
                    "FROM chunk_configs WHERE name = :n"
                ),
                {"n": c.name},
            )
        ).first()
        want = (c.strategy, c.max_tokens, c.overlap_tokens, c.params, TOKENIZER_LABEL)
        if row is None:
            r = await conn.execute(
                text(
                    "INSERT INTO chunk_configs (name, strategy, max_tokens, overlap_tokens, params,"
                    " tokenizer) VALUES (:n, :s, :m, :o, CAST(:p AS jsonb), :t) RETURNING id"
                ),
                {
                    "n": c.name,
                    "s": c.strategy,
                    "m": c.max_tokens,
                    "o": c.overlap_tokens,
                    "p": json.dumps(c.params),
                    "t": TOKENIZER_LABEL,
                },
            )
            ids[c.name] = r.scalar_one()
            rep.chunk_configs_changed += 1
        else:
            ids[c.name] = row.id
            have = (row.strategy, row.max_tokens, row.overlap_tokens, row.params, row.tokenizer)
            if have != want:
                d = await conn.execute(
                    text("DELETE FROM chunks WHERE chunk_config_id = :i"), {"i": row.id}
                )
                rep.chunks_deleted += d.rowcount
                await conn.execute(
                    text(
                        "UPDATE chunk_configs SET strategy=:s, max_tokens=:m, overlap_tokens=:o,"
                        " params=CAST(:p AS jsonb), tokenizer=:t WHERE id=:i"
                    ),
                    {
                        "s": c.strategy,
                        "m": c.max_tokens,
                        "o": c.overlap_tokens,
                        "p": json.dumps(c.params),
                        "t": TOKENIZER_LABEL,
                        "i": row.id,
                    },
                )
                rep.chunk_configs_changed += 1
    return ids


async def _ingest_doc(
    engine: AsyncEngine,
    doc: ParsedDoc,
    cfgs: list[ChunkConfigDef],
    cfg_ids: dict[str, int],
    models: list[str],
    embedders: dict[str, Embedder],
    counter: TokenCounter,
    link,
    rep: IngestReport,
) -> None:
    t0 = time.perf_counter()
    async with engine.connect() as conn:
        existing = (
            await conn.execute(
                text("SELECT id, content_hash FROM documents WHERE slug = :s"), {"s": doc.slug}
            )
        ).first()
        changed = existing is None or existing.content_hash != doc.content_hash
        todo_chunk: list[ChunkConfigDef] = []
        missing_emb: dict[tuple[str, str], list] = {}  # (cfg, model) -> rows lacking an embedding
        for c in cfgs:
            have = 0
            if existing is not None and not changed:
                have = (
                    await conn.execute(
                        text(
                            "SELECT count(*) FROM chunks WHERE document_id = :d"
                            " AND chunk_config_id = :c"
                        ),
                        {"d": existing.id, "c": cfg_ids[c.name]},
                    )
                ).scalar_one()
            if changed or have == 0:
                todo_chunk.append(c)
                continue
            for m in models:
                rows = (
                    await conn.execute(
                        text(
                            "SELECT ch.id, ch.content, ch.section_path FROM chunks ch"
                            " WHERE ch.document_id = :d AND ch.chunk_config_id = :c AND NOT EXISTS"
                            " (SELECT 1 FROM chunk_embeddings e WHERE e.chunk_id = ch.id"
                            "  AND e.model = :m)"
                        ),
                        {"d": existing.id, "c": cfg_ids[c.name], "m": m},
                    )
                ).all()
                if rows:
                    missing_emb[(c.name, m)] = list(rows)

    planned = await asyncio.to_thread(_chunk_and_embed, doc, todo_chunk, models, embedders, counter)
    cfg_by_name = {c.name: c for c in cfgs}
    # Embeddings for already-stored chunks that lack a vector for some model.
    backfill: dict[tuple[str, str], list[list[float]]] = {}
    for (cname, m), rows in missing_emb.items():
        prep = cfg_by_name[cname].prepend_headings
        texts = [
            f"{r.section_path}\n{r.content}" if prep and r.section_path else r.content for r in rows
        ]
        backfill[(cname, m)] = await asyncio.to_thread(embedders[m].embed_documents, texts)

    if not changed and not planned and not backfill:
        rep.documents_unchanged += 1
        return

    async with engine.begin() as conn:
        if existing is None:
            doc_id = (
                await conn.execute(
                    text(
                        "INSERT INTO documents (slug, source_type, title, uri, content,"
                        " content_hash, metadata) VALUES (:slug, :st, :title, :uri, :content, :h,"
                        " CAST(:meta AS jsonb)) RETURNING id"
                    ),
                    _doc_params(doc),
                )
            ).scalar_one()
            rep.documents_inserted += 1
        else:
            doc_id = existing.id
            if changed:
                d = await conn.execute(
                    text("DELETE FROM chunks WHERE document_id = :d"), {"d": doc_id}
                )
                rep.chunks_deleted += d.rowcount
                await conn.execute(
                    text(
                        "UPDATE documents SET source_type=:st, title=:title, uri=:uri,"
                        " content=:content, content_hash=:h, metadata=CAST(:meta AS jsonb),"
                        " updated_at=now() WHERE id=:d"
                    ),
                    {**_doc_params(doc), "d": doc_id},
                )
                rep.documents_updated += 1
        for p in planned:
            cid = cfg_ids[p.cfg_name]
            ids = await _insert_chunks(conn, doc, doc_id, cid, p.chunks, link)
            rep.chunks_inserted += len(ids)
            for m, vecs in p.vectors.items():
                texts = [embed_text(c, cfg_by_name[p.cfg_name].prepend_headings) for c in p.chunks]
                rep.embeddings_inserted += await _insert_embeddings(conn, ids, m, texts, vecs)
        for (cname, m), vecs in backfill.items():
            rows = missing_emb[(cname, m)]
            prep = cfg_by_name[cname].prepend_headings
            texts = [
                f"{r.section_path}\n{r.content}" if prep and r.section_path else r.content
                for r in rows
            ]
            rep.embeddings_inserted += await _insert_embeddings(
                conn, [r.id for r in rows], m, texts, vecs
            )
    rep.per_doc.append(
        {
            "slug": doc.slug,
            "changed": changed,
            "configs_chunked": [c.name for c in todo_chunk],
            "seconds": round(time.perf_counter() - t0, 2),
        }
    )


def _doc_params(doc: ParsedDoc) -> dict:
    return {
        "slug": doc.slug,
        "st": doc.source_type,
        "title": doc.title,
        "uri": doc.uri,
        "content": doc.content,
        "h": doc.content_hash,
        "meta": json.dumps(doc.metadata),
    }


async def _insert_chunks(
    conn: AsyncConnection, doc: ParsedDoc, doc_id: int, cfg_id: int, chunks: list[Chunk], link
) -> list[int]:
    if not chunks:
        return []
    ids: list[int] = []
    for c in chunks:  # one INSERT .. RETURNING per chunk keeps id order trivially correct
        r = await conn.execute(
            text(
                "INSERT INTO chunks (document_id, chunk_config_id, chunk_index, content,"
                " start_char, end_char, token_count, section_path, page, menu_item_ids)"
                " VALUES (:d, :c, :i, :t, :s, :e, :n, :sp, :pg, :links) RETURNING id"
            ),
            {
                "d": doc_id,
                "c": cfg_id,
                "i": c.index,
                "t": c.text,
                "s": c.start,
                "e": c.end,
                "n": c.token_count,
                "sp": c.section_path,
                "pg": _page_for(doc, c.start),
                "links": link(c.text),
            },
        )
        ids.append(r.scalar_one())
    return ids


async def _insert_embeddings(
    conn: AsyncConnection,
    chunk_ids: list[int],
    model: str,
    texts: list[str],
    vecs: list[list[float]],
) -> int:
    if not chunk_ids:
        return 0
    await conn.execute(
        text(
            "INSERT INTO chunk_embeddings (chunk_id, model, embedding, embed_text_hash)"
            " VALUES (:i, :m, CAST(:v AS vector), :h)"
        ),
        [
            {"i": i, "m": model, "v": to_pgvector(v), "h": embed_text_hash(model, t)}
            for i, t, v in zip(chunk_ids, texts, vecs, strict=True)
        ],
    )
    return len(chunk_ids)


async def _ingest_menu(
    engine: AsyncEngine,
    menu: list[MenuRow],
    models: list[str],
    embedders: dict[str, Embedder],
    rep: IngestReport,
) -> None:
    async with engine.connect() as conn:
        have = {
            (r.menu_item_id, r.model): r.content_hash
            for r in await conn.execute(
                text("SELECT menu_item_id, model, content_hash FROM menu_item_embeddings")
            )
        }
    for m in models:
        stale = []
        for it in menu:
            t = menu_embed_text(it)
            if have.get((it.id, m)) != hashlib.sha256(t.encode()).hexdigest():
                stale.append((it, t))
        if not stale:
            continue
        vecs = await asyncio.to_thread(embedders[m].embed_documents, [t for _, t in stale])
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO menu_item_embeddings"
                    " (menu_item_id, model, content_hash, embedding)"
                    " VALUES (:i, :m, :h, CAST(:v AS vector)) ON CONFLICT (menu_item_id, model)"
                    " DO UPDATE SET content_hash = EXCLUDED.content_hash,"
                    " embedding = EXCLUDED.embedding"
                ),
                [
                    {
                        "i": it.id,
                        "m": m,
                        "h": hashlib.sha256(t.encode()).hexdigest(),
                        "v": to_pgvector(v),
                    }
                    for (it, t), v in zip(stale, vecs, strict=True)
                ],
            )
        rep.menu_embeddings_upserted += len(stale)


async def _refresh_links(engine: AsyncEngine, link, cfg_ids: list[int], rep: IngestReport) -> None:
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id, content, menu_item_ids FROM chunks WHERE chunk_config_id = ANY(:c)"
                ),
                {"c": cfg_ids},
            )
        ).all()
        upd = [
            {"i": r.id, "l": new}
            for r in rows
            if (new := link(r.content)) != sorted(r.menu_item_ids)
        ]
        if upd:
            await conn.execute(text("UPDATE chunks SET menu_item_ids = :l WHERE id = :i"), upd)
        rep.links_updated += len(upd)


async def run_ingest(
    engine: AsyncEngine,
    *,
    docs: list[ParsedDoc],
    configs: list[ChunkConfigDef],
    models: list[str],
    embedders: dict[str, Embedder],
    counter: TokenCounter,
    do_docs: bool = True,
    do_menu: bool = True,
    prune: bool = False,
) -> IngestReport:
    rep = IngestReport()
    t0 = time.perf_counter()
    async with engine.connect() as conn:
        menu = await load_menu(conn)
    link = build_linker(menu)

    cfg_ids: dict[str, int] = {}
    if do_docs:
        async with engine.begin() as conn:
            cfg_ids = await _sync_configs(conn, configs, rep)
        for doc in docs:
            await _ingest_doc(engine, doc, configs, cfg_ids, models, embedders, counter, link, rep)
        await _refresh_links(engine, link, list(cfg_ids.values()), rep)
    if do_menu:
        await _ingest_menu(engine, menu, models, embedders, rep)
    if prune:
        async with engine.begin() as conn:
            if do_docs:
                d = await conn.execute(
                    text("DELETE FROM chunk_configs WHERE NOT (name = ANY(:n))"),
                    {"n": [c.name for c in configs]},
                )
                rep.pruned_configs += d.rowcount
            for table in ("chunk_embeddings", "menu_item_embeddings"):
                d = await conn.execute(
                    text(f"DELETE FROM {table} WHERE NOT (model = ANY(:m))"),  # noqa: S608
                    {"m": models},
                )
                rep.pruned_embeddings += d.rowcount
    rep.seconds = round(time.perf_counter() - t0, 2)
    return rep


# --- CLI -----------------------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="python -m app.retrieval.ingest", description=__doc__)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--config", action="append", help="chunk config name (repeatable)")
    g.add_argument("--all-configs", action="store_true")
    ap.add_argument("--model", action="append", help="embedding model (repeatable)")
    only = ap.add_mutually_exclusive_group()
    only.add_argument("--docs", action="store_true", help="documents only (skip menu items)")
    only.add_argument("--menu", action="store_true", help="menu items only (skip documents)")
    ap.add_argument("--prune", action="store_true", help="delete configs/models not selected")
    ap.add_argument("--corpus-dir", type=Path)
    return ap.parse_args(argv)


async def _main(argv: list[str] | None = None) -> IngestReport:
    args = _parse_args(argv)
    settings = get_retrieval_settings()
    all_cfgs, default = load_chunk_configs()
    names = list(all_cfgs) if args.all_configs else (args.config or [default])
    unknown = [n for n in names if n not in all_cfgs]
    if unknown:
        raise SystemExit(f"unknown chunk config(s) {unknown}; known: {sorted(all_cfgs)}")
    models = args.model or [settings.embed_model]
    bad = [m for m in models if m not in MODELS]
    if bad:
        raise SystemExit(f"unsupported model(s) {bad}; allowed: {sorted(MODELS)}")

    configure_logging(get_settings().log_level)
    counter = TokenCounter(await asyncio.to_thread(load_chunk_tokenizer))
    embedders = {m: get_embedder(m) for m in models}
    docs = load_corpus(args.corpus_dir or settings.corpus_dir)
    engine = create_async_engine(get_settings().database_url)
    try:
        rep = await run_ingest(
            engine,
            docs=docs,
            configs=[all_cfgs[n] for n in names],
            models=models,
            embedders=embedders,
            counter=counter,
            do_docs=not args.menu,
            do_menu=not args.docs,
            prune=args.prune,
        )
    finally:
        await engine.dispose()
    log.info(
        "ingest_complete",
        configs=names,
        models=models,
        tokenizer=CHUNK_TOKENIZER_MODEL,
        total_changes=rep.total_changes,
        **{k: v for k, v in asdict(rep).items() if k != "per_doc"},
    )
    sys.stdout.write(json.dumps({"total_changes": rep.total_changes, **asdict(rep)}) + "\n")
    return rep


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
