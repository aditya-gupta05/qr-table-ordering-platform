"""One chat turn: LLM tool loop, guardrails, repair, fallbacks, outcome (plan sections 1, 4, 6).

Shape of a turn:

    prepare (short DB session)      categories, DB facts, history, ids known from earlier turns
    loop <= max_llm_calls           LLM call (NO db connection held) -> short DB session that logs
                                    the call and runs its tool calls -> next call
    respond(...) accepted           guardrails -> (one repair call) -> fallback `blocked`
    compose                         citations, DB-built allergen notice, actions, outcome

Rules that shape the code:
* Never hold a DB connection or transaction across an LLM call. Each stage opens its own session.
* Every tool call from the model gets a tool message back, including invalid ones; those count
  toward the call cap like any other call.
* The last allowed call forces `respond` (named tool_choice), so a turn always ends with an answer
  or a deterministic fallback, never silently.
* Nothing here stores or logs prompt or answer text: rows hold ids, counts, tokens and redacted,
  size-capped tool arguments; logs hold lengths, hashes and ids.

The orchestrator does not write the assistant chat message. It returns a `TurnResult`; the service
persists it (together with the actions and the busy_until release) in one transaction.
"""

import asyncio
import functools
import hashlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat import answer_cache, history, metrics
from app.chat.guardrails import (
    DISCLAIMER,
    AnswerContext,
    ItemFact,
    NameMatcher,
    build_allergen_notice,
    build_fallback_message,
    extract_price_cents,
    items_for_notice,
    run_checks,
)
from app.chat.llm import LLMClient, LLMError, LLMResult
from app.chat.outcomes import assign_turn_outcome
from app.chat.prompts import (
    CANARY,
    SYSTEM_PROMPT,
    build_session_context,
    prefix_sha256,
    prompt_version,
    system_message,
)
from app.chat.redact import redact
from app.chat.settings import ChatSettings
from app.chat.tool_schemas import RespondArgs, get_tools, validate_args
from app.chat.tools import (
    SessionContext,
    ToolEnv,
    ToolResult,
    TurnState,
    find_disclaimer_chunk_id,
    is_allergen_sensitive,
    load_category_names,
    load_item_facts,
    run_tool,
)
from app.models import LLMCall, ToolCall
from app.retrieval.cache import QueryEmbedCache, embed_query_cached
from app.retrieval.chunking import TokenCounter
from app.retrieval.config import RetrievalConfig
from app.retrieval.embedder import Embedder, get_embedder

log = structlog.get_logger("chat")

Emit = Callable[[str, dict], Awaitable[None]]

INFO_TOOLS = frozenset({"search_menu", "search_info", "get_item"})
STAGE_BY_TOOL = {
    "search_menu": ("searching_menu", "Searching the menu..."),
    "search_info": ("searching_info", "Looking through our information..."),
    "get_item": ("checking_item", "Checking the dish details..."),
    "add_to_cart": ("checking_item", "Checking the dish details..."),
    "call_waiter": ("thinking", "Thinking..."),
}
ARG_LOG_CHARS = 200
ERROR_TEXT = (
    "Sorry, I can't answer right now. Please ask a member of staff, "
    "or tap Call a waiter and I will let them know."
)
RESPOND_FORCED = {"type": "function", "function": {"name": "respond"}}


@dataclass
class ChatDeps:
    """Everything a turn needs from the outside. Tests inject a FakeLLM and fake retrieval parts."""

    llm: LLMClient
    settings: ChatSettings
    sessionmaker: async_sessionmaker[AsyncSession]
    embedder: Embedder | None = None
    cache: QueryEmbedCache | None = None
    counter: TokenCounter | None = None
    retrieval_base: RetrievalConfig | None = None

    @property
    def chunk_config(self) -> str:
        return (self.retrieval_base or RetrievalConfig()).chunk_config

    def get_embedder(self) -> Embedder:
        return self.embedder or get_embedder((self.retrieval_base or RetrievalConfig()).embed_model)


