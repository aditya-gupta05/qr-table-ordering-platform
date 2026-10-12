"""Ingestion tests: parsing/cleaning (pure) and the DB pipeline with a fake embedder."""

import os
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.retrieval.chunking import TokenCounter
from app.retrieval.clean import clean_markdown, clean_pdf_pages
from app.retrieval.ingest import ChunkConfigDef, build_linker, load_menu, run_ingest
from app.retrieval.parse import parse_markdown, parse_pdf
from tests.retrieval_helpers import FakeEmbedder, toy_tokenizer

BGE = "BAAI/bge-small-en-v1.5"
ARCTIC = "snowflake/snowflake-arctic-embed-xs"
CORPUS = Path(__file__).resolve().parent.parent / "corpus"

FAQ = """# Test FAQ

## Practices

### Is the fryer shared?
Yes, the Chicken Satay and the Crispy Calamari come from different stations, but the fryer oil is
shared between the calamari and the onion rings. Ask a server about allergies before you order.

### What is the corkage fee?
Corkage is twenty five dollars per bottle and a table may bring two bottles at most.
"""
POLICY = """# Test Policies

## Large parties

Parties of eight or more are charged an automatic gratuity. A card guarantee is taken when the
booking is made and cancellations inside one day are charged per guest.
"""


# --- pure: cleaning ------------------------------------------------------------------------------


def test_clean_markdown_normalises_whitespace():
    assert clean_markdown("a  \r\n\r\n\r\n\r\nb\t\n") == "a\n\nb\n"


def test_pdf_cleaning_strips_header_footer_and_dot_leaders_but_keeps_prices():
    pages = [
        "MENU HEADER\nStarters\nChicken Satay\n$11.50\n. . . . . . . . . .\n"
        "Grilled skewers with\nSlow-\nroasted tomato.\nShop | 88 Street\nPage 1 of 3",
        "MENU HEADER\nMains\nPizza\n$17.00\nDough and sauce.\nShop | 88 Street\nPage 2 of 3",
        "MENU HEADER\nDesserts\nKulfi\n$9.00\nCardamom, pistachio.\nShop | 88 Street\nPage 3 of 3",
    ]
    res = clean_pdf_pages(pages)
    assert "MENU HEADER" not in res.text and "Page 2 of 3" not in res.text
    assert "88 Street" not in res.text
    assert ". . . ." not in res.text
    # prices must survive (digits-normalised, "$#.#" looks "repeated" -- must not be dropped)
    for price in ("$11.50", "$17.00", "$9.00"):
        assert price in res.text
    assert "Slow-roasted tomato." in res.text
    assert [p for _, _, p in res.page_spans] == [1, 2, 3]
    s, e, _ = res.page_spans[1]
    assert "Pizza" in res.text[s:e] and "Satay" not in res.text[s:e]
    assert res.stats["repeated_lines"] >= 6


def test_real_menu_pdf_parses_and_keeps_name_with_price():
    doc = parse_pdf(CORPUS / "menu.pdf", "menu_pdf")
    assert doc.metadata["pages"] >= 3
    assert "Page 1 of" not in doc.content and "SEASONAL MENU" not in doc.content
    assert "Chicken Satay\n$11.50" in doc.content
    raw = parse_pdf(CORPUS / "menu.pdf", "menu_pdf", clean=False)
    assert "Page 1 of" in raw.content and ". . . ." in raw.content
    assert len(doc.content) < len(raw.content)


def test_corpus_docs_have_expected_source_types_and_stable_hash():
    a = parse_markdown(CORPUS / "faq.md", "faq")
    b = parse_markdown(CORPUS / "faq.md", "faq")
    assert a.content_hash == b.content_hash and a.title.startswith("Lantern Kitchen")


