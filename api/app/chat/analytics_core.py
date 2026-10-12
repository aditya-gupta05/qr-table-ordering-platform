"""Pure analytics math for the chatbot (no DB, no I/O). Reused by WP3 (session outcome
assignment) and Phase 7 (eval reporting). Definitions follow docs/plans/phase-5-chatbot.md §6.

Honest limits: these are proxies measured on whatever traffic is in the DB. "answered" is not
"correct" (Phase 7 reports correct deflection) and diners who give up and wave at staff are
unobserved.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import sqrt

TURN_OUTCOMES = (
    "answered",
    "not_found",
    "escalation_offered",
    "off_topic",
    "clarify",
    "action_only",
    "blocked",
    "error",
)
# Turns that count in the deflection denominator (the diner asked something the bot should handle).
IN_SCOPE_OUTCOMES = frozenset({"answered", "not_found", "escalation_offered", "blocked", "error"})
ESCALATION_WINDOW = timedelta(minutes=10)
SESSION_IDLE = timedelta(minutes=15)


def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score interval for a binomial proportion; None when n == 0."""
    if n <= 0:
        return None
    if not 0 <= successes <= n:
        raise ValueError("successes must be within 0..n")
    p = successes / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def proportion(successes: int, n: int) -> dict:
    """{numerator, denominator, rate, ci95:[lo,hi]}; rate and ci95 are null when n == 0."""
    ci = wilson_ci(successes, n)
    return {
        "numerator": successes,
        "denominator": n,
        "rate": (successes / n) if n else None,
        "ci95": [ci[0], ci[1]] if ci else None,
    }


@dataclass(frozen=True)
class TurnRow:
    session_id: str
    outcome: str
    created_at: datetime
    helpful: bool | None = None  # False = marked unhelpful


@dataclass(frozen=True)
class WaiterRow:
    session_id: str
    created_at: datetime


def turn_deflection(
    turns: Iterable[TurnRow], waiters: Iterable[WaiterRow], window: timedelta = ESCALATION_WINDOW
) -> tuple[int, int]:
    """(deflected, in_scope). A deflected turn has outcome 'answered', no waiter request in the
    same session within `window` after the turn, and was not marked unhelpful."""
    by_session: dict[str, list[datetime]] = {}
    for w in waiters:
        by_session.setdefault(w.session_id, []).append(w.created_at)
    deflected = in_scope = 0
    for t in turns:
        if t.outcome not in IN_SCOPE_OUTCOMES:
            continue
        in_scope += 1
        if t.outcome != "answered" or t.helpful is False:
            continue
        escalated = any(
            t.created_at <= wt <= t.created_at + window for wt in by_session.get(t.session_id, ())
        )
        if not escalated:
            deflected += 1
    return deflected, in_scope


def session_outcome(
    turns: Sequence[TurnRow],
    waiters: Sequence[WaiterRow],
    last_activity_at: datetime,
    now: datetime,
) -> str | None:
    """Lazy session outcome (plan §6). None while the session is still active (idle < 15 min).

    no_question: no in-scope turn (nothing the bot should answer was asked) and no waiter request.
    escalated:   a waiter request exists for the session.
    resolved:    >= 1 answered turn, the last in-scope turn is answered, none marked unhelpful.
    abandoned:   in-scope turns exist but none of the above.
    WP3 persists this into chat_sessions.outcome; analytics reads the column and falls back here.
    """
    if now - last_activity_at < SESSION_IDLE:
        return None
    in_scope = sorted(
        (t for t in turns if t.outcome in IN_SCOPE_OUTCOMES), key=lambda t: t.created_at
    )
    if waiters:
        return "escalated"
    if not in_scope:
        return "no_question"
    answered = any(t.outcome == "answered" for t in in_scope)
    unhelpful = any(t.helpful is False for t in turns)
    if answered and in_scope[-1].outcome == "answered" and not unhelpful:
        return "resolved"
    return "abandoned"


def session_deflection(outcomes: Iterable[str | None]) -> tuple[int, int]:
    """(resolved, resolved + escalated + abandoned); active and no_question sessions excluded."""
    resolved = other = 0
    for o in outcomes:
        if o == "resolved":
            resolved += 1
        elif o in ("escalated", "abandoned"):
            other += 1
    return resolved, resolved + other


def guardrail_checks(rows: Iterable[object]) -> dict[str, int]:
    """Count guardrail triggers by check name. Each `chat_messages.guardrails` value is a list of
    strings or of objects with a `check` key."""
    counts: dict[str, int] = {}
    for lst in rows:
        for g in lst or []:
            name = g.get("check") if isinstance(g, dict) else g
            if name:
                counts[str(name)] = counts.get(str(name), 0) + 1
    return counts
