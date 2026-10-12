"""The six chatbot tool schemas (plan section 3), their canonical serialisation, and the Pydantic
models that validate arguments.

Two properties matter:

1. Byte stability. The tool list sits in the cacheable prompt prefix right after the system prompt.
   DeepSeek's context cache matches whole-prefix bytes, so the tools are serialised ONCE through
   `canonical_json` (sorted keys, fixed separators, fixed tool order) and handed to the HTTP client
   as already-parsed dicts whose key order is the sorted order. Same DB categories -> same bytes in
   every process and session.
2. Strict compatibility. DeepSeek's strict mode (beta) needs every property in `required`,
   `additionalProperties: false`, and rejects minLength/maxLength/minItems/maxItems. So the JSON
   schema only uses enum/type/minimum/maximum, and the length limits live in the Pydantic models,
   which run on every call regardless of the strict flag.
"""

import json
from collections.abc import Sequence
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from app.vocab import ALLERGENS, DIETARY_TAGS

TOOL_ORDER = ("search_menu", "search_info", "get_item", "add_to_cart", "call_waiter", "respond")
SORTS = ("relevance", "price_asc", "price_desc")
INFO_TOPICS = ("faq", "policy", "allergen_practices", "dish_guide", "about", "menu_pdf")
WAITER_REASONS = ("allergy_or_dietary", "question_not_answered", "diner_asked_for_staff", "other")
ANSWER_STATUSES = ("answered", "not_found", "clarify", "off_topic", "needs_staff")

MAX_QUERY_CHARS = 200
MAX_NOTE_CHARS = 200
MAX_ANSWER_CHARS = 1200
MAX_PRICE_CENTS = 100_000
MAX_CART_QTY = 10
MAX_CITATIONS = 12
MAX_TERMS = 8
MAX_TERM_CHARS = 40
# per-turn caps (plan section 3, "Server validation")
MAX_ADD_TO_CART_PER_TURN = 3
MAX_CALL_WAITER_PER_TURN = 1


# --- Pydantic argument models (extra="forbid"; defaults keep non-strict parsing lenient) ---


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


Term = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TERM_CHARS)
]


class SearchMenuArgs(_Args):
    query: str = Field("", max_length=MAX_QUERY_CHARS)
    exclude_allergens: list[Literal[*ALLERGENS]] = Field(default_factory=list, max_length=12)  # type: ignore[valid-type]
    dietary: list[Literal[*DIETARY_TAGS]] = Field(default_factory=list, max_length=5)  # type: ignore[valid-type]
    categories: list[str] = Field(default_factory=list, max_length=10)
    other_allergy_terms: list[Term] = Field(default_factory=list, max_length=MAX_TERMS)
    max_price_cents: int = Field(0, ge=0, le=MAX_PRICE_CENTS)
    sort: Literal[*SORTS] = "relevance"  # type: ignore[valid-type]


class SearchInfoArgs(_Args):
    query: str = Field(..., min_length=1, max_length=MAX_QUERY_CHARS)
    topics: list[Literal[*INFO_TOPICS]] = Field(default_factory=list, max_length=6)  # type: ignore[valid-type]


class GetItemArgs(_Args):
    """Exactly one of item_id (>0) or name (non-empty). 0 / "" mean "not given" so the strict JSON
    schema can require both properties."""

    item_id: int = Field(0, ge=0)
    name: str = Field("", max_length=MAX_QUERY_CHARS)

    @model_validator(mode="after")
    def _exactly_one(self):
        if bool(self.item_id) == bool(self.name):
            raise ValueError("set exactly one of item_id (0 = none) or name ('' = none)")
        return self


class AddToCartArgs(_Args):
    item_id: int = Field(..., ge=1)
    qty: int = Field(1, ge=1, le=MAX_CART_QTY)
    note: str = Field("", max_length=MAX_NOTE_CHARS)


class CallWaiterArgs(_Args):
    reason: Literal[*WAITER_REASONS]  # type: ignore[valid-type]
    note: str = Field("", max_length=MAX_NOTE_CHARS)


class RespondArgs(_Args):
    answer: str = Field(..., min_length=1, max_length=MAX_ANSWER_CHARS)
    status: Literal[*ANSWER_STATUSES]  # type: ignore[valid-type]
    cited_item_ids: list[int] = Field(default_factory=list, max_length=MAX_CITATIONS)
    cited_passage_ids: list[int] = Field(default_factory=list, max_length=MAX_CITATIONS)


ARG_MODELS: dict[str, type[_Args]] = {
    "search_menu": SearchMenuArgs,
    "search_info": SearchInfoArgs,
    "get_item": GetItemArgs,
    "add_to_cart": AddToCartArgs,
    "call_waiter": CallWaiterArgs,
    "respond": RespondArgs,
}


# --- JSON schemas ---------------------------------------------------------------------------------


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),  # strict mode: every property is required
        "additionalProperties": False,
    }


def _enum_list(values: Sequence[str]) -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string", "enum": list(values)}}