@dataclass(frozen=True)
class TurnInput:
    session_id: uuid.UUID
    turn_id: uuid.UUID
    turn_index: int
    table_label: str
    declared: tuple[str, ...]
    text: str  # PII-redacted diner message
    request_id: str | None = None


@dataclass
class TurnResult:
    text: str
    status: str  # answered|not_found|clarify|off_topic|needs_staff|blocked|error
    outcome: str
    citations: list[dict]
    allergen_notice: dict | None
    actions: list[dict]
    guardrails: list[dict]
    latency_ms: int
    prompt_version: str = ""
    error_code: str | None = None
    llm_calls: int = 0
    served_from_cache: bool = False
    cache_candidate: answer_cache.Candidate | None = None  # shadow mode only
    debug: dict[str, Any] = field(default_factory=dict)

    @property
    def is_error(self) -> bool:
        return self.outcome == "error"


class _TurnError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass
class _Acc:
    """Per-turn accumulators (logging, metrics, debug block, answer-cache decision)."""

    llm: list[dict] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)
    guardrails: list[dict] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    repaired: bool = False

    @property
    def n_llm(self) -> int:
        return len(self.llm)


@dataclass
class _Prep:
    categories: list[str]
    facts: dict[int, ItemFact]
    matcher: NameMatcher
    pairs: list[history.HistoryPair]
    hist_messages: list[dict]
    known_items: set[int]
    known_passages: set[int]
    hist_prices: set[int]


# --- helpers --------------------------------------------------------------------------------------


def sha12(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def log_args(raw: str) -> dict[str, Any]:
    """Redacted, size-capped arguments for tool_calls.arguments (never raw diner text)."""

    def scrub(v: Any) -> Any:
        if isinstance(v, str):
            return redact(v)[:ARG_LOG_CHARS]
        if isinstance(v, list):
            return [scrub(x) for x in v[:12]]
        if isinstance(v, dict):
            return {k: scrub(x) for k, x in list(v.items())[:12]}
        return v

    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {"_unparsed": redact(str(raw))[:ARG_LOG_CHARS]}
    out = scrub(data) if isinstance(data, dict) else {"_value": scrub(data)}
    return out if len(json.dumps(out)) <= 1000 else {"_truncated": True}


def _tool_status(res: ToolResult) -> str:
    err = res.payload.get("error") if not res.ok else None
    if err in ("invalid_arguments", "unknown_tool"):
        return err
    return "ok" if res.ok else "error"


def _assistant_message(r: LLMResult) -> dict[str, Any]:
    m: dict[str, Any] = {"role": "assistant", "content": r.message.content or ""}
    if r.message.tool_calls:
        m["tool_calls"] = [
            {
                "id": tc.id or f"call_{i}",
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.arguments},
            }
            for i, tc in enumerate(r.message.tool_calls)
        ]
    if r.message.reasoning_content:
        m["reasoning_content"] = r.message.reasoning_content
    return m


def _tool_msg(call_id: str, payload: dict | str) -> dict[str, str]:
    content = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    )
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _error_code(exc: LLMError) -> str:
    return "timeout" if exc.kind == "timeout" else "llm_unavailable"


# --- prepare --------------------------------------------------------------------------------------


async def _prepare(deps: ChatDeps, inp: TurnInput) -> _Prep:
    async with deps.sessionmaker() as db:
        conn = await db.connection()
        categories = await load_category_names(conn)
        facts = await load_item_facts(conn)
        pairs = await history.load_pairs(db, inp.session_id, inp.turn_index)
        window = history.select_window(pairs, deps.settings.history_turns)
        known_items: set[int] = set()
        known_passages: set[int] = set()
        for (rid,) in await db.execute(
            text("SELECT result_ids FROM tool_calls WHERE session_id = :s AND turn_id <> :t"),
            {"s": inp.session_id, "t": inp.turn_id},
        ):
            if isinstance(rid, dict):
                known_items.update(int(i) for i in rid.get("items", []))
                known_passages.update(int(i) for i in rid.get("passages", []))
        cited = await db.execute(
            text(
                "SELECT citations FROM chat_messages WHERE session_id = :s AND role = 'assistant' "
                "AND turn_id <> :t"
            ),
            {"s": inp.session_id, "t": inp.turn_id},
        )
        for (cits,) in cited:
            for c in cits or []:
                if c.get("kind") == "menu_item":
                    known_items.add(int(c["menu_item_id"]))
                elif c.get("kind") == "passage":
                    known_passages.add(int(c["chunk_id"]))
    hist_prices = {c for p in pairs for c in extract_price_cents(p.assistant)}
    return _Prep(
        categories=categories,
        facts=facts,
        matcher=NameMatcher(facts.values()),
        pairs=pairs,
        hist_messages=history.to_messages(window),
        known_items=known_items,
        known_passages=known_passages,
        hist_prices=hist_prices,
    )


