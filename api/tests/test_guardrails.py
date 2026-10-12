"""Pure tests: guardrail checks, redaction, allergen synonym table, prompt/tool-schema determinism.
No DB, no network."""

import hashlib
import subprocess
import sys

import pytest

from app.chat import guardrails as g
from app.chat.allergen_terms import (
    DELIBERATELY_UNMAPPED,
    TERMS,
    extract_declaration,
    lookup,
    mentions_allergen_topic,
    widen,
)
from app.chat.prompts import CANARY, SYSTEM_PROMPT, build_session_context, prompt_version
from app.chat.redact import luhn_ok, redact, redact_with_counts
from app.chat.tool_schemas import get_tools, tools_json, validate_args
from app.vocab import ALLERGENS, DIETARY_TAGS
from tests.conftest import API_DIR

CATS = ["Bowls & Curries", "Desserts", "Drinks", "Mains", "Sides", "Starters"]


def _facts() -> dict[int, g.ItemFact]:
    rows = [
        (1, "Pad Thai", 2000, ("egg", "fish", "peanut", "shellfish"), ()),
        (2, "Edamame", 700, ("soy",), ("vegan", "gluten_free")),
        (3, "Fish and Chips", 2200, ("gluten", "fish", "egg"), ()),
        (4, "Pinot Grigio (glass)", 1100, ("sulphites",), ("vegan",)),
        (5, "Burrata & Heirloom Tomato", 1400, ("dairy", "tree_nut"), ("vegetarian",)),
        (6, "Oat Milk Latte", 550, ("gluten",), ("dairy_free",)),
        (7, "Margherita Pizza", 1700, ("gluten", "dairy"), ("vegetarian",)),
    ]
    return {i: g.ItemFact(i, n, "Mains", p, tuple(a), tuple(d)) for i, n, p, a, d in rows}


FACTS = _facts()
MATCHER = g.NameMatcher(FACTS.values())


def ctx(**kw) -> g.AnswerContext:
    base = dict(
        status="answered",
        cited_item_ids=(1,),
        known_item_ids=frozenset(FACTS),
        grounded_this_turn=True,
        allowed_price_cents=frozenset({2000, 700, 1100, 1400, 2200}),
        facts=FACTS,
        allergen_sensitive=True,
        canary=CANARY,
        system_prompt=SYSTEM_PROMPT,
    )
    base.update(kw)
    return g.AnswerContext(**base)


def checks(answer: str, **kw) -> list[str]:
    return g.run_checks(answer, ctx(**kw), MATCHER).checks


# --- 1, 2: citations and grounding ---


def test_unknown_citation_fires_and_known_passes():
    assert "unknown_citation" in checks("Pad Thai is $20.00.", cited_item_ids=(999,))
    assert "unknown_citation" in checks(
        "See policy.", cited_passage_ids=(5,), known_passage_ids=frozenset({6})
    )
    assert checks("Pad Thai lists egg, fish, peanut and shellfish. Ask your server.") == []


def test_answered_without_valid_citation_is_flagged_after_a_search():
    assert checks("It is $20.00.", cited_item_ids=()) == ["unknown_citation"]
    # other statuses do not need citations
    assert checks("Sorry, I don't have that.", status="not_found", cited_item_ids=()) == []


def test_ungrounded_answer_needs_tool_or_known_history_citation():
    out = checks("Hello there.", grounded_this_turn=False, cited_item_ids=())
    assert out == ["ungrounded_answer"]
    # follow-up answered from an id returned in an earlier turn is fine
    assert checks("Edamame is $7.00.", grounded_this_turn=False, cited_item_ids=(2,)) == []
    assert checks("Hi!", status="clarify", grounded_this_turn=False, cited_item_ids=()) == []


# --- 3: prices ---


def test_price_mismatch():
    assert "price_mismatch" in checks("Pad Thai costs $18.00.")
    assert checks("Pad Thai costs $20.00.") == []
    assert "price_mismatch" in checks("It is $20.50 each.")
    assert g.extract_price_cents("$14 and $14.50 and 3.25 dollars") == [1400, 1450, 325]


def test_prices_stated_by_the_diner_are_allowed_via_context():
    assert (
        checks("Under $15.00 there is Edamame.", allowed_price_cents=frozenset({1500, 700})) == []
    )


