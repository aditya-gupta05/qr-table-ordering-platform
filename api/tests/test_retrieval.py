import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.retrieval.cache import QueryEmbedCache
from app.retrieval.chunking import TokenCounter
from app.retrieval.config import Filters, RetrievalConfig
from app.retrieval.fusion import rrf
from app.retrieval.ingest import ChunkConfigDef, load_menu, menu_embed_text, run_ingest
from app.retrieval.parse import parse_markdown
from app.retrieval.search import build_tsquery, chunk_vector_sql, explain_chunk_vector, retrieve
from tests.retrieval_helpers import FakeEmbedder, toy_tokenizer

BGE = "BAAI/bge-small-en-v1.5"
CFG = ChunkConfigDef("t_rec", "recursive_md", 60, 8)
DOC_SAUCE = """# Sauces

## Peanut sauce

The Chicken Satay is served with warm peanut sauce made fresh every morning with roasted peanuts.

## Fryers

The fryer oil is shared between the Crispy Calamari and the Onion Rings. Ask about allergies.
"""
DOC_FAQ = """# FAQ

## Corkage

Corkage is twenty five dollars per bottle and a table may bring two bottles at most.

## Vegan

We cater for vegans. The Vegan Buddha Bowl is our most popular plant based dish.
"""
PEANUT_ITEMS = {"Chicken Satay", "Satay Tofu Skewers", "Pad Thai"}


# --- pure: fusion --------------------------------------------------------------------------------


def test_rrf_math_and_provenance():
    out = rrf({"a": ["x", "y", "z"], "b": ["y", "x"]}, k=60)
    by = {f.key: f for f in out}
    assert by["x"].score == pytest.approx(1 / 61 + 1 / 62)
    assert by["y"].score == pytest.approx(1 / 62 + 1 / 61)
    assert by["z"].score == pytest.approx(1 / 63)  # single-list item still ranked
    assert by["x"].ranks == {"a": 1, "b": 2}
    assert [f.key for f in out][-1] == "z"


def test_rrf_ties_are_deterministic():
    out1 = rrf({"a": ["x", "y"], "b": ["y", "x"]})
    out2 = rrf({"b": ["y", "x"], "a": ["x", "y"]})
    assert [f.key for f in out1] == [f.key for f in out2] == ["x", "y"]  # tie -> key order


def test_rrf_k_changes_ordering():
    lists = {"a": ["top", "m1", "m2", "x"], "b": ["m1", "m2", "x", "top2"], "c": ["m2", "m1"]}
    low = [f.key for f in rrf(lists, k=1)]
    high = [f.key for f in rrf(lists, k=1000)]
    assert low != high


def test_rrf_duplicate_in_one_list_counts_once():
    out = rrf({"a": ["x", "x", "y"]}, k=60)
    assert {f.key: f.score for f in out}["x"] == pytest.approx(1 / 61)


def test_tsquery_builder_is_syntax_safe():
    assert build_tsquery("is the fryer shared?", "or") == "is | the | fryer | shared"
    assert build_tsquery("corkage & ('x' | !y)", "and") == "corkage & x & y"
    assert build_tsquery("   ", "or") == ""


def test_filters_reject_unknown_values():
    with pytest.raises(ValueError):
        Filters(exclude_allergens=("peanuts",))
    with pytest.raises(ValueError):
        RetrievalConfig(embed_model="not/a-model")


# --- DB: retrieval -------------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def db(migrated_test_db, tmp_path_factory):
    eng = create_async_engine(migrated_test_db)
    d = tmp_path_factory.mktemp("corpus")
    (d / "faq.md").write_text(DOC_FAQ)
    (d / "about-sourcing.md").write_text(DOC_SAUCE)
    docs = [parse_markdown(d / "faq.md", "faq"), parse_markdown(d / "about-sourcing.md", "about")]
    async with eng.begin() as c:
        for t in ("documents", "chunk_configs", "menu_item_embeddings"):
            await c.execute(text(f"DELETE FROM {t}"))  # noqa: S608
    emb = FakeEmbedder(BGE)
    await run_ingest(
        eng,
        docs=docs,
        configs=[CFG],
        models=[BGE],
        embedders={BGE: emb},
        counter=TokenCounter(toy_tokenizer(DOC_FAQ, DOC_SAUCE)),
    )
    yield eng
    async with eng.begin() as c:
        for t in ("documents", "chunk_configs", "menu_item_embeddings"):
            await c.execute(text(f"DELETE FROM {t}"))  # noqa: S608
    await eng.dispose()