# --- one LLM call + its tools, persisted --------------------------------------------------------


async def _log_llm_call(
    deps: ChatDeps,
    inp: TurnInput,
    call_index: int,
    pv: str,
    prefix: str,
    result: LLMResult | None,
    error: LLMError | None,
    tools_runner: Callable[[AsyncSession, int | None], Awaitable[None]] | None,
) -> None:
    """Insert the llm_calls row, commit, then run tools (if any) on the same short session and
    insert their tool_calls rows. The LLM call itself already finished: no connection was held."""
    s = deps.settings
    async with deps.sessionmaker() as db:
        row = LLMCall(
            session_id=inp.session_id,
            turn_id=inp.turn_id,
            call_index=call_index,
            attempt=(result.attempts if result else (error.attempts if error else 1)),
            model=(result.model if result else s.model),
            thinking=s.thinking,
            temperature=s.temperature,
            prompt_version=pv,
            prefix_sha256=prefix,
            prompt_tokens=result.usage.prompt_tokens if result else 0,
            cache_hit_tokens=result.usage.cache_hit_tokens if result else 0,
            cache_miss_tokens=result.usage.cache_miss_tokens if result else 0,
            completion_tokens=result.usage.completion_tokens if result else 0,
            reasoning_tokens=result.usage.reasoning_tokens if result else 0,
            cost_usd_micros=result.cost_usd_micros if result else 0,
            price_period=result.price_period if result else None,
            latency_ms=result.latency_ms if result else (error.latency_ms if error else None),
            finish_reason=result.finish_reason if result else None,
            status="ok" if result else "error",
            error_kind=error.kind if error else None,
            http_status=(result.http_status if result else (error.http_status if error else None)),
            request_id=inp.request_id,
        )
        db.add(row)
        await db.commit()
        if tools_runner is not None:
            await tools_runner(db, row.id)


# --- guardrails ----------------------------------------------------------------------------------


def _answer_context(
    args: RespondArgs,
    turn: TurnState,
    prep: _Prep,
    inp: TurnInput,
    sensitive: bool,
) -> AnswerContext:
    return AnswerContext(
        status=args.status,
        cited_item_ids=tuple(args.cited_item_ids),
        cited_passage_ids=tuple(args.cited_passage_ids),
        known_item_ids=frozenset(turn.item_ids | prep.known_items),
        known_passage_ids=frozenset(turn.passage_ids | prep.known_passages),
        grounded_this_turn=turn.grounded,
        allowed_price_cents=frozenset(
            turn.prices_cents | prep.hist_prices | set(extract_price_cents(inp.text))
        ),
        facts=prep.facts,
        allergen_sensitive=sensitive,
        canary=CANARY,
        system_prompt=SYSTEM_PROMPT,
    )


# --- compose --------------------------------------------------------------------------------------


async def _passage_citations(
    deps: ChatDeps, turn: TurnState, ids: list[int]
) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    missing = []
    for i in ids:
        p = turn.passages.get(i)
        if p:
            out[i] = {
                "kind": "passage",
                "chunk_id": i,
                "title": p.title,
                "section": p.section,
                "source_type": p.source_type,
            }
        else:
            missing.append(i)
    if missing:
        async with deps.sessionmaker() as db:
            rows = await db.execute(
                text(
                    "SELECT ch.id, d.title, ch.section_path, d.source_type FROM chunks ch "
                    "JOIN documents d ON d.id = ch.document_id WHERE ch.id = ANY(:ids)"
                ),
                {"ids": missing},
            )
            for r in rows:
                out[r.id] = {
                    "kind": "passage",
                    "chunk_id": r.id,
                    "title": r.title,
                    "section": r.section_path or "",
                    "source_type": r.source_type,
                }
    return out