# --- 4: unsafe allergen language ---


@pytest.mark.parametrize(
    "answer",
    [
        "The Edamame is safe for you.",
        "This dish is completely safe.",
        "The Pad Thai is allergen-free.",
        "Edamame is peanut-free.",
        "It is a nut free dish.",
        "I guarantee there are no nuts.",
        "There is no risk with this one.",
        "It is 100% free of gluten.",
        "The Pad Thai is free from nuts.",
        "There is no cross-contact in our kitchen.",
        "Perfectly safe to eat.",
    ],
)
def test_unsafe_language_fires_on_sensitive_turns(answer):
    assert "unsafe_allergen_language" in checks(answer, cited_item_ids=(2,))


@pytest.mark.parametrize(
    "answer",
    [
        "Pad Thai lists peanut, egg, fish and shellfish.",
        "The listed allergens for Edamame do not include peanut.",
        "I can't say it is safe, so please ask your server.",
        "We cannot guarantee anything because of cross-contact.",
        "Edamame has the gluten_free tag.",
        "Edamame carries the gluten-free tag in our menu data.",
        "It is tagged gluten-free, but the kitchen cannot rule out cross-contact.",
        "The kitchen cannot rule out cross-contact.",
        "Pad Thai contains peanut.",
    ],
)
def test_unsafe_language_does_not_false_fire(answer):
    assert "unsafe_allergen_language" not in checks(answer, cited_item_ids=(2,))


def test_unsafe_language_only_on_allergen_sensitive_turns():
    assert (
        checks("The Edamame is safe for you.", cited_item_ids=(2,), allergen_sensitive=False) == []
    )


# --- 5: allergen contradiction ---


@pytest.mark.parametrize(
    "answer",
    [
        "The Pad Thai has no peanuts.",
        "Pad Thai does not contain peanut.",
        "Pad Thai is peanut-free.",
        "The Pad Thai contains no nuts at all.",  # nuts -> peanut + tree_nut
        "Pad Thai is gluten free but contains egg. The Pad Thai has no fish or shellfish.",
        "The Burrata & Heirloom Tomato is dairy free.",
        "The Burrata and Heirloom Tomato has no dairy.",
        "Margherita Pizza is made without gluten.",
        "The pinot grigio (glass) has no sulphites.",
        "Fish and Chips: no fish in it.",  # item name must not hide the real claim
        "The Pad Thai is popular. It has no peanuts.",  # subject carried over
    ],
)
def test_contradiction_fires(answer):
    out = checks(answer, allergen_sensitive=False, cited_item_ids=(1,))
    # Pad Thai + gluten-free line is not a contradiction by itself; all listed examples have one
    if answer.startswith("Pad Thai is gluten free"):
        assert "allergen_contradiction" in out  # 'no fish or shellfish'
    else:
        assert "allergen_contradiction" in out, answer


@pytest.mark.parametrize(
    "answer",
    [
        "The Pad Thai contains peanut.",
        "Edamame has no peanuts.",
        "Edamame is peanut-free but contains soy.",
        "Pad Thai contains peanut, but the Edamame has no peanuts.",
        "The Pad Thai has egg, and no sesame.",
        "Fish and Chips lists gluten, fish and egg.",
        "The Oat Milk Latte is tagged dairy_free.",
        "Burrata & Heirloom Tomato has no peanuts.",  # tree_nut only
        "There are no peanut dishes under $7.00.",  # no item named
        "The Pinot Grigio (glass) lists sulphites.",
    ],
)
def test_contradiction_does_not_false_fire(answer):
    out = checks(
        answer, allergen_sensitive=False, cited_item_ids=(1,), allowed_price_cents=frozenset({700})
    )
    assert "allergen_contradiction" not in out, answer


# --- 6: prompt leak ---


def test_prompt_leak_canary_and_ngram():
    assert "prompt_leak" in checks(f"My marker is {CANARY.lower()}")
    words = SYSTEM_PROMPT.split()
    quote = " ".join(words[-90:-70])
    assert "prompt_leak" in checks(f"Sure! {quote}")
    assert "prompt_leak" not in checks(
        "Edamame lists soy. The kitchen cannot rule out cross-contact.", cited_item_ids=(2,)
    )