def _descriptions() -> dict[str, str]:
    return {
        "search_menu": (
            "Search the dishes and drinks on the menu. Use for any question about what is "
            "available, ingredients, allergens, dietary options or prices of dishes. Allergy and "
            "diet needs MUST go in exclude_allergens / dietary / other_allergy_terms, never only "
            "in query. query is a short plain-words search ('' if only filters matter). "
            "max_price_cents 0 means no limit. Use sort price_asc for cheapest, price_desc for "
            "most expensive."
        ),
        "search_info": (
            "Search restaurant information passages: FAQ, policies, kitchen allergen practices, "
            "the dish guide (how dishes are prepared), sourcing, and items that appear only on "
            "the PDF menu. Do NOT use it to decide whether a dish contains an allergen. topics: "
            "[] searches all sources."
        ),
        "get_item": (
            "Get the full record of one dish: price, allergens, dietary tags, spice, availability, "
            "a short preparation note and allergen_conflict against the diner's declared "
            "allergies. Use name=<dish as the diner said it> (item_id 0) whenever the diner names "
            "a specific dish ('is the margherita safe for a coeliac?', 'add the Pad Thai'); it is "
            "never hidden by allergen filters. Use item_id (name '') for an id from an earlier "
            "result. Set exactly one. If several dishes match, you get candidates: pick one."
        ),
        "add_to_cart": (
            "Propose adding a dish to the diner's cart. Only when the diner clearly asks to add "
            "or order it. The diner must still confirm. note is for preparation requests "
            "('' if none) and is never an allergy safety instruction."
        ),
        "call_waiter": (
            "Propose calling a waiter to the table. The diner must confirm. Use when the diner "
            "asks for staff, for severe or unclear allergy questions, or when you cannot answer. "
            "note: a short reason for the staff member ('' if none)."
        ),
        "respond": (
            "Send the final answer to the diner. Call this exactly once, as the last step of "
            "every turn. answer is plain text (no markdown, no links). status: answered, "
            "not_found (nothing relevant found), clarify (you need more detail), off_topic, "
            "needs_staff (a waiter should handle it). cited_item_ids / cited_passage_ids list "
            "only ids returned by tools."
        ),
    }


def _parameters(categories: Sequence[str]) -> dict[str, dict[str, Any]]:
    cats: dict[str, Any] = (
        _enum_list(categories) if categories else {"type": "array", "items": {"type": "string"}}
    )
    return {
        "search_menu": _obj(
            {
                "query": {"type": "string"},
                "exclude_allergens": _enum_list(ALLERGENS),
                "dietary": _enum_list(DIETARY_TAGS),
                "categories": cats,
                "other_allergy_terms": {"type": "array", "items": {"type": "string"}},
                "max_price_cents": {"type": "integer", "minimum": 0, "maximum": MAX_PRICE_CENTS},
                "sort": {"type": "string", "enum": list(SORTS)},
            }
        ),
        "search_info": _obj(
            {"query": {"type": "string"}, "topics": _enum_list(INFO_TOPICS)},
        ),
        "get_item": _obj(
            {"item_id": {"type": "integer", "minimum": 0}, "name": {"type": "string"}}
        ),
        "add_to_cart": _obj(
            {
                "item_id": {"type": "integer", "minimum": 1},
                "qty": {"type": "integer", "minimum": 1, "maximum": MAX_CART_QTY},
                "note": {"type": "string"},
            }
        ),
        "call_waiter": _obj(
            {"reason": {"type": "string", "enum": list(WAITER_REASONS)}, "note": {"type": "string"}}
        ),
        "respond": _obj(
            {
                "answer": {"type": "string"},
                "status": {"type": "string", "enum": list(ANSWER_STATUSES)},
                "cited_item_ids": {"type": "array", "items": {"type": "integer"}},
                "cited_passage_ids": {"type": "array", "items": {"type": "integer"}},
            }
        ),
    }


def canonical_json(obj: Any) -> str:
    """Sorted keys, no whitespace, no ASCII escaping: identical bytes on every call and process."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _build_tools_json(categories: tuple[str, ...], strict: bool) -> str:
    desc, params = _descriptions(), _parameters(categories)
    tools = []
    for name in TOOL_ORDER:
        fn: dict[str, Any] = {"name": name, "description": desc[name], "parameters": params[name]}
        if strict:
            fn["strict"] = True
        tools.append({"type": "function", "function": fn})
    return canonical_json(tools)


@lru_cache(maxsize=8)
def tools_json(categories: tuple[str, ...], strict: bool = False) -> str:
    """Canonical JSON of the tool list. Cached per process, keyed by (sorted categories, strict)."""
    return _build_tools_json(tuple(sorted(categories)), strict)


def get_tools(categories: Sequence[str], strict: bool = False) -> list[dict[str, Any]]:
    """The tool list for the request body. Parsed from the canonical string so the dict key order
    (and therefore whatever the HTTP layer serialises) is the sorted order."""
    return json.loads(tools_json(tuple(sorted(categories)), strict))


# --- argument validation -------------------------------------------------------------------------


def validate_args(
    name: str, raw: str | dict[str, Any], categories: Sequence[str] | None = None
) -> tuple[_Args | None, dict[str, Any] | None]:
    """Parse + validate one tool call. Returns (args, None) or (None, error_payload).

    The error payload goes back to the model verbatim: it names fields and rules, never echoes
    values (they may contain diner PII)."""
    model = ARG_MODELS.get(name)
    if model is None:
        return None, {"error": "unknown_tool", "details": [f"no tool named {name!r}"]}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return None, {"error": "invalid_arguments", "details": ["arguments are not valid JSON"]}
    if not isinstance(data, dict):
        return None, {"error": "invalid_arguments", "details": ["arguments must be an object"]}
    try:
        args = model.model_validate(data)
    except ValidationError as exc:
        details = [
            f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}"
            for e in exc.errors(include_input=False, include_url=False, include_context=False)
        ][:5]
        return None, {"error": "invalid_arguments", "details": details}
    if isinstance(args, SearchMenuArgs) and categories is not None:
        bad = sorted(set(args.categories) - set(categories))
        if bad:
            return None, {
                "error": "invalid_arguments",
                "details": [f"categories: unknown {bad}; allowed {sorted(categories)}"],
            }
    return args, None
