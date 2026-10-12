"""Tool implementations for the chat loop (plan section 3).

Building blocks for the WP3 orchestrator: `run_tool(env, name, raw_args)` validates the model's
arguments, runs the tool against the DB, and returns a `ToolResult` whose `payload` goes back to the
model. Side information for logging, citations and guardrails is accumulated on `TurnState`.

Nothing here writes to the database. `add_to_cart` and `call_waiter` only build proposals (with a
generated action id); persisting them in `chat_actions` is the orchestrator's job after it commits.

Allergen safety is enforced here, not in the prompt: `search_menu` unions the model's exclusions
with allergens the diner declared (see allergen_terms.widen) and applies them as SQL filters. A
filtered-out dish can never reach the model, so it cannot be recommended.
"""

import difflib
import json
import re
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.chat.allergen_terms import extract_declaration, mentions_allergen_topic, widen
from app.chat.guardrails import ItemFact, fmt_price, name_variants
from app.chat.redact import redact
from app.chat.settings import ChatSettings
from app.chat.tool_schemas import (
    MAX_ADD_TO_CART_PER_TURN,
    MAX_CALL_WAITER_PER_TURN,
    AddToCartArgs,
    CallWaiterArgs,
    GetItemArgs,
    SearchInfoArgs,
    SearchMenuArgs,
    validate_args,
)
from app.retrieval.cache import QueryEmbedCache
from app.retrieval.chunking import TokenCounter
from app.retrieval.config import Filters, RetrievalConfig
from app.retrieval.embedder import Embedder, load_chunk_tokenizer
from app.retrieval.search import Hit, list_menu_items, retrieve

log = structlog.get_logger(__name__)

DESC_CHARS = 160
GUIDE_CHARS = 400
MAX_PRICE_FILTER_FETCH = 40  # over-fetch when a price cap is applied after relevance ranking
NO_MATCH_NOTE = "No available dish matches these filters."

_ITEM_SQL = (
    "SELECT m.id, m.name, m.description, m.price_cents, m.allergens, m.dietary_tags, "
    "m.spice_level, m.is_available, c.name AS category "
    "FROM menu_items m JOIN menu_categories c ON c.id = m.category_id"
)


# --- state objects --------------------------------------------------------------------------------


@dataclass
class SessionContext:
    table_label: str = ""
    declared_allergens: tuple[str, ...] = ()
    user_message: str = ""  # the current (redacted) diner message; used for synonym widening


@dataclass(frozen=True)
class PassageRef:
    id: int
    title: str
    section: str
    source_type: str
    slug: str = ""


@dataclass
class TurnState:
    """Mutable per-turn bookkeeping shared by all tool calls in one turn."""

    add_to_cart_count: int = 0
    call_waiter_count: int = 0
    grounded: bool = False  # a search/get/add tool succeeded this turn
    allergen_sensitive: bool = False
    items: dict[int, ItemFact] = field(default_factory=dict)  # returned by tools this turn
    passages: dict[int, PassageRef] = field(default_factory=dict)
    prices_cents: set[int] = field(default_factory=set)  # every price shown to the model
    actions: list[dict[str, Any]] = field(default_factory=list)  # proposals, in order
    results: list["ToolResult"] = field(default_factory=list)

    @property
    def item_ids(self) -> set[int]:
        return set(self.items)

    @property
    def passage_ids(self) -> set[int]:
        return set(self.passages)


@dataclass
class ToolEnv:
    conn: AsyncConnection
    settings: ChatSettings
    session: SessionContext
    turn: TurnState
    categories: Sequence[str] = ()
    # injection points (tests use fakes; production leaves them None)
    embedder: Embedder | None = None
    cache: QueryEmbedCache | None = None
    counter: TokenCounter | None = None
    retrieval_base: RetrievalConfig | None = None  # e.g. a different chunk_config in tests


