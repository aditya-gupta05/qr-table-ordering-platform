"""Tool implementations against the seeded test DB, with a fake embedder and a fake reranker."""

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.chat import tools as T
from app.chat.settings import ChatSettings
from app.chat.tool_schemas import MAX_ADD_TO_CART_PER_TURN
from app.retrieval.cache import QueryEmbedCache
from app.retrieval.chunking import TokenCounter
from app.retrieval.config import Filters, RetrievalConfig
from app.retrieval.ingest import ChunkConfigDef, run_ingest
from app.retrieval.parse import parse_markdown
from app.retrieval.search import list_menu_items
from tests.retrieval_helpers import FakeEmbedder, toy_tokenizer

BGE = "BAAI/bge-small-en-v1.5"
CFG = ChunkConfigDef("t_tools", "recursive_md", 40, 6)
DOC_FAQ = """# FAQ

## Corkage

Corkage is twenty five dollars per bottle and a table may bring two bottles at most.

## Parking

Free parking is available behind the restaurant after six in the evening for all guests.

## Reservations

Reservations can be made by phone or online for groups of up to eight people at a time.
"""
DOC_PRACTICES = """# Allergen and Kitchen Practices

## Cross-contact

Cross-contact is always possible in our busy kitchen because shared fryers and woks are used.
We cannot promise that a dish is completely free from traces of an allergen.
"""
DOC_GUIDE = """# Dish Guide

## Starters

### Chicken Satay

Thigh meat is marinated overnight and grilled over charcoal.
The Chicken Satay comes with peanut sauce in a pot.

### Edamame

Soybeans in the pod are steamed for four minutes. The Edamame is a quick first order.
"""
PEANUT_ITEMS = {"Chicken Satay", "Satay Tofu Skewers", "Pad Thai"}
NUT_ITEMS_SQL = "SELECT name FROM menu_items WHERE allergens && ARRAY['peanut','tree_nut']"


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def db(migrated_test_db, tmp_path_factory):
    eng = create_async_engine(migrated_test_db)
    d = tmp_path_factory.mktemp("corpus")
    for name, body in (
        ("faq", DOC_FAQ),
        ("allergen-kitchen-practices", DOC_PRACTICES),
        ("dish-guide", DOC_GUIDE),
    ):
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
        eng,
        docs=docs,
        configs=[CFG],
        models=[BGE],
        embedders={BGE: FakeEmbedder(BGE)},
        counter=TokenCounter(toy_tokenizer(DOC_FAQ, DOC_PRACTICES, DOC_GUIDE)),
    )
    yield eng
    async with eng.begin() as c:
        for t in ("documents", "chunk_configs", "menu_item_embeddings"):
            await c.execute(text(f"DELETE FROM {t}"))  # noqa: S608
    await eng.dispose()


@pytest.fixture
def fake_rerank(monkeypatch):
    """Cross-encoder stub: every candidate scores `.score`, minus 0.01 per rank."""

    class R:
        score = -1.0
        calls = 0

    async def fake(model, query, docs):
        R.calls += 1
        return [R.score - 0.01 * i for i in range(len(docs))]

    R.score, R.calls = -1.0, 0
    monkeypatch.setattr("app.retrieval.search.rerank_scores", fake)
    return R


CATEGORIES = ["Bowls & Curries", "Desserts", "Drinks", "Mains", "Sides", "Starters"]


async def run(db, name, args, *, message="", declared=(), settings=None, turn=None, **env_kw):
    """Run one tool call on a fresh connection; returns (result, turn)."""
    turn = turn or T.TurnState()
    async with db.connect() as conn:
        env = T.ToolEnv(
            conn=conn,
            settings=settings or ChatSettings(),
            session=T.SessionContext("T3", tuple(declared), message),
            turn=turn,
            categories=CATEGORIES,
            embedder=FakeEmbedder(BGE),
            cache=QueryEmbedCache(8),
            counter=TokenCounter(toy_tokenizer(DOC_FAQ, DOC_PRACTICES, DOC_GUIDE)),
            retrieval_base=RetrievalConfig(chunk_config="t_tools"),
            **env_kw,
        )
        return await T.run_tool(env, name, args), turn