def test_linker_matches_names_case_insensitively_with_word_boundaries():
    from app.retrieval.ingest import MenuRow

    menu = [
        MenuRow(1, "Chicken Satay", "Starters", "", (), 0),
        MenuRow(2, "Pinot Grigio (glass)", "Drinks", "", (), 0),
        MenuRow(3, "Edamame", "Starters", "", (), 0),
    ]
    link = build_linker(menu)
    assert link("Try the CHICKEN satay!") == [1]
    assert link("a glass of pinot grigio") == [2]
    assert link("edamames and chicken satays") == []  # no partial-word matches


# --- DB pipeline ---------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def engine(migrated_test_db):
    eng = create_async_engine(migrated_test_db)
    await _wipe(eng)
    yield eng
    await _wipe(eng)
    await eng.dispose()


async def _wipe(eng):
    async with eng.begin() as c:
        await c.execute(text("DELETE FROM documents"))
        await c.execute(text("DELETE FROM chunk_configs"))
        await c.execute(text("DELETE FROM menu_item_embeddings"))


def _write(tmp_path, faq=FAQ, policy=POLICY):
    (tmp_path / "faq.md").write_text(faq)
    (tmp_path / "policies.md").write_text(policy)
    return [
        parse_markdown(tmp_path / "faq.md", "faq"),
        parse_markdown(tmp_path / "policies.md", "policy"),
    ]


CFG = ChunkConfigDef("t_rec", "recursive_md", 40, 6)
CFG2 = ChunkConfigDef("t_fixed", "fixed_token", 30, 5)


async def _counts(eng):
    async with eng.connect() as c:
        r = await c.execute(
            text(
                "SELECT (SELECT count(*) FROM documents), (SELECT count(*) FROM chunks),"
                " (SELECT count(*) FROM chunk_embeddings),"
                " (SELECT count(*) FROM menu_item_embeddings)"
            )
        )
        return tuple(r.one())


async def _ingest(eng, docs, cfgs=(CFG,), models=(BGE,), **kw):
    emb = {m: FakeEmbedder(m) for m in models}
    counter = TokenCounter(toy_tokenizer(FAQ, POLICY, FAQ.replace("fryer", "grill")))
    return await run_ingest(
        eng,
        docs=docs,
        configs=list(cfgs),
        models=list(models),
        embedders=emb,
        counter=counter,
        **kw,
    )


async def test_ingest_inserts_then_is_idempotent(engine, tmp_path):
    docs = _write(tmp_path)
    rep = await _ingest(engine, docs)
    docs_n, chunks_n, emb_n, menu_emb_n = await _counts(engine)
    assert docs_n == 2 and chunks_n >= 2 and emb_n == chunks_n and menu_emb_n == 42
    assert rep.total_changes > 0 and rep.menu_embeddings_upserted == 42

    again = await _ingest(engine, docs)
    assert again.total_changes == 0
    assert await _counts(engine) == (docs_n, chunks_n, emb_n, menu_emb_n)


async def test_changed_document_replaces_only_its_chunks_and_cascades_embeddings(engine, tmp_path):
    await _ingest(engine, _write(tmp_path))
    async with engine.connect() as c:
        before = {
            r.slug: r.n
            for r in await c.execute(
                text(
                    "SELECT d.slug, count(*) n FROM chunks ch"
                    " JOIN documents d ON d.id=ch.document_id GROUP BY d.slug"
                )
            )
        }
        old_policy_ids = [
            r.id
            for r in await c.execute(
                text(
                    "SELECT ch.id FROM chunks ch JOIN documents d ON d.id=ch.document_id"
                    " WHERE d.slug='policies'"
                )
            )
        ]
    docs = _write(tmp_path, policy=POLICY + "\nWe also host private dinners in the back room.\n")
    rep = await _ingest(engine, docs)
    assert rep.documents_updated == 1 and rep.documents_unchanged == 1
    assert rep.chunks_deleted == before["policies"]
    async with engine.connect() as c:
        faq_n = (
            await c.execute(
                text(
                    "SELECT count(*) FROM chunks ch JOIN documents d ON d.id=ch.document_id"
                    " WHERE d.slug='faq'"
                )
            )
        ).scalar_one()
        orphan = (
            await c.execute(
                text("SELECT count(*) FROM chunk_embeddings WHERE chunk_id = ANY(:i)"),
                {"i": old_policy_ids},
            )
        ).scalar_one()
        total_chunks, total_emb = (
            await c.execute(
                text(
                    "SELECT (SELECT count(*) FROM chunks), (SELECT count(*) FROM chunk_embeddings)"
                )
            )
        ).one()
    assert faq_n == before["faq"]
    assert orphan == 0 and total_emb == total_chunks


