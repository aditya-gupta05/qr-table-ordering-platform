"""Semantic answer cache: bypass rules, versioned keys, shadow mode never serves."""

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.chat import answer_cache as ac
from app.db import SessionLocal
from app.retrieval.chunking import TokenCounter
from app.retrieval.ingest import run_ingest
from app.retrieval.parse import parse_markdown
from tests.chat_helpers import (
    FakeLLM,
    clean_chat_tables,
    last_tool_json,
    make_deps,
    new_session,
    reply,
    respond,
    rows,
    run_one,
    tc,
)
from tests.retrieval_helpers import FakeEmbedder, toy_tokenizer
from tests.test_chat_tools import BGE, CFG, DOC_FAQ, DOC_GUIDE, DOC_PRACTICES

BUMP = (
    "UPDATE menu_items SET price_cents = price_cents {} 1 "
    "WHERE id = (SELECT min(id) FROM menu_items)"
)
Q = "How much is the corkage fee?"


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def corpus(migrated_test_db, tmp_path_factory):
    eng = create_async_engine(migrated_test_db)
    d = tmp_path_factory.mktemp("cache_corpus")
    for name, body in (("faq", DOC_FAQ), ("allergen-kitchen-practices", DOC_PRACTICES),
                       ("dish-guide", DOC_GUIDE)):  # fmt: skip
        (d / f"{name}.md").write_text(body)
    docs = [
        parse_markdown(d / "faq.md", "faq"),
        parse_markdown(d / "allergen-kitchen-practices.md", "allergen_practices"),
        parse_markdown(d / "dish-guide.md", "dish_guide"),
    ]
    async with eng.begin() as c:
        for t in ("documents", "chunk_configs", "menu_item_embeddings"):
            await c.execute(text(f"DELETE FROM {t}"))  # noqa: S608
    await run_ingest(
        eng, docs=docs, configs=[CFG], models=[BGE], embedders={BGE: FakeEmbedder(BGE)},
        counter=TokenCounter(toy_tokenizer(DOC_FAQ, DOC_PRACTICES, DOC_GUIDE)),
    )  # fmt: skip
    yield
    async with eng.begin() as c:
        for t in ("documents", "chunk_configs", "menu_item_embeddings"):
            await c.execute(text(f"DELETE FROM {t}"))  # noqa: S608
    await eng.dispose()


@pytest.fixture(autouse=True)
async def clean(corpus):
    await clean_chat_tables()
    yield
    await clean_chat_tables()


def info_script():
    """search_info, then an answer citing the first passage."""

    def answer(messages):
        p = last_tool_json(messages)["passages"][0]
        return reply(respond("Corkage is charged per bottle.", items=[], passages=[p["id"]]))

    return [reply(tc("search_info", query="corkage fee")), answer]


# --- pure rules ---


def test_eligibility_bypass_rules():
    ok = dict(is_first_turn=True, declared=())
    assert ac.question_eligible(Q, **ok)
    assert not ac.question_eligible(Q, is_first_turn=False, declared=())
    assert not ac.question_eligible(Q, is_first_turn=True, declared=("peanut",))
    assert not ac.question_eligible("Is the corkage ok with my nut allergy?", **ok)
    assert not ac.question_eligible("I'm vegan, what is the corkage?", **ok)
    assert not ac.question_eligible("Please add the satay to my order", **ok)
    assert not ac.question_eligible("Can you call a waiter about corkage?", **ok)


def test_storable_rules():
    base = dict(tool_names=["search_info"], status="answered", outcome="answered", violations=0,
                actions=0, allergen_sensitive=False)  # fmt: skip
    assert ac.answer_storable(**base)
    for bad in (
        {"tool_names": ["search_menu"]},
        {"tool_names": ["search_info", "search_menu"]},
        {"tool_names": []},
        {"status": "not_found", "outcome": "not_found"},
        {"violations": 1},
        {"actions": 1},
        {"allergen_sensitive": True},
    ):
        assert not ac.answer_storable(**(base | bad)), bad


def test_normalize():
    assert (
        ac.normalize_question("  How MUCH is the   corkage fee?! ") == "how much is the corkage fee"
    )


# --- end to end ---