@dataclass
class ToolResult:
    name: str
    payload: dict[str, Any]
    ok: bool = True
    signals: dict[str, Any] = field(default_factory=dict)  # logged per call
    widened: dict[str, list[str]] = field(default_factory=dict)
    result_item_ids: list[int] = field(default_factory=list)
    result_passage_ids: list[int] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)  # retrieval stage timings
    latency_ms: float = 0.0

    def content(self) -> str:
        """The `tool` message body sent back to the model."""
        return json.dumps(self.payload, separators=(",", ":"), ensure_ascii=False)


# --- small DB helpers -----------------------------------------------------------------------------

_category_cache: list[str] | None = None
_counter: TokenCounter | None = None


async def load_category_names(conn: AsyncConnection, *, refresh: bool = False) -> list[str]:
    """Sorted category names; cached per process (the list feeds the tool-schema enum, which must
    stay byte-stable). Restart the process or pass refresh=True after changing categories."""
    global _category_cache
    if _category_cache is None or refresh:
        rows = await conn.execute(text("SELECT name FROM menu_categories"))
        _category_cache = sorted(r[0] for r in rows)
    return list(_category_cache)


def clear_category_cache() -> None:
    global _category_cache
    _category_cache = None


def default_counter() -> TokenCounter:
    global _counter
    if _counter is None:
        _counter = TokenCounter(load_chunk_tokenizer())
    return _counter


def _fact(r) -> ItemFact:
    return ItemFact(
        id=r.id,
        name=r.name,
        category=r.category,
        price_cents=r.price_cents,
        allergens=tuple(r.allergens),
        dietary_tags=tuple(r.dietary_tags),
        spice_level=r.spice_level,
        available=r.is_available,
        description=r.description or "",
    )


async def load_item_facts(conn: AsyncConnection) -> dict[int, ItemFact]:
    """All menu items (42 rows): DB truth for NameMatcher and the contradiction check."""
    rows = (await conn.execute(text(_ITEM_SQL + " ORDER BY m.id"))).all()
    return {r.id: _fact(r) for r in rows}


async def _get_fact(conn: AsyncConnection, item_id: int) -> ItemFact | None:
    row = (await conn.execute(text(_ITEM_SQL + " WHERE m.id = :id"), {"id": item_id})).first()
    return _fact(row) if row else None


async def find_disclaimer_chunk_id(conn: AsyncConnection, chunk_config: str) -> int | None:
    """Chunk of the kitchen-practices page that states cross-contact is always possible; the
    allergen notice cites it as its source."""
    row = (
        await conn.execute(
            text(
                "SELECT ch.id FROM chunks ch JOIN documents d ON d.id = ch.document_id "
                "WHERE d.slug = 'allergen-kitchen-practices' AND ch.chunk_config_id = "
                "(SELECT id FROM chunk_configs WHERE name = :cc) "
                "ORDER BY (ch.content ILIKE '%cross-contact%') DESC, ch.chunk_index LIMIT 1"
            ),
            {"cc": chunk_config},
        )
    ).first()
    return row[0] if row else None


def is_allergen_sensitive(turn: TurnState, user_message: str, declared: Sequence[str] = ()) -> bool:
    """A turn is allergen-sensitive if the diner talked about allergies/diets, has declared
    allergens this session, or a tool call used allergen/diet filters."""
    return bool(turn.allergen_sensitive or declared or mentions_allergen_topic(user_message))


# --- evidence signals -----------------------------------------------------------------------------


def _rrf_score(h: Hit, rrf_k: int) -> float:
    return sum(1.0 / (rrf_k + r) for r in h.ranks.values())


def evidence(hits: list[Hit], cfg: RetrievalConfig, s: ChatSettings) -> tuple[str, dict]:
    """strong|weak|none|unknown plus every raw signal (logged even when the signal is switched off).

    The cross-encoder (rerank) score is the abstention signal: strong when the best of the top-3
    scores >= CHAT_ABSTAIN_THRESHOLD. Hits are sorted by rerank, so that is the top-1 score; the
    top-3 list is logged for analysis. With CHAT_ABSTAIN_SIGNAL=false there is no rerank and the
    strength is "unknown"."""
    cos_keys = ("menu_vec", "chunk_vec")
    cosines = [v for h in hits for k, v in h.scores.items() if k in cos_keys]
    signals: dict[str, Any] = {
        "n_hits": len(hits),
        "top1_rrf": round(_rrf_score(hits[0], cfg.rrf_k), 5) if hits else None,
        "best_cosine": round(max(cosines), 4) if cosines else None,
        "ce_top3": [round(h.scores["rerank"], 3) for h in hits[:3] if "rerank" in h.scores],
    }
    if not hits:
        strength = "none"
    elif not s.abstain_signal or not signals["ce_top3"]:
        strength = "unknown"
    else:
        strength = "strong" if max(signals["ce_top3"]) >= s.abstain_threshold else "weak"
    signals["evidence_strength"] = strength
    return strength, signals