def test_prompt_leak_ignores_ordinary_welcome_but_catches_regurgitation():
    welcome = (
        "Hi, welcome to Lantern Kitchen! I can help with the menu, prices, ingredients, allergens, "
        "dietary options, hours and policies. What would you like to know?"
    )
    assert "prompt_leak" not in checks(welcome, status="clarify")
    scope = SYSTEM_PROMPT.split("\n\n")[1]  # the SCOPE block may be restated to diners
    assert scope.startswith("SCOPE")
    assert "prompt_leak" not in checks(scope.replace("\n", " "), status="clarify")
    # a long verbatim run from a non-public block still trips it
    sec = next(b for b in SYSTEM_PROMPT.split("\n\n") if b.startswith("ALLERGEN"))
    assert "prompt_leak" in checks(sec.replace("\n", " "), status="clarify")
    # one 12-word shingle alone (an 12-13 word quote) is not enough
    words = sec.split()
    assert "prompt_leak" not in checks(" ".join(words[3:15]), status="clarify")


# --- 7: URLs and markup ---


def test_markup_is_stripped_and_not_repairable():
    rep = g.run_checks(
        "**Edamame** costs $7.00, see [menu](https://x.example/menu) or www.foo.com <b>now</b>.",
        ctx(cited_item_ids=(2,)),
        MATCHER,
    )
    assert rep.answer == "Edamame costs $7.00, see menu or now."
    assert rep.checks == ["url_or_markup"]
    assert not rep.needs_repair
    plain = g.run_checks("Edamame costs $7.00.", ctx(cited_item_ids=(2,)), MATCHER)
    assert plain.answer == "Edamame costs $7.00." and plain.violations == ()


# --- name detection ---


def test_name_matcher_variants_and_boundaries():
    def names(t):
        return [FACTS[m.item_id].name for m in MATCHER.find(t)]

    assert names("try the PAD THAI!") == ["Pad Thai"]
    assert names("Pinot Grigio is nice; the pinot grigio (glass) is $11.00") == [
        "Pinot Grigio (glass)",
        "Pinot Grigio (glass)",
    ]
    assert names("burrata and heirloom tomato") == ["Burrata & Heirloom Tomato"]
    assert names("Burrata & Heirloom  Tomato") == ["Burrata & Heirloom Tomato"]
    assert names("padthai pad thais") == []  # word boundaries
    assert names("Fish and Chips with Pad Thai") == ["Fish and Chips", "Pad Thai"]


def test_all_seed_menu_names_are_detected():
    import json

    menu = json.loads((API_DIR / "seed" / "menu.json").read_text())
    items = [i["name"] for c in menu["categories"] for i in c["items"]]
    assert len(items) == 42
    facts = [g.ItemFact(n_, n) for n_, n in enumerate(items, 1)]
    m = g.NameMatcher(facts)
    for f in facts:
        assert [x.item_id for x in m.find(f"I'd like the {f.name.lower()} please.")] == [f.id], (
            f.name
        )


# --- notice / fallback ---


def test_allergen_notice_shape():
    n = g.build_allergen_notice([FACTS[1], FACTS[1], FACTS[2]], source_chunk_id=311)
    assert n["source_chunk_id"] == 311 and n["disclaimer"] == g.DISCLAIMER
    assert n["items"][0] == {
        "menu_item_id": 1, "name": "Pad Thai",
        "allergens": ["egg", "fish", "peanut", "shellfish"], "dietary_tags": [],
    }  # fmt: skip
    assert len(n["items"]) == 2
    empty = g.build_allergen_notice([])
    assert empty["items"] == [] and empty["source_chunk_id"] is None


def test_items_for_notice_cited_then_named():
    out = g.items_for_notice("Try the Edamame.", [1], FACTS, MATCHER)
    assert [i.name for i in out] == ["Pad Thai", "Edamame"]


def test_fallback_message_is_db_only_and_passes_its_own_checks():
    msg = g.build_fallback_message([FACTS[1], FACTS[2]])
    assert "Pad Thai ($20.00): egg, fish, peanut and shellfish." in msg
    rep = g.run_checks(msg, ctx(status="needs_staff", cited_item_ids=()), MATCHER)
    assert rep.violations == (), rep.violations
    assert "Call a waiter" in g.build_fallback_message([])