def menu_args(**kw):
    return {"query": "", "exclude_allergens": [], "dietary": [], "categories": [],
            "other_allergy_terms": [], "max_price_cents": 0, "sort": "relevance"} | kw  # fmt: skip


async def names_with(db, sql: str) -> set[str]:
    async with db.connect() as c:
        return {r[0] for r in await c.execute(text(sql))}


# --- search_menu: allergens ---


async def test_exclude_peanut_never_returns_peanut_items(db, fake_rerank):
    r, turn = await run(
        db,
        "search_menu",
        menu_args(query="chicken satay peanut sauce", exclude_allergens=["peanut"]),
    )
    assert r.ok and r.payload["items"]
    assert all("peanut" not in i["allergens"] for i in r.payload["items"])
    assert not {i["name"] for i in r.payload["items"]} & PEANUT_ITEMS
    assert r.payload["filters_applied"]["exclude_allergens"] == ["peanut"]
    assert turn.allergen_sensitive and turn.grounded
    assert (
        r.result_item_ids
        == [i["id"] for i in r.payload["items"]]
        == sorted(turn.item_ids, key=r.result_item_ids.index)
    )
    # control: the same query without the filter does return the satay
    r2, _ = await run(db, "search_menu", menu_args(query="chicken satay peanut sauce"))
    assert "Chicken Satay" in {i["name"] for i in r2.payload["items"]}


async def test_allergic_to_nuts_widens_tree_nut_only_args(db, fake_rerank):
    r, turn = await run(
        db, "search_menu", menu_args(query="dessert", exclude_allergens=["tree_nut"]),
        message="I'm allergic to nuts, what desserts do you have?",
    )  # fmt: skip
    p = r.payload
    assert p["filters_applied"]["exclude_allergens"] == ["peanut", "tree_nut"]
    assert p["widened_from_message"] == ["peanut"] and "widened_note" in p
    assert r.widened["from_message"] == ["peanut"]
    nutty = await names_with(db, NUT_ITEMS_SQL)
    assert p["items"] and not {i["name"] for i in p["items"]} & nutty
    assert turn.allergen_sensitive


async def test_session_declared_allergens_apply_to_later_turns(db, fake_rerank):
    r, _ = await run(
        db,
        "search_menu",
        menu_args(query="pasta gnocchi"),
        message="what pasta do you have?",
        declared=["egg"],
    )
    assert r.payload["filters_applied"]["exclude_allergens"] == ["egg"]
    assert all("egg" not in i["allergens"] for i in r.payload["items"])
    assert r.payload["widened_from_message"] == ["egg"]  # reported to the model either way


async def test_untracked_terms_reported_not_guessed(db, fake_rerank):
    r, _ = await run(db, "search_menu", menu_args(query="dessert"), message="I'm allergic to kiwi")
    assert r.payload["untracked_terms"] == ["kiwi"]
    assert "Not tracked" in r.payload["note"] and "call_waiter" in r.payload["note"]
    assert r.payload["filters_applied"]["exclude_allergens"] == []
    r, _ = await run(db, "search_menu", menu_args(query="x", other_allergy_terms=["lupin"]))
    assert r.payload["untracked_terms"] == ["lupin"]


async def test_dietary_filter_and_empty_result_note(db, fake_rerank):
    r, _ = await run(db, "search_menu", menu_args(query="curry noodle", dietary=["vegan"]))
    assert r.payload["items"] and all("vegan" in i["dietary"] for i in r.payload["items"])
    r, _ = await run(
        db,
        "search_menu",
        menu_args(
            query="x",
            dietary=["vegan"],
            exclude_allergens=["soy", "sesame", "celery", "gluten"],
            categories=["Mains"],
        ),
    )
    assert r.payload["items"] == [] and r.payload["note"].startswith("No available dish")


# --- search_menu: categories, price, availability ---