async def _compose(
    deps: ChatDeps,
    inp: TurnInput,
    prep: _Prep,
    turn: TurnState,
    acc: _Acc,
    t0: float,
    pv: str,
    *,
    answer: str,
    status: str,
    cited_items: list[int],
    cited_passages: list[int],
    blocked: bool = False,
    error_code: str | None = None,
) -> TurnResult:
    """Final message: citations (only ids the session has really seen), DB-built allergen notice
    on every allergen-sensitive turn, and the outcome."""
    facts = prep.facts
    known_items = turn.item_ids | prep.known_items
    known_passages = turn.passage_ids | prep.known_passages
    valid_items = [i for i in dict.fromkeys(cited_items) if i in known_items and i in facts]
    valid_passages = [i for i in dict.fromkeys(cited_passages) if i in known_passages]

    actions = [] if error_code else list(turn.actions)
    sensitive = is_allergen_sensitive(turn, inp.text, inp.declared)
    if (error_code or blocked) and not any(a["type"] == "call_waiter" for a in actions):
        actions.append(
            {
                "type": "call_waiter",
                "action_id": str(uuid.uuid4()),
                "reason": "allergy_or_dietary" if sensitive else "question_not_answered",
                "note": None,
                "status": "proposed",
            }
        )

    if blocked:
        shown = items_for_notice(answer, valid_items, facts, prep.matcher)
        if not shown and sensitive:
            shown = list(turn.items.values())[:3]
        answer = build_fallback_message(shown)
        valid_items = [it.id for it in shown]
        valid_passages = []
        status = "blocked"
    if error_code:
        answer, status = ERROR_TEXT, "error"
        valid_items, valid_passages = [], []

    citations: list[dict[str, Any]] = [
        {
            "kind": "menu_item",
            "menu_item_id": i,
            "name": facts[i].name,
            "price_cents": facts[i].price_cents,
        }
        for i in valid_items
    ]
    pc = await _passage_citations(deps, turn, valid_passages)
    citations += [pc[i] for i in valid_passages if i in pc]

    notice = None
    if sensitive:
        action_items = [a["menu_item_id"] for a in actions if a["type"] == "add_to_cart"]
        covered = items_for_notice(answer, [*valid_items, *action_items], facts, prep.matcher)
        async with deps.sessionmaker() as db:
            conn = await db.connection()
            chunk_id = await find_disclaimer_chunk_id(conn, deps.chunk_config)
        notice = build_allergen_notice(covered, DISCLAIMER, chunk_id)

    grounded = turn.grounded or bool(set(valid_items) | set(valid_passages))
    outcome = assign_turn_outcome(
        error=bool(error_code),
        blocked=blocked,
        status=status,
        actions=actions,
        grounded=grounded,
        valid_citation=bool(valid_items or valid_passages),
    )
    return TurnResult(
        text=answer,
        status=status,
        outcome=outcome,
        citations=citations,
        allergen_notice=notice,
        actions=actions,
        guardrails=acc.guardrails,
        latency_ms=int((time.perf_counter() - t0) * 1000),
        prompt_version=pv,
        error_code=error_code,
        llm_calls=acc.n_llm,
        debug=_debug(acc, pv),
    )


def _debug(acc: _Acc, pv: str) -> dict[str, Any]:
    return {
        "prompt_version": pv,
        "llm_calls": acc.llm,
        "tool_calls": acc.tools,
        "guardrails": acc.guardrails,
    }


# --- the turn -------------------------------------------------------------------------------------