# --- redaction ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("mail me at jo.doe+x@Example.co.uk please", "mail me at [email] please"),
        ("call +44 20 7946 0958 now", "call [phone] now"),
        ("call (780) 555-1234 now", "call [phone] now"),
        ("780-555-1234", "[phone]"),
        ("+1 780 555 1234", "[phone]"),
        ("my number is 07911 123456", "my number is [phone]"),
        ("card 4111 1111 1111 1111 ok", "card [card] ok"),
        ("4111-1111-1111-1111", "[card]"),
        ("5500005555555559", "[card]"),
    ],
)
def test_redaction_positive(raw, expected):
    assert redact(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "1234 5678 9012 3456",  # 16 digits, fails Luhn
        "1234567890123456",
        "Pad Thai is $14.50 for 2 people at table 12",
        "order on 2026-10-11 at 18:30",
        "I'd like 3 of item 12 and 10 of item 7",
        "T3",
    ],
)
def test_redaction_leaves_non_pii_alone(raw):
    assert redact(raw) == raw


def test_luhn_and_counts():
    assert luhn_ok("4111111111111111") and not luhn_ok("1234567890123456")
    r = redact_with_counts("a@b.co, +1 780 555 1234, 4111 1111 1111 1111, 1234 5678 9012 3456")
    assert (r.emails, r.phones, r.cards) == (1, 1, 1)
    assert "1234 5678 9012 3456" in r.text


# --- allergen terms ---


def test_table_only_maps_to_known_allergens_and_is_lowercase():
    for term, al in TERMS.items():
        assert term == term.lower() and al and set(al) <= set(ALLERGENS), term
    assert not set(DELIBERATELY_UNMAPPED) & set(TERMS)


@pytest.mark.parametrize(
    ("msg", "allergens", "untracked"),
    [
        ("I'm allergic to nuts", ("peanut", "tree_nut"), ()),
        ("I have a nut allergy", ("peanut", "tree_nut"), ()),
        ("allergic to seafood", ("fish", "shellfish"), ()),
        ("I'm coeliac", ("gluten",), ()),
        ("my daughter is celiac", ("gluten",), ()),
        ("I can't eat wheat", ("gluten",), ()),
        ("lactose intolerant", ("dairy",), ()),
        ("I cannot have prawns or crab", ("shellfish",), ()),
        ("allergic to eggs, tahini and soya", ("egg", "soy", "sesame"), ()),
        ("allergic to tofu", ("soy",), ()),
        ("allergic to soy sauce", ("soy", "gluten"), ()),
        ("I'm allergic to kiwi and peanuts, what can I eat?", ("peanut",), ("kiwi",)),
        ("allergic to coconut", (), ("coconut",)),
        ("no nuts please", ("peanut", "tree_nut"), ()),
        ("severe allergy to cashews. What desserts do you have?", ("tree_nut",), ()),
    ],
)
def test_declaration_extraction(msg, allergens, untracked):
    d = extract_declaration(msg)
    assert d.cue
    assert tuple(sorted(d.allergens)) == tuple(sorted(allergens))
    assert d.untracked == untracked


@pytest.mark.parametrize(
    "msg",
    [
        "show me tofu dishes",
        "do you have butter chicken",
        "I'm not allergic to peanuts",
        "what is your nut allergy policy",
        "no onions please",
        "is the cheese vegetarian",
        "how much is the pad thai",
    ],
)
def test_no_false_declaration(msg):
    d = extract_declaration(msg)
    assert not d.allergens and not d.untracked, d


def test_generic_allergy_cue_without_term():
    d = extract_declaration("I'm allergic, what can I eat?")
    assert d.cue and not d.allergens