async def test_categories_filter_and_unknown_category_rejected(db, fake_rerank):
    r, _ = await run(db, "search_menu", menu_args(query="sweet creamy", categories=["Desserts"]))
    assert r.payload["items"] and {i["category"] for i in r.payload["items"]} == {"Desserts"}
    r, _ = await run(db, "search_menu", menu_args(query="sweet", categories=["Dessert"]))
    assert not r.ok and r.payload["error"] == "invalid_arguments"
    assert "Desserts" in r.payload["details"][0]


async def test_price_asc_returns_cheapest_available(db):
    async with db.begin() as c:
        await c.execute(
            text("UPDATE menu_items SET is_available=false WHERE name='Steamed Jasmine Rice'")
        )
    try:
        r, _ = await run(db, "search_menu", menu_args(sort="price_asc"))
        prices = [i["price"] for i in r.payload["items"]]
        assert r.signals["route"] == "list" and r.payload["evidence_strength"] == "not_applicable"
        assert r.payload["items"][0]["name"] == "Iced Jasmine Tea" and prices[0] == "$4.50"
        assert "Steamed Jasmine Rice" not in {i["name"] for i in r.payload["items"]}
        assert prices == sorted(prices, key=lambda p: float(p[1:]))
        cents = await names_with(
            db, "SELECT min(price_cents)::text FROM menu_items WHERE is_available"
        )
        assert cents == {"450"}
    finally:
        async with db.begin() as c:
            await c.execute(
                text("UPDATE menu_items SET is_available=true WHERE name='Steamed Jasmine Rice'")
            )


async def test_price_desc_with_cap_and_filters(db):
    r, _ = await run(
        db,
        "search_menu",
        menu_args(sort="price_desc", max_price_cents=1000, exclude_allergens=["dairy"]),
    )
    prices = [float(i["price"][1:]) for i in r.payload["items"]]
    assert prices and max(prices) <= 10.0 and prices == sorted(prices, reverse=True)
    assert all("dairy" not in i["allergens"] for i in r.payload["items"])
    assert r.payload["filters_applied"]["max_price"] == "$10.00"
    assert "query text was not used" not in r.payload["note"]  # empty query: nothing ignored
    r, _ = await run(db, "search_menu", menu_args(query="soup", sort="price_asc"))
    assert "query text was not used" in r.payload["note"]


async def test_relevance_search_with_price_cap(db, fake_rerank):
    r, _ = await run(db, "search_menu", menu_args(query="soup bowl sorbet", max_price_cents=900))
    assert r.payload["items"] and all(float(i["price"][1:]) <= 9.0 for i in r.payload["items"])


async def test_list_menu_items_reuses_menu_where(db):
    async with db.connect() as c:
        hits = await list_menu_items(
            c,
            Filters(exclude_allergens=("peanut",), dietary=("vegan",), categories=("Starters",)),
            "price_desc",
            limit=20,
        )
        assert hits and all(
            h.meta["category"] == "Starters"
            and "peanut" not in h.meta["allergens"]
            and "vegan" in h.meta["dietary_tags"]
            for h in hits
        )
        assert [h.meta["price_cents"] for h in hits] == sorted(
            (h.meta["price_cents"] for h in hits), reverse=True
        )
        n_all = len(await list_menu_items(c, Filters(), "name", limit=100))
        assert n_all == 42
        with pytest.raises(ValueError):
            await list_menu_items(c, None, "popularity")


# --- evidence strength ---


async def test_evidence_strong_weak_and_signals(db, fake_rerank):
    fake_rerank.score = -1.0
    r, _ = await run(db, "search_info", {"query": "corkage bottle", "topics": []})
    assert r.payload["evidence_strength"] == "strong"
    assert r.signals["ce_top3"] == pytest.approx([-1.0, -1.01, -1.02])
    assert r.signals["best_cosine"] is not None and r.signals["top1_rrf"] > 0
    fake_rerank.score = -8.0
    r, _ = await run(db, "search_info", {"query": "corkage bottle", "topics": []})
    assert r.payload["evidence_strength"] == "weak"
    fake_rerank.score = -1.0
    r, _ = await run(db, "search_menu", menu_args(query="soup"))
    assert r.payload["evidence_strength"] == "strong"
    fake_rerank.score = -9.0
    r, _ = await run(db, "search_menu", menu_args(query="soup"))
    assert r.payload["evidence_strength"] == "weak"