async def run_turn(deps: ChatDeps, inp: TurnInput, emit: Emit) -> TurnResult:
    """Run one diner turn to a TurnResult. Never raises for LLM/tool/guardrail trouble (those become
    an `error` / `blocked` result); only cancellation propagates."""
    s = deps.settings
    t0 = time.perf_counter()
    acc = _Acc()
    turn = TurnState()
    prep: _Prep | None = None
    pv = ""
    try:
        prep = await _prepare(deps, inp)
        pv = prompt_version(prep.categories, s.strict_tools)
        prefix = prefix_sha256(prep.categories, s.strict_tools)
        log.info(
            "chat_turn_started",
            session_id=str(inp.session_id),
            turn_id=str(inp.turn_id),
            turn_index=inp.turn_index,
            text_len=len(inp.text),
            text_sha=sha12(inp.text),
            declared_allergens=len(inp.declared),
            history_pairs=len(prep.pairs),
            prompt_version=pv,
            prefix_sha=prefix[:12],
            answer_cache=s.answer_cache,
        )
        async with asyncio.timeout(s.turn_deadline_s):
            cached = await _cache_stage(deps, inp, prep, pv, acc)
            if isinstance(cached, TurnResult):
                cached.latency_ms = int((time.perf_counter() - t0) * 1000)
                return _finish(cached, acc, t0)
            candidate = cached
            result = await _loop(deps, inp, prep, turn, acc, emit, t0, pv, prefix)
        result.cache_candidate = candidate
        await _maybe_store(deps, inp, prep, turn, acc, result, pv)
    except _TurnError as exc:
        if prep is None:
            prep = _Prep([], {}, NameMatcher([]), [], [], set(), set(), set())
        result = await _compose(
            deps, inp, prep, turn, acc, t0, pv, answer="", status="error",
            cited_items=[], cited_passages=[], error_code=exc.code,
        )  # fmt: skip
    except TimeoutError:
        prep = prep or _Prep([], {}, NameMatcher([]), [], [], set(), set(), set())
        result = await _compose(
            deps, inp, prep, turn, acc, t0, pv, answer="", status="error",
            cited_items=[], cited_passages=[], error_code="timeout",
        )  # fmt: skip
    except Exception:
        log.exception("chat_turn_internal_error", turn_id=str(inp.turn_id))
        prep = prep or _Prep([], {}, NameMatcher([]), [], [], set(), set(), set())
        result = await _compose(
            deps, inp, prep, turn, acc, t0, pv, answer="", status="error",
            cited_items=[], cited_passages=[], error_code="internal",
        )  # fmt: skip
    return _finish(result, acc, t0)


def _finish(result: TurnResult, acc: _Acc, t0: float) -> TurnResult:
    result.latency_ms = int((time.perf_counter() - t0) * 1000)
    metrics.CHAT_TURN_SECONDS.labels(result.outcome).observe(result.latency_ms / 1000)
    metrics.CHAT_TURN_OUTCOMES.labels(result.outcome).inc()
    metrics.CHAT_LLM_CALLS_PER_TURN.observe(acc.n_llm)
    if any(a["type"] == "call_waiter" for a in result.actions):
        metrics.CHAT_ESCALATIONS.labels("offered").inc()
    log.info(
        "chat_turn_done",
        outcome=result.outcome,
        status=result.status,
        error_code=result.error_code,
        latency_ms=result.latency_ms,
        llm_calls=acc.n_llm,
        tool_calls=len(acc.tools),
        guardrail_checks=[g["check"] for g in acc.guardrails],
        served_from_cache=result.served_from_cache,
        prompt_tokens=sum(c.get("prompt_tokens", 0) for c in acc.llm),
        cache_hit_tokens=sum(c.get("cache_hit_tokens", 0) for c in acc.llm),
        cost_usd_micros=sum(c.get("cost_usd_micros", 0) for c in acc.llm),
        citations=len(result.citations),
        actions=len(result.actions),
        allergen_notice=result.allergen_notice is not None,
    )
    result.debug = _debug(acc, result.prompt_version)
    return result


@dataclass
class _Batch:
    """Outcome of executing one LLM response's tool calls."""

    outbound: list[dict[str, str]] = field(default_factory=list)  # tool messages, in order
    respond_args: RespondArgs | None = None
    respond_call_id: str = ""