def test_widen_only_adds():
    w = widen({"exclude_allergens": ["tree_nut"]}, "I'm allergic to nuts")
    assert w.exclude_allergens == ("peanut", "tree_nut")
    assert w.widened_from_message == ("peanut",) and w.widened
    # the model's own exclusions are never removed, even when the message says nothing
    w = widen({"exclude_allergens": ["fish", "soy"]}, "what's good for lunch?")
    assert w.exclude_allergens == ("soy", "fish") and not w.widened
    w = widen({"exclude_allergens": []}, "what's good for lunch?", declared=["egg"])
    assert w.exclude_allergens == ("egg",) and w.widened_from_session == ("egg",)
    w = widen({"other_allergy_terms": ["seafood", "kiwi", "everything"]}, "hi")
    assert w.exclude_allergens == ("fish", "shellfish")
    assert w.untracked_terms == ("kiwi",) and w.widened_from_terms == ("fish", "shellfish")


def test_lookup_and_topic_detection():
    assert lookup("Sesame seeds!") == ("sesame",)
    assert lookup("kiwi") is None
    assert mentions_allergen_topic("is it gluten free?")
    assert mentions_allergen_topic("I'm allergic to kiwi")
    assert not mentions_allergen_topic("what time do you close")


# --- prompt and tool-schema determinism ---


def test_tools_are_strict_compatible_and_ordered():
    tools = get_tools(CATS, strict=True)
    assert [t["function"]["name"] for t in tools] == [
        "search_menu", "search_info", "get_item", "add_to_cart", "call_waiter", "respond",
    ]  # fmt: skip
    banned = {"minLength", "maxLength", "minItems", "maxItems"}

    def walk(node):
        if isinstance(node, dict):
            assert not banned & set(node), node
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for t in tools:
        assert t["function"]["strict"] is True
        walk(t["function"]["parameters"])
    sm = tools[0]["function"]["parameters"]["properties"]
    assert sm["categories"]["items"]["enum"] == sorted(CATS)
    assert sm["exclude_allergens"]["items"]["enum"] == list(ALLERGENS)
    assert sm["dietary"]["items"]["enum"] == list(DIETARY_TAGS)
    assert "strict" not in get_tools(CATS)[0]["function"]


def _digest(cats=CATS) -> str:
    return hashlib.sha256(
        (SYSTEM_PROMPT + tools_json(tuple(sorted(cats)), False)).encode()
    ).hexdigest()


def test_prefix_bytes_identical_across_calls_orderings_and_processes():
    assert _digest() == _digest(list(reversed(CATS)))
    assert tools_json(tuple(sorted(CATS))) == tools_json(tuple(sorted(CATS)))
    out = subprocess.run(
        [
            sys.executable, "-c",
            "import hashlib; from app.chat.prompts import SYSTEM_PROMPT; "
            "from app.chat.tool_schemas import tools_json; "
            f"c={CATS!r}; "
            "tj = tools_json(tuple(sorted(c)), False); "
            "print(hashlib.sha256((SYSTEM_PROMPT + tj).encode()).hexdigest())",
        ],
        cwd=API_DIR, capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert out.stdout.strip() == _digest()


def test_prompt_version_changes_with_categories_and_has_no_per_request_content():
    assert prompt_version(CATS) == prompt_version(list(reversed(CATS)))
    assert prompt_version(CATS) != prompt_version(CATS + ["Specials"])
    assert prompt_version(CATS) != prompt_version(CATS, strict=True)
    assert CANARY in SYSTEM_PROMPT and "{" not in SYSTEM_PROMPT
    assert build_session_context("T3", ["peanut", "egg"])["content"].count("egg, peanut") == 1
    assert "not declared" in build_session_context("T3")["content"]


def test_validate_args_enforces_limits_the_schema_cannot():
    args, err = validate_args("search_info", {"query": "x" * 201, "topics": []})
    assert args is None and err["error"] == "invalid_arguments"
    assert "x" * 20 not in str(err)  # never echoes values
    _, err = validate_args("add_to_cart", {"item_id": 1, "qty": 11, "note": ""})
    assert err["error"] == "invalid_arguments"
    _, err = validate_args("respond", {"answer": "ok", "status": "maybe"})
    assert err["error"] == "invalid_arguments"
    _, err = validate_args("search_menu", "not json")
    assert err["details"] == ["arguments are not valid JSON"]
    _, err = validate_args("nope", {})
    assert err["error"] == "unknown_tool"
    ok, err = validate_args("respond", '{"answer":"Hi","status":"clarify"}')
    assert err is None and ok.cited_item_ids == []