async def test_evidence_signal_can_be_switched_off(db, fake_rerank):
    s = ChatSettings(abstain_signal=False)
    r, _ = await run(db, "search_info", {"query": "corkage bottle", "topics": []}, settings=s)
    assert r.payload["evidence_strength"] == "unknown" and fake_rerank.calls == 0
    assert r.payload["passages"]  # still returns passages
    assert r.signals["ce_top3"] == [] and r.signals["top1_rrf"] > 0


# --- search_info ---


async def test_search_info_returns_citable_passages_within_budget(db, fake_rerank):
    r, turn = await run(
        db, "search_info", {"query": "corkage bottle parking reservations", "topics": ["faq"]}
    )
    ps = r.payload["passages"]
    assert ps and set(r.result_passage_ids) == {p["id"] for p in ps} == turn.passage_ids
    assert all(p["source"] == "FAQ" for p in ps) and all(
        set(p) == {"id", "source", "section", "text"} for p in ps
    )
    assert all(turn.passages[p["id"]].source_type == "faq" for p in ps)
    assert turn.grounded and not turn.item_ids

    counter = TokenCounter(toy_tokenizer(DOC_FAQ, DOC_PRACTICES, DOC_GUIDE))
    total = sum(counter.count(p["text"]) for p in ps)
    assert total == r.signals["context_tokens"] <= 1024

    tight = ChatSettings(info_context_tokens=30)
    r2, _ = await run(
        db,
        "search_info",
        {"query": "corkage bottle parking reservations", "topics": []},
        settings=tight,
    )
    used = sum(counter.count(p["text"]) for p in r2.payload["passages"])
    assert 1 <= len(r2.payload["passages"]) < len(r.payload["passages"])
    assert r2.signals["dropped_for_budget"] > 0 and used <= 30


async def test_search_info_topics_restrict_sources(db, fake_rerank):
    r, turn = await run(
        db,
        "search_info",
        {"query": "cross-contact fryers kitchen", "topics": ["allergen_practices"]},
    )
    assert r.payload["passages"] and {turn.passages[i].source_type for i in turn.passage_ids} == {
        "allergen_practices"
    }
    r, _ = await run(db, "search_info", {"query": "zzzz qqqq", "topics": ["policy"]})
    assert r.payload["passages"] == [] and "do not have" in r.payload["note"]
    assert r.payload["evidence_strength"] == "none"


# --- get_item ---


async def test_get_item_unknown_and_known_with_guide(db):
    r, _ = await run(db, "get_item", {"item_id": 987654})
    assert not r.ok and r.payload == {"error": "unknown_item"}
    async with db.connect() as c:
        iid = (
            await c.execute(text("SELECT id FROM menu_items WHERE name='Chicken Satay'"))
        ).scalar_one()
    r, turn = await run(db, "get_item", {"item_id": iid})
    p = r.payload
    assert (
        p["name"] == "Chicken Satay"
        and p["price"] == "$11.50"
        and p["allergens"] == ["peanut", "soy"]
    )
    assert p["available"] is True and p["guide_chunk_id"] in turn.passage_ids
    assert p["guide"] and len(p["guide"]) <= 400 and "charcoal" in p["guide"]
    assert 1150 in turn.prices_cents and turn.items[iid].name == "Chicken Satay"


async def test_get_item_by_exact_and_fuzzy_name(db):
    r, _ = await run(db, "get_item", {"item_id": 0, "name": "pad thai"})
    assert r.ok and r.payload["name"] == "Pad Thai" and "peanut" in r.payload["allergens"]
    r, _ = await run(db, "get_item", {"item_id": 0, "name": "margherita"})
    assert r.payload["name"] == "Margherita Pizza"
    r, _ = await run(db, "get_item", {"item_id": 0, "name": "margarita pizza"})
    assert r.payload["name"] == "Margherita Pizza"
    r, _ = await run(db, "get_item", {"item_id": 0, "name": "Pinot Grigio"})
    assert r.payload["name"] == "Pinot Grigio (glass)"