async def _execute_calls(
    deps: ChatDeps,
    inp: TurnInput,
    prep: _Prep,
    turn: TurnState,
    acc: _Acc,
    calls: list,
    batch: _Batch,
    ctx_tools: tuple[SessionContext, Callable[[str, str], Awaitable[None]]],
    db: AsyncSession,
    llm_call_id: int | None,
) -> None:
    """Run every tool call of one model response on `db` and log it. `respond` is validated here
    but not executed. A `respond` that arrives together with a search/get call is refused: the
    model wrote it without seeing the results."""
    session_ctx, stage = ctx_tools
    has_info = any(tc.name in INFO_TOOLS for tc in calls)
    conn = await db.connection()
    env = ToolEnv(
        conn=conn,
        settings=deps.settings,
        session=session_ctx,
        turn=turn,
        categories=prep.categories,
        embedder=deps.embedder,
        cache=deps.cache,
        counter=deps.counter,
        retrieval_base=deps.retrieval_base,
    )
    for i, tc in enumerate(calls):
        cid = tc.id or f"call_{i}"
        if tc.name == "respond":
            if has_info:
                metrics.CHAT_TOOL_CALLS.labels("respond", "error").inc()
                batch.outbound.append(
                    _tool_msg(
                        cid,
                        {
                            "error": "respond_too_early",
                            "details": ["call respond alone, after you have the tool results"],
                        },
                    )
                )
                continue
            args, err = validate_args("respond", tc.arguments)
            if err:
                metrics.CHAT_TOOL_CALLS.labels("respond", "invalid_arguments").inc()
                batch.outbound.append(_tool_msg(cid, err))
            else:
                metrics.CHAT_TOOL_CALLS.labels("respond", "ok").inc()
                batch.respond_args, batch.respond_call_id = args, cid  # type: ignore[assignment]
            continue
        known = tc.name in STAGE_BY_TOOL
        if known:
            await stage(*STAGE_BY_TOOL[tc.name])
        try:
            res = await run_tool(env, tc.name, tc.arguments)
        except Exception:
            log.exception("chat_tool_crashed", tool=tc.name)
            await db.rollback()
            env.conn = await db.connection()
            res = ToolResult(tc.name, {"error": "tool_failed"}, ok=False)
        status = _tool_status(res)
        label_name = tc.name if known else "unknown"
        metrics.CHAT_TOOL_CALLS.labels(label_name, status).inc()
        if known:
            metrics.CHAT_TOOL_SECONDS.labels(tc.name).observe(res.latency_ms / 1000)
        acc.tool_names.append(tc.name)
        acc.tools.append(
            {
                "name": tc.name,
                "status": status,
                "latency_ms": res.latency_ms,
                "signals": res.signals,
                "widened": res.widened,
            }
        )
        log.info(
            "tool_call",
            tool=label_name,
            status=status,
            latency_ms=res.latency_ms,
            result_items=len(res.result_item_ids),
            result_passages=len(res.result_passage_ids),
            widened=any(res.widened.values()),
            evidence=res.signals.get("evidence_strength"),
        )
        db.add(
            ToolCall(
                llm_call_id=llm_call_id,
                session_id=inp.session_id,
                turn_id=inp.turn_id,
                name=tc.name[:60],
                arguments=log_args(tc.arguments),
                status=status,
                widened=any(res.widened.values()),
                result_ids={"items": res.result_item_ids, "passages": res.result_passage_ids},
                signals=res.signals,
                retrieval_timings=res.timings_ms,
                latency_ms=int(res.latency_ms),
            )
        )
        batch.outbound.append(_tool_msg(cid, res.content()))
    await db.commit()