def cfg(**kw) -> RetrievalConfig:
    return RetrievalConfig(chunk_config="t_rec", **kw)


async def run(db, q, filters=None, scope=None, embedder=None, **kw):
    async with db.connect() as c:
        return await retrieve(
            c,
            q,
            filters,
            cfg(**kw),
            scope,
            embedder=embedder or FakeEmbedder(BGE),
            cache=QueryEmbedCache(8),
        )


async def satay_query(db) -> str:
    async with db.connect() as c:
        menu = await load_menu(c)
    return menu_embed_text(next(m for m in menu if m.name == "Chicken Satay"))


@pytest.mark.parametrize("mode", ["vector", "hybrid"])
@pytest.mark.parametrize("index", ["hnsw", "exact"])
async def test_exclude_peanut_never_returns_peanut_items_even_when_satay_is_nearest(
    db, mode, index
):
    q = await satay_query(db)  # engineered: identical text -> cosine 1.0 with Chicken Satay
    plain = await run(db, q, Filters(), scope={"menu"}, mode="vector", k=3, vector_index=index)
    assert plain.hits[0].kind == "menu_item" and plain.hits[0].meta["name"] == "Chicken Satay"
    assert plain.hits[0].scores["menu_vec"] == pytest.approx(1.0, abs=1e-4)

    res = await run(
        db, q, Filters(exclude_allergens=("peanut",)), mode=mode, k=20, vector_index=index
    )
    assert res.hits
    async with db.connect() as c:
        ids = {
            r.id: r.name
            for r in await c.execute(
                text("SELECT id, name FROM menu_items WHERE name = ANY(:n)"),
                {"n": list(PEANUT_ITEMS)},
            )
        }
    for h in res.hits:
        if h.kind == "menu_item":
            assert "peanut" not in h.meta["allergens"] and h.meta["name"] not in PEANUT_ITEMS
        else:
            assert not set(h.meta["menu_item_ids"]) & set(ids), h.text
    # the satay *chunk* exists and is returned without the filter, so the test is meaningful
    unfiltered = await run(db, q, Filters(), mode=mode, k=20, vector_index=index)
    assert any(
        h.kind == "chunk" and set(h.meta["menu_item_ids"]) & set(ids) for h in unfiltered.hits
    )


async def test_vegan_filter_applies_to_items_and_linked_chunks(db):
    res = await run(db, "tasty dish with sauce", Filters(dietary=("vegan",)), k=30, mode="hybrid")
    assert res.hits
    for h in res.hits:
        if h.kind == "menu_item":
            assert "vegan" in h.meta["dietary_tags"]
    async with db.connect() as c:
        non_vegan = {
            r.id
            for r in await c.execute(
                text("SELECT id FROM menu_items WHERE NOT dietary_tags @> ARRAY['vegan']")
            )
        }
    for h in res.hits:
        if h.kind == "chunk":
            assert not set(h.meta["menu_item_ids"]) & non_vegan


async def test_unavailable_items_excluded_by_default(db):
    async with db.begin() as c:
        await c.execute(text("UPDATE menu_items SET is_available=false WHERE name='Edamame'"))
    try:
        hide = await run(db, "edamame soybeans", mode="fts", k=20)
        assert all(h.meta.get("name") != "Edamame" for h in hide.hits)
        show = await run(db, "edamame soybeans", Filters(available_only=False), mode="fts", k=20)
        assert any(h.meta.get("name") == "Edamame" for h in show.hits)
    finally:
        async with db.begin() as c:
            await c.execute(text("UPDATE menu_items SET is_available=true WHERE name='Edamame'"))


