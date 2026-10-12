"""GET /api/analytics/chat: chatbot KPIs computed from the DB (docs/plans/phase-5-chatbot.md §6).

Window: optional `since` / `until` (ISO 8601, `until` exclusive). Sessions are windowed by
created_at; turns, LLM calls, tool calls, actions and waiter requests by their own created_at.
Unauthenticated, same known gap as the kitchen endpoints.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat import analytics_core as core
from app.db import get_session

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

# One statement shape for bounded and open windows (NULL parameter = no bound).
_W = (
    "(CAST(:since AS timestamptz) IS NULL OR {c} >= :since) AND "
    "(CAST(:until AS timestamptz) IS NULL OR {c} < :until)"
)


def _w(col: str) -> str:
    return _W.format(c=col)


def _f(v) -> float | None:
    return None if v is None else float(v)


@router.get("/chat")
async def chat_analytics(
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    p = {"since": since, "until": until}
    now = datetime.now(UTC)

    async def rows(sql: str, **extra):
        return (await session.execute(text(sql), {**p, **extra})).all()

    # --- sessions and turns -------------------------------------------------------------------
    sessions = await rows(
        f"SELECT id::text, last_activity_at, outcome FROM chat_sessions WHERE {_w('created_at')}"
    )
    turn_rows = await rows(
        "SELECT session_id::text, turn_outcome, created_at, feedback FROM chat_messages "
        f"WHERE role='assistant' AND turn_outcome IS NOT NULL AND {_w('created_at')}"
    )
    turns = [core.TurnRow(r[0], r[1], r[2], r[3]) for r in turn_rows]
    waiter_rows = await rows(
        "SELECT session_id::text, created_at FROM waiter_requests WHERE session_id IS NOT NULL"
    )
    waiters = [core.WaiterRow(r[0], r[1]) for r in waiter_rows]

    outcome_counts = dict.fromkeys(core.TURN_OUTCOMES, 0)
    for t in turns:
        outcome_counts[t.outcome] = outcome_counts.get(t.outcome, 0) + 1

    deflected, in_scope = core.turn_deflection(turns, waiters)

    # Session outcome: stored column when set (WP3), else lazy per plan §6.
    turns_by_s: dict[str, list[core.TurnRow]] = {}
    for t in turns:
        turns_by_s.setdefault(t.session_id, []).append(t)
    waiters_by_s: dict[str, list[core.WaiterRow]] = {}
    for w in waiters:
        waiters_by_s.setdefault(w.session_id, []).append(w)
    s_outcomes: list[str | None] = [
        s[2]
        or core.session_outcome(turns_by_s.get(s[0], []), waiters_by_s.get(s[0], []), s[1], now)
        for s in sessions
    ]
    s_counts = {k: 0 for k in ("resolved", "escalated", "abandoned", "no_question", "active")}
    for o in s_outcomes:
        s_counts[o or "active"] += 1
    resolved, s_den = core.session_deflection(s_outcomes)

    # --- actions ------------------------------------------------------------------------------
    act = {
        (r[0], r[1]): r[2]
        for r in await rows(
            f"SELECT kind, status, count(*) FROM chat_actions WHERE {_w('created_at')} "
            "GROUP BY kind, status"
        )
    }

    def acts(kind: str, status: str | None = None) -> int:
        return sum(n for (k, s), n in act.items() if k == kind and (status is None or s == status))

    # --- LLM calls ----------------------------------------------------------------------------
    (llm,) = await rows(
        "SELECT count(*), count(*) FILTER (WHERE status <> 'ok'), "
        "coalesce(sum(cache_hit_tokens),0), coalesce(sum(cache_miss_tokens),0), "
        "coalesce(sum(completion_tokens),0), coalesce(sum(reasoning_tokens),0), "
        "coalesce(sum(cost_usd_micros),0), "
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms), "
        "percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) "
        f"FROM llm_calls WHERE {_w('created_at')}"
    )
    hit, miss = int(llm[2]), int(llm[3])
    cost_usd = int(llm[6]) / 1_000_000
    (lat,) = await rows(
        "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms), "
        "percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) FROM chat_messages "
        f"WHERE role='assistant' AND turn_outcome IS NOT NULL AND latency_ms IS NOT NULL "
        f"AND {_w('created_at')}"
    )

    # --- tools and guardrails -----------------------------------------------------------------
    tools = {
        r[0]: {"calls": r[1], "errors": r[2]}
        for r in await rows(
            "SELECT name, count(*), count(*) FILTER (WHERE status <> 'ok') FROM tool_calls "
            f"WHERE {_w('created_at')} GROUP BY name ORDER BY name"
        )
    }
    guard = core.guardrail_checks(
        r[0]
        for r in await rows(
            "SELECT guardrails FROM chat_messages WHERE role='assistant' "
            f"AND jsonb_array_length(guardrails) > 0 AND {_w('created_at')}"
        )
    )

    # --- latency split per turn: tools vs LLM vs everything else -----------------------------
    (split,) = await rows(
        "WITH t AS (SELECT turn_id, latency_ms FROM chat_messages WHERE role='assistant' "
        f"AND turn_outcome IS NOT NULL AND latency_ms IS NOT NULL AND {_w('created_at')}), "
        "l AS (SELECT turn_id, sum(latency_ms) ms FROM llm_calls GROUP BY turn_id), "
        "c AS (SELECT turn_id, sum(latency_ms) ms FROM tool_calls GROUP BY turn_id) "
        "SELECT count(*), coalesce(sum(t.latency_ms),0), coalesce(sum(l.ms),0), "
        "coalesce(sum(c.ms),0) FROM t LEFT JOIN l USING (turn_id) LEFT JOIN c USING (turn_id)"
    )
    n_split, total_ms, llm_ms, tool_ms = (int(x) for x in split)
    other_ms = max(total_ms - llm_ms - tool_ms, 0)

    def share(x: int) -> float | None:
        return x / total_ms if total_ms else None

    n_turns, n_sessions = len(turns), len(sessions)
    return {
        "window": {
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
        },
        "sessions": {"total": n_sessions, "outcomes": s_counts},
        "turns": {"total": n_turns, "in_scope": in_scope, "outcomes": outcome_counts},
        "deflection": {
            "turn": core.proportion(deflected, in_scope),
            "session": core.proportion(resolved, s_den),
            "definition": (
                "turn: answered turns with no waiter request in the same session within 10 min "
                "and not marked unhelpful, over in-scope turns (answered, not_found, "
                "escalation_offered, blocked, error). session: resolved / (resolved + escalated "
                "+ abandoned). Wilson 95% CI. A proxy, not correctness."
            ),
        },
        "escalations": {
            "offered": acts("call_waiter"),
            "confirmed": acts("call_waiter", "accepted"),
        },
        "cart": {"proposed": acts("add_to_cart"), "accepted": acts("add_to_cart", "accepted")},
        "llm": {
            "calls": llm[0],
            "errors": llm[1],
            "calls_per_turn": (llm[0] / n_turns) if n_turns else None,
            "call_latency_ms": {"p50": _f(llm[7]), "p95": _f(llm[8])},
            "tokens": {
                "cache_hit": hit,
                "cache_miss": miss,
                "completion": int(llm[4]),
                "reasoning": int(llm[5]),
            },
            "cache_hit_ratio": (hit / (hit + miss)) if hit + miss else None,
            "cost_usd": {
                "total": cost_usd,
                "per_turn": (cost_usd / n_turns) if n_turns else None,
                "per_session": (cost_usd / n_sessions) if n_sessions else None,
            },
        },
        "turn_latency_ms": {"p50": _f(lat[0]), "p95": _f(lat[1])},
        "tools": {
            "calls": sum(v["calls"] for v in tools.values()),
            "errors": sum(v["errors"] for v in tools.values()),
            "by_tool": tools,
        },
        "guardrails": {"total": sum(guard.values()), "by_check": guard},
        "latency_split": {
            "turns": n_split,
            "total_ms": total_ms,
            "llm_ms": llm_ms,
            "retrieval_ms": tool_ms,  # tool execution time (search_menu/search_info dominate)
            "other_ms": other_ms,
            "share": {"llm": share(llm_ms), "retrieval": share(tool_ms), "other": share(other_ms)},
        },
    }