async def _loop(
    deps: ChatDeps,
    inp: TurnInput,
    prep: _Prep,
    turn: TurnState,
    acc: _Acc,
    emit: Emit,
    t0: float,
    pv: str,
    prefix: str,
) -> TurnResult:
    s = deps.settings
    tools = get_tools(prep.categories, s.strict_tools)
    session_ctx = SessionContext(inp.table_label, tuple(inp.declared), inp.text)
    messages: list[dict[str, Any]] = [
        system_message(),
        build_session_context(inp.table_label, inp.declared),
        *prep.hist_messages,
        {"role": "user", "content": inp.text},
    ]
    deadline = t0 + s.turn_deadline_s
    repairs = 0
    last_stage = ""

    async def stage(name: str, label: str) -> None:
        nonlocal last_stage
        if name != last_stage:
            last_stage = name
            await emit("status", {"stage": name, "label": label})

    for call_index in range(1, s.max_llm_calls + 1):
        remaining = deadline - time.perf_counter()
        if remaining <= 0.05:
            raise _TurnError("timeout")
        forced = call_index == s.max_llm_calls
        if call_index == 1:
            await stage("thinking", "Thinking...")
        try:
            result = await deps.llm.complete(
                messages,
                tools=tools,
                tool_choice=RESPOND_FORCED if forced else "required",
                max_tokens=s.max_tokens,
                deadline_s=remaining,
            )
        except LLMError as exc:
            acc.llm.append({"index": call_index, "error": exc.kind, "latency_ms": exc.latency_ms})
            await _log_llm_call(deps, inp, call_index, pv, prefix, None, exc, None)
            raise _TurnError(_error_code(exc)) from exc

        acc.llm.append(
            {
                "index": call_index,
                "latency_ms": result.latency_ms,
                "attempts": result.attempts,
                "prompt_tokens": result.usage.prompt_tokens,
                "cache_hit_tokens": result.usage.cache_hit_tokens,
                "cache_miss_tokens": result.usage.cache_miss_tokens,
                "completion_tokens": result.usage.completion_tokens,
                "cost_usd_micros": result.cost_usd_micros,
                "finish_reason": result.finish_reason,
                "tool_calls": [tc.name for tc in result.message.tool_calls],
            }
        )
        messages.append(_assistant_message(result))
        calls = result.message.tool_calls

        if not calls:
            await _log_llm_call(deps, inp, call_index, pv, prefix, result, None, None)
            if forced:
                raise _TurnError("internal")
            messages.append({"role": "user", "content": "Call a tool. Finish with respond."})
            continue

        batch = _Batch()
        ctx_tools = (session_ctx, stage)

        run_calls = functools.partial(
            _execute_calls, deps, inp, prep, turn, acc, calls, batch, ctx_tools
        )

        await _log_llm_call(deps, inp, call_index, pv, prefix, result, None, run_calls)
        messages.extend(batch.outbound)
        respond_args, respond_call_id = batch.respond_args, batch.respond_call_id

        if respond_args is None:
            if forced:
                # Last allowed call and still no valid respond: deterministic fallback.
                return await _blocked(deps, inp, prep, turn, acc, t0, pv, None)
            continue

        # --- guardrails -----------------------------------------------------------------------
        await stage("checking_answer", "Checking the answer...")
        sensitive = is_allergen_sensitive(turn, inp.text, inp.declared)
        ctx = _answer_context(respond_args, turn, prep, inp, sensitive)
        report = run_checks(respond_args.answer, ctx, prep.matcher)
        for v in report.violations:
            metrics.CHAT_GUARDRAIL_TRIGGERS.labels(v.check).inc()
            acc.guardrails.append(
                {
                    "check": v.check,
                    "detail": v.detail[:300],
                    "stage": "repair" if repairs else "initial",
                }
            )
            log.warning(
                "guardrail_triggered",
                check=v.check,
                repairable=v.repairable,
                repaired=bool(repairs),
            )
        if not report.needs_repair:
            return await _compose(
                deps, inp, prep, turn, acc, t0, pv,
                answer=report.answer, status=respond_args.status,
                cited_items=respond_args.cited_item_ids,
                cited_passages=respond_args.cited_passage_ids,
            )  # fmt: skip
        if repairs == 0 and call_index < s.max_llm_calls:
            repairs += 1
            acc.repaired = True
            bad = [v for v in report.violations if v.repairable]
            messages.append(
                _tool_msg(
                    respond_call_id,
                    {
                        "error": "answer_rejected",
                        "checks": [v.check for v in bad],
                        "details": [v.detail[:200] for v in bad],
                        "instruction": (
                            "Fix these problems and call respond again. Use only facts, prices "
                            "and ids from tool results; no safety claims about allergens. If you "
                            "cannot, say you do not know and offer call_waiter."
                        ),
                    },
                )
            )
            continue
        return await _blocked(deps, inp, prep, turn, acc, t0, pv, respond_args)

    raise _TurnError("internal")  # unreachable: the last call either responds or falls back