async def test_get_item_ambiguous_returns_candidates_not_a_guess(db):
    r, turn = await run(db, "get_item", {"item_id": 0, "name": "chicken"})
    assert r.ok and r.payload["ambiguous"] is True and 2 <= len(r.payload["candidates"]) <= 3
    assert {"id", "name", "category"} == set(r.payload["candidates"][0])
    assert not turn.grounded and not turn.items


async def test_get_item_unknown_name_does_not_echo_input(db):
    r, _ = await run(db, "get_item", {"item_id": 0, "name": "zzqx unicorn burger"})
    assert not r.ok and r.payload == {"error": "unknown_item"}


async def test_get_item_by_name_is_not_hidden_by_declared_allergens(db):
    r, turn = await run(
        db,
        "get_item",
        {"item_id": 0, "name": "Pad Thai"},
        message="I'm allergic to peanuts",
        declared=("peanut",),
    )
    assert r.payload["name"] == "Pad Thai" and r.payload["allergen_conflict"] == ["peanut"]
    assert turn.allergen_sensitive
    r, _ = await run(db, "get_item", {"item_id": 0, "name": "Pad Thai"}, message="hi")
    assert r.payload["allergen_conflict"] == []
    r, _ = await run(
        db, "get_item", {"item_id": 0, "name": "Pad Thai"}, message="I have a nut allergy"
    )
    assert r.payload["allergen_conflict"] == ["peanut"]


async def test_get_item_needs_exactly_one_of_id_or_name(db):
    for args in ({"item_id": 0, "name": ""}, {"item_id": 5, "name": "Pad Thai"}):
        r, _ = await run(db, "get_item", args)
        assert not r.ok and r.payload["error"] == "invalid_arguments"


# --- add_to_cart / call_waiter ---


async def item_id(db, name):
    async with db.connect() as c:
        return (
            await c.execute(text("SELECT id FROM menu_items WHERE name=:n"), {"n": name})
        ).scalar_one()


async def test_add_to_cart_proposal_and_errors(db):
    pad = await item_id(db, "Pad Thai")
    r, turn = await run(
        db, "add_to_cart", {"item_id": pad, "qty": 2, "note": "extra lime, call me at 780-555-1234"}
    )
    p = r.payload
    assert (
        p["status"] == "proposed" and p["line_total"] == "$40.00" and p["allergen_conflict"] == []
    )
    act = turn.actions[0]
    assert act["type"] == "add_to_cart" and act["action_id"] == p["action_id"] and act["qty"] == 2
    assert act["price_cents"] == 2000 and act["note"] == "extra lime, call me at [phone]"
    assert (
        act["allergens"] == ["egg", "fish", "peanut", "shellfish"] and act["status"] == "proposed"
    )
    assert 4000 in turn.prices_cents  # line total may be quoted back

    r, _ = await run(db, "add_to_cart", {"item_id": 987654, "qty": 1, "note": ""})
    assert r.payload == {"error": "unknown_item"}
    async with db.begin() as c:
        await c.execute(text("UPDATE menu_items SET is_available=false WHERE id=:i"), {"i": pad})
    try:
        r, turn = await run(db, "add_to_cart", {"item_id": pad, "qty": 1, "note": ""})
        assert not r.ok and r.payload == {"error": "unavailable"} and turn.actions == []
    finally:
        async with db.begin() as c:
            await c.execute(text("UPDATE menu_items SET is_available=true WHERE id=:i"), {"i": pad})