async def test_scope_and_source_type_filters(db):
    menu_only = await run(db, "fryer oil shared", scope={"menu"}, mode="hybrid", k=10)
    assert menu_only.hits and all(h.kind == "menu_item" for h in menu_only.hits)
    docs_only = await run(db, "fryer oil shared", scope={"docs"}, mode="hybrid", k=10)
    assert docs_only.hits and all(h.kind == "chunk" for h in docs_only.hits)
    faq_only = await run(db, "corkage bottle", Filters(source_types=("faq",)), scope={"docs"}, k=10)
    assert faq_only.hits and {h.meta["slug"] for h in faq_only.hits} == {"faq"}
    cat = await run(db, "something", Filters(categories=("Desserts",)), scope={"menu"}, k=10)
    assert cat.hits and {h.meta["category"] for h in cat.hits} == {"Desserts"}


@pytest.mark.parametrize("mode", ["vector", "fts", "hybrid"])
async def test_result_contract_shape_and_descending_scores(db, mode):
    res = await run(db, "corkage bottle fryer", mode=mode, k=6, fts_operator="or")
    assert 0 < len(res.hits) <= 6
    assert res.config_name == "t_rec" and res.mode == mode
    assert set(res.timings_ms) == {"embed", "vector", "fts", "fuse", "rerank", "total"}
    assert res.timings_ms["total"] >= res.timings_ms["fuse"] >= 0
    scores = [h.score for h in res.hits]
    assert scores == sorted(scores, reverse=True)
    for h in res.hits:
        assert h.text and h.ranks and h.scores
        if h.kind == "chunk":
            assert h.document_id and h.chunk_index is not None and h.menu_item_id is None
        else:
            assert h.menu_item_id == h.id and h.document_id is None
    if mode == "vector":
        assert res.timings_ms["fts"] == 0 and res.timings_ms["embed"] >= 0
    if mode == "fts":
        assert res.timings_ms["embed"] == 0 and res.cache_hit is None


async def test_fts_or_vs_and(db):
    or_ = await run(
        db, "corkage unicornword", Filters(), scope={"docs"}, mode="fts", fts_operator="or"
    )
    and_ = await run(
        db, "corkage unicornword", Filters(), scope={"docs"}, mode="fts", fts_operator="and"
    )
    assert or_.hits and "orkage" in or_.hits[0].text
    assert and_.hits == []


async def test_stopword_only_and_empty_queries_are_safe(db):
    assert (await run(db, "   ", mode="hybrid")).hits == []
    res = await run(db, "is the a", mode="fts")
    assert res.hits == []


async def test_query_embedding_cache_hit_skips_embedder(db):
    emb = FakeEmbedder(BGE)
    cache = QueryEmbedCache(8)
    async with db.connect() as c:
        r1 = await retrieve(c, "Corkage  Fee", None, cfg(mode="vector"), embedder=emb, cache=cache)
        r2 = await retrieve(c, "corkage fee", None, cfg(mode="vector"), embedder=emb, cache=cache)
    assert (r1.cache_hit, r2.cache_hit) == (False, True)  # normalised key: case + whitespace
    assert emb.query_calls == 1 and (cache.hits, cache.misses) == (1, 1)
    assert [h.id for h in r1.hits] == [h.id for h in r2.hits]


def test_cache_is_lru():
    c = QueryEmbedCache(2)
    c.put(("a",), [1.0])
    c.put(("b",), [2.0])
    assert c.get(("a",)) == [1.0]
    c.put(("c",), [3.0])
    assert c.get(("b",)) is None and c.get(("a",)) == [1.0]


async def test_planner_flags_do_not_leak_and_hnsw_index_is_chosen(db):
    async with db.connect() as c:
        await retrieve(c, "fryer", None, cfg(mode="vector"), embedder=FakeEmbedder(BGE))
        flags = [
            (await c.execute(text(f"SHOW {f}"))).scalar_one()
            for f in ("enable_seqscan", "enable_sort", "enable_indexscan", "enable_bitmapscan")
        ]
        plan = "\n".join(await explain_chunk_vector(c, cfg()))
        exact = "\n".join(await explain_chunk_vector(c, cfg(vector_index="exact")))
    assert flags == ["on"] * 4
    assert "ix_chunk_emb_hnsw_bge_small" in plan
    assert "ix_chunk_emb_hnsw" not in exact