async def _blocked(
    deps: ChatDeps,
    inp: TurnInput,
    prep: _Prep,
    turn: TurnState,
    acc: _Acc,
    t0: float,
    pv: str,
    args: RespondArgs | None,
) -> TurnResult:
    return await _compose(
        deps, inp, prep, turn, acc, t0, pv,
        answer=args.answer if args else "", status="blocked",
        cited_items=list(args.cited_item_ids) if args else [],
        cited_passages=[], blocked=True,
    )  # fmt: skip


# --- answer cache ---------------------------------------------------------------------------------


async def _cache_stage(
    deps: ChatDeps, inp: TurnInput, prep: _Prep, pv: str, acc: _Acc
) -> "TurnResult | answer_cache.Candidate | None":
    """on: return a TurnResult served from cache on a hit. shadow: return the candidate (never
    served). Both: None / candidate when ineligible or missed."""
    s = deps.settings
    mode = s.answer_cache
    if mode == "off":
        return None
    if not answer_cache.question_eligible(
        inp.text, is_first_turn=not prep.pairs, declared=inp.declared
    ):
        metrics.CHAT_ANSWER_CACHE.labels(mode, "ineligible").inc()
        return None
    emb, _ = await embed_query_cached(
        deps.get_embedder(), answer_cache.normalize_question(inp.text), None, deps.cache
    )
    async with deps.sessionmaker() as db:
        versions = await answer_cache.current_versions(db, pv, s.model)
        cand = await answer_cache.lookup(db, emb, versions, s.answer_cache_threshold)
        if cand is not None and mode == "on":
            await answer_cache.bump_hits(db, cand.cache_id)
            await db.commit()
    metrics.CHAT_ANSWER_CACHE.labels(mode, "hit" if cand else "miss").inc()
    if cand is None:
        return None
    if mode == "shadow":
        return cand
    m = cand.message
    return TurnResult(
        text=m["text"],
        status=m.get("status", "answered"),
        outcome="answered",
        citations=m.get("citations", []),
        allergen_notice=None,
        actions=[],
        guardrails=[],
        latency_ms=0,
        prompt_version=pv,
        served_from_cache=True,
    )


async def _maybe_store(
    deps: ChatDeps,
    inp: TurnInput,
    prep: _Prep,
    turn: TurnState,
    acc: _Acc,
    result: TurnResult,
    pv: str,
) -> None:
    s = deps.settings
    if s.answer_cache == "off":
        return
    if not answer_cache.question_eligible(
        inp.text, is_first_turn=not prep.pairs, declared=inp.declared
    ):
        return
    if result.cache_candidate is not None:  # an equivalent answer is already cached
        return
    if not answer_cache.answer_storable(
        tool_names=acc.tool_names,
        status=result.status,
        outcome=result.outcome,
        violations=len(acc.guardrails),
        actions=len(result.actions),
        allergen_sensitive=result.allergen_notice is not None,
    ):
        return
    emb, _ = await embed_query_cached(
        deps.get_embedder(), answer_cache.normalize_question(inp.text), None, deps.cache
    )
    async with deps.sessionmaker() as db:
        versions = await answer_cache.current_versions(db, pv, s.model)
        await answer_cache.store(
            db,
            inp.text,
            emb,
            {"text": result.text, "status": result.status, "citations": result.citations},
            versions,
        )
        await db.commit()
    metrics.CHAT_ANSWER_CACHE.labels(s.answer_cache, "store").inc()
