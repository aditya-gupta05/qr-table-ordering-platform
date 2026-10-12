from datetime import UTC, datetime, timedelta

import pytest

from app.chat import analytics_core as core

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def turn(s, outcome, minutes=0, helpful=None):
    return core.TurnRow(s, outcome, T0 + timedelta(minutes=minutes), helpful)


def test_wilson_known_values():
    lo, hi = core.wilson_ci(1, 4)
    assert lo == pytest.approx(0.0456, abs=1e-3) and hi == pytest.approx(0.6994, abs=1e-3)
    lo, hi = core.wilson_ci(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-3) and hi == pytest.approx(0.5962, abs=1e-3)


def test_wilson_edges():
    assert core.wilson_ci(0, 0) is None
    lo, hi = core.wilson_ci(0, 10)
    assert lo == 0.0 and 0.25 < hi < 0.31
    lo, hi = core.wilson_ci(10, 10)
    assert hi == 1.0 and 0.69 < lo < 0.75
    with pytest.raises(ValueError):
        core.wilson_ci(5, 4)


def test_proportion_empty_has_nulls():
    assert core.proportion(0, 0) == {"numerator": 0, "denominator": 0, "rate": None, "ci95": None}


def test_turn_deflection_rules():
    turns = [
        turn("a", "answered"),  # deflected
        turn("b", "answered"),  # waiter 5 min later -> not deflected
        turn("c", "answered", helpful=False),  # unhelpful -> not deflected
        turn("d", "answered"),  # waiter 11 min later -> outside window -> deflected
        turn("e", "not_found"),  # in scope, never deflected
        turn("f", "off_topic"),  # out of scope
        turn("g", "action_only"),  # out of scope
    ]
    waiters = [
        core.WaiterRow("b", T0 + timedelta(minutes=5)),
        core.WaiterRow("d", T0 + timedelta(minutes=11)),
        core.WaiterRow("a", T0 - timedelta(minutes=1)),  # before the turn: ignored
    ]
    assert core.turn_deflection(turns, waiters) == (2, 5)


def test_session_outcome_rules():
    now = T0 + timedelta(hours=1)
    idle = T0 + timedelta(minutes=10)  # 50 min idle
    w = [core.WaiterRow("s", T0)]
    assert (
        core.session_outcome([turn("s", "answered")], [], now - timedelta(minutes=5), now) is None
    )
    assert core.session_outcome([turn("s", "answered")], [], idle, now) == "resolved"
    assert core.session_outcome([turn("s", "answered")], w, idle, now) == "escalated"
    assert core.session_outcome([turn("s", "off_topic")], [], idle, now) == "no_question"
    assert core.session_outcome([], [], idle, now) == "no_question"
    assert core.session_outcome([turn("s", "not_found")], [], idle, now) == "abandoned"
    assert (
        core.session_outcome([turn("s", "answered", 0), turn("s", "not_found", 1)], [], idle, now)
        == "abandoned"
    )
    assert (
        core.session_outcome([turn("s", "answered", helpful=False)], [], idle, now) == "abandoned"
    )
    assert (
        core.session_outcome([turn("s", "not_found", 0), turn("s", "answered", 1)], [], idle, now)
        == "resolved"
    )


def test_session_deflection_excludes_active_and_no_question():
    outs = ["resolved", "resolved", "escalated", "abandoned", "no_question", None]
    assert core.session_deflection(outs) == (2, 4)


def test_guardrail_checks_mixed_shapes():
    rows = [["price_mismatch"], [{"check": "allergen_claim"}, "price_mismatch"], [], None]
    assert core.guardrail_checks(rows) == {"price_mismatch": 2, "allergen_claim": 1}