# --- tool: search_menu ----------------------------------------------------------------------------


def _desc_of(h: Hit) -> str:
    name = h.meta.get("name", "")
    d = h.text[len(name) + 2 :] if h.text.startswith(name + ". ") else h.text
    return d if len(d) <= DESC_CHARS else d[: DESC_CHARS - 3].rstrip() + "..."


def _hit_fact(h: Hit) -> ItemFact:
    m = h.meta
    return ItemFact(
        id=h.id,
        name=m["name"],
        category=m["category"],
        price_cents=m["price_cents"],
        allergens=tuple(m["allergens"]),
        dietary_tags=tuple(m["dietary_tags"]),
        spice_level=m["spice_level"],
        available=m["is_available"],
    )


def _register_item(turn: TurnState, fact: ItemFact) -> None:
    turn.items[fact.id] = fact
    turn.prices_cents.add(fact.price_cents)


async def search_menu(env: ToolEnv, a: SearchMenuArgs) -> ToolResult:
    s, turn = env.settings, env.turn
    w = widen(a.model_dump(), env.session.user_message, env.session.declared_allergens)
    filters = Filters(
        available_only=True,
        exclude_allergens=w.exclude_allergens,
        dietary=tuple(a.dietary),
        categories=tuple(a.categories),
    )
    if w.exclude_allergens or a.dietary or w.cue:
        turn.allergen_sensitive = True

    query = a.query.strip()
    listing = a.sort != "relevance" or not query
    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    if listing:
        sort = a.sort if a.sort != "relevance" else "name"
        hits = await list_menu_items(
            env.conn, filters, sort, a.max_price_cents or None, limit=s.search_menu_k
        )
        strength, signals = "not_applicable", {"route": "list", "n_hits": len(hits)}
    else:
        cfg = (env.retrieval_base or RetrievalConfig()).with_(
            mode="hybrid",
            k=MAX_PRICE_FILTER_FETCH if a.max_price_cents else s.search_menu_k,
            rerank=s.abstain_signal,
            rerank_top_n=20,
        )
        res = await retrieve(
            env.conn, query, filters, cfg, {"menu"}, embedder=env.embedder, cache=env.cache
        )
        hits = res.hits
        if a.max_price_cents:  # price is not a safety filter, so Python post-filtering is fine
            hits = [h for h in hits if h.meta["price_cents"] <= a.max_price_cents]
        hits = hits[: s.search_menu_k]
        strength, signals = evidence(hits, cfg, s)
        signals["route"] = "retrieve"
        timings = res.timings_ms
    signals["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)

    items = []
    for h in hits:
        fact = _hit_fact(h)
        _register_item(turn, fact)
        items.append(
            {
                "id": h.id,
                "name": fact.name,
                "category": fact.category,
                "price": fmt_price(fact.price_cents),
                "allergens": sorted(fact.allergens),
                "dietary": sorted(fact.dietary_tags),
                "spice": fact.spice_level,
                "desc": _desc_of(h),
            }
        )
    turn.grounded = True

    notes = []
    if not items:
        notes.append(NO_MATCH_NOTE)
    if listing and query and a.sort != "relevance":
        notes.append("Sorted by price; the query text was not used. Narrow with categories.")
    if w.untracked_terms:
        notes.append(
            "Not tracked in our menu data: "
            + ", ".join(w.untracked_terms)
            + ". Tell the diner and offer call_waiter."
        )
    widened = {
        "from_message": list(w.widened_from_message),
        "from_session": list(w.widened_from_session),
        "from_terms": list(w.widened_from_terms),
    }
    payload: dict[str, Any] = {
        "items": items,
        "filters_applied": {
            "exclude_allergens": list(w.exclude_allergens),
            "dietary": list(a.dietary),
            "categories": list(a.categories),
            "max_price": fmt_price(a.max_price_cents) if a.max_price_cents else None,
            "sort": a.sort,
        },
        "widened_from_message": sorted(set(w.widened_from_message) | set(w.widened_from_session)),
        "untracked_terms": list(w.untracked_terms),
        "evidence_strength": strength,
        "note": " ".join(notes),
    }
    if w.widened:
        payload["widened_note"] = (
            "Extra exclusions were added from the diner's own words: "
            + ", ".join(w.widened_from_message + w.widened_from_session + w.widened_from_terms)
            + "."
        )
    return ToolResult(
        "search_menu",
        payload,
        signals=signals,
        widened=widened,
        result_item_ids=[i["id"] for i in items],
        timings_ms=timings,
    )


# --- tool: search_info ----------------------------------------------------------------------------


async def _doc_titles(conn: AsyncConnection, slugs: set[str]) -> dict[str, str]:
    if not slugs:
        return {}
    rows = await conn.execute(
        text("SELECT slug, title FROM documents WHERE slug = ANY(:s)"), {"s": sorted(slugs)}
    )
    return {r.slug: r.title for r in rows}


async def search_info(env: ToolEnv, a: SearchInfoArgs) -> ToolResult:
    s, turn = env.settings, env.turn
    cfg = (env.retrieval_base or RetrievalConfig()).with_(
        mode="hybrid", k=s.search_info_k, rerank=s.abstain_signal, rerank_top_n=20
    )
    filters = Filters(available_only=True, source_types=tuple(a.topics))
    t0 = time.perf_counter()
    res = await retrieve(
        env.conn, a.query, filters, cfg, {"docs"}, embedder=env.embedder, cache=env.cache
    )
    strength, signals = evidence(res.hits, cfg, s)
    signals["route"] = "retrieve"

    counter = env.counter or default_counter()
    budget, used, kept = s.info_context_tokens, 0, []
    for h in res.hits:
        n = counter.count(h.text)
        if kept and used + n > budget:
            break  # keep rank order: stop at the first passage that no longer fits
        kept.append(h)
        used += n
    signals["context_tokens"] = used
    signals["dropped_for_budget"] = len(res.hits) - len(kept)
    signals["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)

    titles = await _doc_titles(env.conn, {h.meta["slug"] for h in kept})
    passages = []
    for h in kept:
        title = titles.get(h.meta["slug"], h.meta["slug"])
        section = h.meta.get("section_path") or ""
        turn.passages[h.id] = PassageRef(
            h.id, title, section, h.meta["source_type"], h.meta["slug"]
        )
        passages.append({"id": h.id, "source": title, "section": section, "text": h.text})
    turn.grounded = True
    payload = {"passages": passages, "evidence_strength": strength}
    if not passages:
        payload["note"] = "No passage matched. Say you do not have that information."
    return ToolResult(
        "search_info",
        payload,
        signals=signals,
        result_passage_ids=[p["id"] for p in passages],
        timings_ms=res.timings_ms,
    )


# --- tool: get_item -------------------------------------------------------------------------------


async def _guide_passage(env: ToolEnv, fact: ItemFact) -> tuple[int, str, PassageRef] | None:
    cfg = env.retrieval_base or RetrievalConfig()
    row = (
        await env.conn.execute(
            text(
                "SELECT ch.id, ch.content, ch.section_path, d.slug, d.title, d.source_type "
                "FROM chunks ch JOIN documents d ON d.id = ch.document_id "
                "WHERE d.source_type = 'dish_guide' AND :iid = ANY(ch.menu_item_ids) AND "
                "ch.chunk_config_id = (SELECT id FROM chunk_configs WHERE name = :cc) "
                "ORDER BY (position(lower(:nm) in lower(ch.section_path)) > 0) DESC, "
                "ch.chunk_index LIMIT 1"
            ),
            {"iid": fact.id, "cc": cfg.chunk_config, "nm": fact.name},
        )
    ).first()
    if not row:
        return None
    ref = PassageRef(row.id, row.title, row.section_path or "", row.source_type, row.slug)
    return row.id, row.content, ref


_NAME_STOP = frozenset({"the", "a", "an", "of", "and", "with", "please", "dish", "one", "some"})
_FUZZY_RATIO = 0.85


def _toks(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in _NAME_STOP}


def match_item_name(query: str, facts: Sequence[ItemFact]) -> list[ItemFact]:
    """Resolve a dish name the diner (or model) wrote. Case-insensitive exact match on the full
    name or a variant ('Pinot Grigio' for 'Pinot Grigio (glass)') wins. Otherwise candidates are
    items whose name tokens contain every query token, or whose variant is >= 0.85 similar. One
    clear best candidate is returned alone; otherwise up to 3, best first (ambiguous)."""
    q = " ".join(query.lower().split())
    exact = [f for f in facts if q in name_variants(f.name)]
    if exact:
        return exact[:1]
    qt = _toks(q)
    scored: list[tuple[float, ItemFact]] = []
    for f in facts:
        ratio = max(difflib.SequenceMatcher(None, q, v).ratio() for v in name_variants(f.name))
        subset = bool(qt) and any(qt <= _toks(v) for v in name_variants(f.name))
        if ratio >= _FUZZY_RATIO or subset:
            scored.append((max(ratio, 0.5 if subset else 0), f))
    scored.sort(key=lambda x: (-x[0], x[1].id))
    if len(scored) > 1 and not (scored[0][0] >= 0.9 and scored[0][0] - scored[1][0] >= 0.1):
        return [f for _, f in scored[:3]]
    return [f for _, f in scored[:1]]


async def get_item(env: ToolEnv, a: GetItemArgs) -> ToolResult:
    if a.item_id:
        fact = await _get_fact(env.conn, a.item_id)
    else:
        matches = match_item_name(a.name, list((await load_item_facts(env.conn)).values()))
        if len(matches) > 1:
            return ToolResult(
                "get_item",
                {
                    "ambiguous": True,
                    "candidates": [
                        {"id": f.id, "name": f.name, "category": f.category} for f in matches
                    ],
                    "instruction": "Several dishes match. Call get_item again with the id of "
                    "the one the diner means (name ''), or ask which one they mean.",
                },
            )
        fact = matches[0] if matches else None
    if fact is None:
        return ToolResult("get_item", {"error": "unknown_item"}, ok=False)
    # Named-dish lookups are never filtered by allergens: the diner asked about THIS dish, so we
    # report its allergens and the conflict with what they declared (this session + this message).
    w = widen({"exclude_allergens": []}, env.session.user_message, env.session.declared_allergens)
    conflict = sorted(set(w.exclude_allergens) & set(fact.allergens))
    if w.exclude_allergens or w.cue:
        env.turn.allergen_sensitive = True
    _register_item(env.turn, fact)
    env.turn.grounded = True
    payload: dict[str, Any] = {
        "id": fact.id,
        "name": fact.name,
        "category": fact.category,
        "price": fmt_price(fact.price_cents),
        "allergens": sorted(fact.allergens),
        "dietary": sorted(fact.dietary_tags),
        "spice": fact.spice_level,
        "available": fact.available,
        "allergen_conflict": conflict,
        "description": fact.description,
        "guide": None,
        "guide_chunk_id": None,
    }
    ids_p: list[int] = []
    if (g := await _guide_passage(env, fact)) is not None:
        cid, content, ref = g
        payload["guide"] = (
            content if len(content) <= GUIDE_CHARS else content[: GUIDE_CHARS - 3] + "..."
        )
        payload["guide_chunk_id"] = cid
        env.turn.passages[cid] = ref
        ids_p.append(cid)
    return ToolResult("get_item", payload, result_item_ids=[fact.id], result_passage_ids=ids_p)


# --- tools: add_to_cart, call_waiter (proposals only) ---------------------------------------


def _limit_error(tool: str, cap: int) -> ToolResult:
    return ToolResult(
        tool,
        {"error": "limit_reached", "details": [f"at most {cap} {tool} per turn"]},
        ok=False,
    )


async def add_to_cart(env: ToolEnv, a: AddToCartArgs) -> ToolResult:
    turn = env.turn
    if turn.add_to_cart_count >= MAX_ADD_TO_CART_PER_TURN:
        return _limit_error("add_to_cart", MAX_ADD_TO_CART_PER_TURN)
    fact = await _get_fact(env.conn, a.item_id)
    if fact is None:
        return ToolResult("add_to_cart", {"error": "unknown_item"}, ok=False)
    if not fact.available:
        return ToolResult("add_to_cart", {"error": "unavailable"}, ok=False)
    declared = set(env.session.declared_allergens) | set(
        extract_declaration(env.session.user_message).allergens
    )
    conflict = sorted(declared & set(fact.allergens))
    action_id = str(uuid.uuid4())
    note = redact(a.note) or None
    _register_item(turn, fact)
    line_total = fact.price_cents * a.qty
    turn.prices_cents.add(line_total)
    turn.add_to_cart_count += 1
    turn.grounded = True
    if conflict:
        turn.allergen_sensitive = True
    turn.actions.append(
        {
            "type": "add_to_cart",
            "action_id": action_id,
            "menu_item_id": fact.id,
            "name": fact.name,
            "price_cents": fact.price_cents,
            "qty": a.qty,
            "note": note,
            "allergens": sorted(fact.allergens),
            "allergen_conflict": conflict,
            "status": "proposed",
        }
    )
    payload = {
        "status": "proposed",
        "action_id": action_id,
        "item": {"id": fact.id, "name": fact.name, "price": fmt_price(fact.price_cents)},
        "qty": a.qty,
        "line_total": fmt_price(line_total),
        "allergen_conflict": conflict,
        "instruction": "This is only a suggestion; the diner must tap to add it. "
        + (
            "Warn them: it contains " + ", ".join(conflict) + ", which they declared. "
            if conflict
            else ""
        )
        + "Do not say it was added.",
    }
    return ToolResult("add_to_cart", payload, result_item_ids=[fact.id])


async def call_waiter(env: ToolEnv, a: CallWaiterArgs) -> ToolResult:
    turn = env.turn
    if turn.call_waiter_count >= MAX_CALL_WAITER_PER_TURN:
        return _limit_error("call_waiter", MAX_CALL_WAITER_PER_TURN)
    action_id = str(uuid.uuid4())
    note = redact(a.note) or None
    turn.call_waiter_count += 1
    if a.reason == "allergy_or_dietary":
        turn.allergen_sensitive = True
    turn.actions.append(
        {
            "type": "call_waiter",
            "action_id": action_id,
            "reason": a.reason,
            "note": note,
            "status": "proposed",
        }
    )
    payload = {
        "status": "proposed",
        "action_id": action_id,
        "instruction": "The diner must confirm. Do not say a waiter is on the way.",
    }
    return ToolResult("call_waiter", payload)


# --- dispatcher -----------------------------------------------------------------------------------

_TOOLS = {
    "search_menu": search_menu,
    "search_info": search_info,
    "get_item": get_item,
    "add_to_cart": add_to_cart,
    "call_waiter": call_waiter,
}


async def run_tool(env: ToolEnv, name: str, raw_args: str | dict[str, Any]) -> ToolResult:
    """Validate and run one model tool call. `respond` is not run here: the orchestrator validates
    it with `tool_schemas.validate_args("respond", ...)`. Never raises for bad model input."""
    t0 = time.perf_counter()
    if name not in _TOOLS:
        res = ToolResult(
            name, {"error": "unknown_tool", "details": [f"no tool named {name!r}"]}, ok=False
        )
    else:
        args, err = validate_args(name, raw_args, env.categories or None)
        res = (
            ToolResult(name, err, ok=False)  # type: ignore[arg-type]
            if err
            else await _TOOLS[name](env, args)  # type: ignore[arg-type]
        )
    res.latency_ms = round((time.perf_counter() - t0) * 1000, 2)
    env.turn.results.append(res)
    return res