# --- HTTP: /api/search ---------------------------------------------------------------------------


@pytest.fixture
def fake_embedder(monkeypatch):
    import app.retrieval.search as s

    emb = FakeEmbedder(BGE)
    monkeypatch.setattr(s, "get_embedder", lambda model: emb)
    return emb


@pytest.fixture
def search_cfg(monkeypatch, db):
    """Point the endpoint at the test chunk config and the shared test engine."""
    import app.routers.search as r

    monkeypatch.setattr(r, "_chunk_configs", lambda: ({"t_rec": CFG}, "t_rec"))
    monkeypatch.setattr(r, "engine", db)


async def test_search_endpoint_200_shape(client, fake_embedder, search_cfg):
    resp = await client.get("/api/search", params={"q": "corkage bottle", "k": 4})
    assert resp.status_code == 200
    body = resp.json()
    assert body["config_name"] == "t_rec" and body["mode"] == "hybrid"
    assert 0 < len(body["hits"]) <= 4
    assert set(body["timings_ms"]) == {"embed", "vector", "fts", "fuse", "rerank", "total"}
    h = body["hits"][0]
    assert {"kind", "id", "text", "score", "ranks", "scores", "meta"} <= set(h)
    assert all(len(x["text"]) <= 500 for x in body["hits"])


@pytest.mark.parametrize(
    "params",
    [
        {"q": "x", "exclude_allergens": "peanuts"},
        {"q": "x", "dietary": "carnivore"},
        {"q": "x", "mode": "magic"},
        {"q": "x", "config": "no_such_config"},
        {"q": "x", "scope": "everything"},
        {"q": "x", "k": 0},
        {"q": ""},
    ],
)
async def test_search_endpoint_422(client, search_cfg, params):
    assert (await client.get("/api/search", params=params)).status_code == 422


async def test_search_endpoint_filters_pass_through(client, fake_embedder, search_cfg):
    q = "chicken satay peanut sauce"
    plain = (await client.get("/api/search", params={"q": q, "scope": "menu", "k": 30})).json()
    assert any("peanut" in h["meta"]["allergens"] for h in plain["hits"])
    filt = (
        await client.get(
            "/api/search",
            params={"q": q, "scope": "menu", "k": 30, "exclude_allergens": "peanut"},
        )
    ).json()
    assert filt["hits"]
    assert all("peanut" not in h["meta"]["allergens"] for h in filt["hits"])


async def test_search_endpoint_scope_and_source_type(client, fake_embedder, search_cfg):
    docs = (await client.get("/api/search", params={"q": "fryer", "scope": "docs"})).json()
    assert docs["hits"] and all(h["kind"] == "chunk" for h in docs["hits"])
    menu = (await client.get("/api/search", params={"q": "fryer", "scope": "menu"})).json()
    assert all(h["kind"] == "menu_item" for h in menu["hits"])
    faq = (
        await client.get(
            "/api/search", params={"q": "corkage", "scope": "docs", "source_type": "faq"}
        )
    ).json()
    assert faq["hits"] and {h["meta"]["slug"] for h in faq["hits"]} == {"faq"}


def test_exact_and_hnsw_vector_sql_are_different_statements():
    """asyncpg caches prepared statements by text and Postgres replays a generic plan after 5 runs.
    A seq-scan plan built under exact steering must never be reused for the HNSW variant (it gave a
    fake 100% recall at 0.9 ms in bench/retrieval_index.py)."""
    hnsw, _ = chunk_vector_sql(RetrievalConfig(vector_index="hnsw"), Filters())
    exact, _ = chunk_vector_sql(RetrievalConfig(vector_index="exact"), Filters())
    assert hnsw != exact