async def test_on_mode_serves_second_session_with_zero_llm_calls():
    llm = FakeLLM(info_script())
    deps = make_deps(llm, answer_cache="on")
    r1, m1, _ = await run_one(deps, await new_session(deps), Q)
    assert r1.outcome == "answered" and not r1.served_from_cache
    assert (await rows("SELECT count(*) FROM answer_cache"))[0][0] == 1
    calls = len(llm.calls)
    r2, m2, _ = await run_one(deps, await new_session(deps, "T2"), Q)
    assert r2.served_from_cache and r2.llm_calls == 0 and len(llm.calls) == calls
    assert m2["text"] == m1["text"] and m2["citations"] == m1["citations"]
    assert m2["id"] != m1["id"] and r2.outcome == "answered"
    assert (await rows("SELECT hits FROM answer_cache"))[0][0] == 1


async def test_not_served_after_menu_or_version_change_and_not_for_allergen_question():
    llm = FakeLLM(
        info_script() + info_script() + [reply(respond("Please ask staff.", "needs_staff"))]
    )
    deps = make_deps(llm, answer_cache="on")
    await run_one(deps, await new_session(deps), Q)
    base = len(llm.calls)

    # menu change -> new menu_version -> miss -> fresh answer
    await _sql(BUMP.format("+"))
    try:
        await run_one(deps, await new_session(deps), Q)
    finally:
        await _sql(BUMP.format("-"))
    assert len(llm.calls) == base + 2

    # allergen cue bypasses the cache even though the text is close to a cached question
    n = len(llm.calls)
    r, _, _ = await run_one(
        deps, await new_session(deps), "Corkage fee and I have a peanut allergy?"
    )
    assert not r.served_from_cache and len(llm.calls) == n + 1


async def _sql(sql):
    async with SessionLocal() as db:
        await db.execute(text(sql))
        await db.commit()


async def test_key_changes_with_prompt_corpus_and_menu_version():
    emb = FakeEmbedder().embed_query(ac.normalize_question(Q))
    async with SessionLocal() as db:
        v = await ac.current_versions(db, "pv1", "deepseek-flash")
        await ac.store(db, Q, emb, {"text": "t", "status": "answered", "citations": []}, v)
        await db.commit()
        assert (await ac.lookup(db, emb, v, 0.95)) is not None
        for changed in (
            ac.Versions("pv2", v.model, v.corpus_version, v.menu_version),
            ac.Versions(v.prompt_version, "other-model", v.corpus_version, v.menu_version),
            ac.Versions(v.prompt_version, v.model, "x" * 32, v.menu_version),
            ac.Versions(v.prompt_version, v.model, v.corpus_version, "y" * 32),
        ):
            assert await ac.lookup(db, emb, changed, 0.95) is None
        other = FakeEmbedder().embed_query("what time do you close on sundays")
        assert await ac.lookup(db, other, v, 0.95) is None  # below threshold
    # TTL: an old row is invisible
    await _sql("UPDATE answer_cache SET created_at = now() - interval '25 hours'")
    async with SessionLocal() as db:
        assert await ac.lookup(db, emb, v, 0.95) is None


async def test_shadow_never_serves_but_records_pairs():
    llm = FakeLLM(info_script() + info_script())
    deps = make_deps(llm, answer_cache="shadow")
    await run_one(deps, await new_session(deps), Q)
    assert (await rows("SELECT count(*) FROM answer_cache"))[0][0] == 1
    assert (await rows("SELECT count(*) FROM answer_cache_shadow"))[0][0] == 0
    r2, m2, _ = await run_one(deps, await new_session(deps, "T2"), Q)
    assert not r2.served_from_cache and len(llm.calls) == 4  # full pipeline ran again
    (pair,) = await rows("SELECT similarity, fresh_message_id FROM answer_cache_shadow")
    assert pair.similarity >= 0.95 and pair.fresh_message_id == m2["id"]
    assert (await rows("SELECT count(*) FROM answer_cache"))[0][0] == 1  # no duplicate stored


async def test_off_mode_touches_nothing():
    llm = FakeLLM(info_script())
    deps = make_deps(llm)
    await run_one(deps, await new_session(deps), Q)
    assert (await rows("SELECT count(*) FROM answer_cache"))[0][0] == 0