async def test_second_model_only_adds_embeddings(engine, tmp_path):
    docs = _write(tmp_path)
    await _ingest(engine, docs, models=(BGE,))
    _, chunks_n, emb_n, _ = await _counts(engine)
    rep = await _ingest(engine, docs, models=(BGE, ARCTIC))
    assert rep.chunks_inserted == 0 and rep.documents_updated == 0
    assert rep.embeddings_inserted == chunks_n
    assert (await _counts(engine))[2] == 2 * chunks_n
    assert (await _ingest(engine, docs, models=(BGE, ARCTIC))).total_changes == 0


async def test_chunks_are_linked_to_mentioned_menu_items_and_prune_works(engine, tmp_path):
    docs = _write(tmp_path)
    await _ingest(engine, docs, cfgs=(CFG, CFG2), models=(BGE, ARCTIC))
    async with engine.connect() as c:
        satay = (
            await c.execute(text("SELECT id FROM menu_items WHERE name='Chicken Satay'"))
        ).scalar_one()
        linked = (
            await c.execute(
                text("SELECT count(*) FROM chunks WHERE :i = ANY(menu_item_ids)"), {"i": satay}
            )
        ).scalar_one()
    assert linked >= 1
    rep = await _ingest(engine, docs, cfgs=(CFG,), models=(BGE,), prune=True)
    assert rep.pruned_configs == 1 and rep.pruned_embeddings > 0
    async with engine.connect() as c:
        names = (await c.execute(text("SELECT name FROM chunk_configs"))).scalars().all()
        models = (
            (await c.execute(text("SELECT DISTINCT model FROM chunk_embeddings"))).scalars().all()
        )
    assert names == ["t_rec"] and models == [BGE]


async def test_changed_config_params_rechunk(engine, tmp_path):
    docs = _write(tmp_path)
    await _ingest(engine, docs)
    rep = await _ingest(engine, docs, cfgs=(ChunkConfigDef("t_rec", "recursive_md", 25, 4),))
    assert rep.chunk_configs_changed == 1 and rep.chunks_deleted > 0 and rep.chunks_inserted > 0


async def test_menu_embeddings_exclude_allergens_from_text(engine):
    from app.retrieval.ingest import menu_embed_text

    async with engine.connect() as c:
        menu = await load_menu(c)
    satay = next(m for m in menu if m.name == "Chicken Satay")
    t = menu_embed_text(satay)
    assert "peanut" in t.lower()  # in the *description* wording, not from the allergens column
    assert "allergen" not in t.lower()
    assert t.startswith("Chicken Satay. Starters.") and "Dietary: halal, dairy free." in t


@pytest.mark.skipif(not os.environ.get("RUN_MODEL_TESTS"), reason="downloads models; opt in")
def test_real_model_smoke():
    from app.retrieval.embedder import get_embedder, load_chunk_tokenizer

    emb = get_embedder(BGE)
    v = emb.embed_query("is the fryer shared")
    d = emb.embed_documents(["The fryer oil is shared between the calamari and the onion rings."])[
        0
    ]
    assert len(v) == 384 and abs(sum(x * x for x in v) - 1) < 1e-3
    assert sum(a * b for a, b in zip(v, d, strict=True)) > 0.5
    c = TokenCounter(load_chunk_tokenizer())
    assert c.count("hello world") == 2