async def test_add_to_cart_allergen_conflict_from_session_and_current_message(db):
    pad = await item_id(db, "Pad Thai")
    r, turn = await run(
        db, "add_to_cart", {"item_id": pad, "qty": 1, "note": ""}, declared=["peanut", "dairy"]
    )
    assert r.payload["allergen_conflict"] == ["peanut"] and "peanut" in r.payload["instruction"]
    assert turn.actions[0]["allergen_conflict"] == ["peanut"] and turn.allergen_sensitive
    r, _ = await run(
        db,
        "add_to_cart",
        {"item_id": pad, "qty": 1, "note": ""},
        message="I'm allergic to shrimp, add a pad thai",
    )
    assert r.payload["allergen_conflict"] == ["shellfish"]


async def test_call_waiter_proposal(db):
    r, turn = await run(
        db, "call_waiter", {"reason": "allergy_or_dietary", "note": "severe peanut allergy"}
    )
    assert r.ok and r.payload["status"] == "proposed" and "confirm" in r.payload["instruction"]
    assert turn.actions == [
        {"type": "call_waiter", "action_id": r.payload["action_id"], "reason": "allergy_or_dietary",
         "note": "severe peanut allergy", "status": "proposed"}
    ]  # fmt: skip
    assert turn.allergen_sensitive and not turn.grounded


async def test_per_turn_caps(db):
    turn = T.TurnState()
    pad = await item_id(db, "Pad Thai")
    for _ in range(MAX_ADD_TO_CART_PER_TURN):
        r, _ = await run(db, "add_to_cart", {"item_id": pad, "qty": 1, "note": ""}, turn=turn)
        assert r.ok
    r, _ = await run(db, "add_to_cart", {"item_id": pad, "qty": 1, "note": ""}, turn=turn)
    assert not r.ok and r.payload["error"] == "limit_reached" and len(turn.actions) == 3
    r, _ = await run(db, "call_waiter", {"reason": "other", "note": ""}, turn=turn)
    assert r.ok
    r, _ = await run(db, "call_waiter", {"reason": "other", "note": ""}, turn=turn)
    assert not r.ok and r.payload["error"] == "limit_reached" and len(turn.actions) == 4
    assert len(turn.results) == 6


# --- invalid arguments ---


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("search_menu", {"exclude_allergens": ["nuts"]}),
        ("search_menu", {"max_price_cents": -5}),
        ("search_menu", {"query": "x", "bogus": 1}),
        ("search_menu", "{not json"),
        ("search_menu", "[1, 2]"),
        ("search_info", {"query": ""}),
        ("get_item", {"item_id": "abc"}),
        ("add_to_cart", {"item_id": 1, "qty": 0}),
        ("call_waiter", {"reason": "bored"}),
    ],
)
async def test_invalid_arguments_return_error_to_the_model(db, name, args):
    r, turn = await run(db, name, args)
    assert not r.ok and r.payload["error"] == "invalid_arguments" and r.payload["details"]
    assert not turn.grounded and turn.actions == []


async def test_unknown_tool_and_respond_are_not_executed(db):
    for name in ("drop_tables", "respond"):
        r, _ = await run(db, name, {})
        assert not r.ok and r.payload["error"] == "unknown_tool"


async def test_support_queries(db):
    async with db.connect() as c:
        assert await T.load_category_names(c, refresh=True) == sorted(CATEGORIES)
        facts = await T.load_item_facts(c)
        assert len(facts) == 42 and all(f.name for f in facts.values())
        cid = await T.find_disclaimer_chunk_id(c, "t_tools")
        txt = (
            await c.execute(text("SELECT content FROM chunks WHERE id=:i"), {"i": cid})
        ).scalar_one()
        assert "ross-contact" in txt
        assert await T.find_disclaimer_chunk_id(c, "nope") is None
    T.clear_category_cache()


async def test_cheapest_in_category_is_the_true_minimum(db):
    r, _ = await run(db, "search_menu", menu_args(sort="price_asc", categories=["Mains"]))
    cents = await names_with(
        db,
        "SELECT min(m.price_cents)::text FROM menu_items m JOIN menu_categories c "
        "ON c.id = m.category_id WHERE m.is_available AND c.name = 'Mains'",
    )
    top = r.payload["items"][0]
    assert top["category"] == "Mains" and cents == {str(int(float(top["price"][1:]) * 100))}
