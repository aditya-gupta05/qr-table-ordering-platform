"""DeepSeek price table (USD per 1M tokens) and cost calculation in USD micros.

Prices verified 2026-10-11 from api-docs.deepseek.com/quick_start/pricing (plan §0). When DeepSeek
changes prices, add a NEW dated entry instead of editing: llm_calls stores `price_period` and
`cost_usd_micros` at call time, so history stays reproducible.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

PRICES_AS_OF = "2026-10-11"


@dataclass(frozen=True)
class Price:
    """USD per 1M tokens."""

    cache_hit: Decimal
    cache_miss: Decimal
    output: Decimal


def _p(hit: str, miss: str, out: str) -> Price:
    return Price(Decimal(hit), Decimal(miss), Decimal(out))


# model -> period -> Price
PRICE_TABLE: dict[str, dict[str, Price]] = {
    "deepseek-flash": {
        "offpeak": _p("0.003", "0.15", "0.60"),
        "peak": _p("0.006", "0.30", "1.20"),
    },
    "deepseek-v4-pro": {
        "offpeak": _p("0.022", "0.66", "1.98"),
        "peak": _p("0.044", "1.32", "3.96"),
    },
}

# UTC, Monday-Friday only. [start, end) hours.
PEAK_WINDOWS_UTC = ((1, 4), (6, 10))


def price_period(at: datetime | None = None) -> str:
    """'peak' or 'offpeak' for a moment in time (naive datetimes are treated as UTC)."""
    at = at or datetime.now(UTC)
    at = at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)
    if at.weekday() < 5 and any(start <= at.hour < end for start, end in PEAK_WINDOWS_UTC):
        return "peak"
    return "offpeak"


def cost_usd_micros(
    model: str,
    *,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
    completion_tokens: int,
    at: datetime | None = None,
) -> tuple[int, str]:
    """Return (cost in USD micros, price period). Completion tokens include reasoning tokens.

    USD per 1M tokens equals micro-USD per token, so micros = sum(tokens * price).
    Unknown models cost 0 (and are visible as 0 in analytics) rather than failing a diner's turn.
    """
    period = price_period(at)
    prices = PRICE_TABLE.get(model)
    if prices is None:
        return 0, period
    p = prices[period]
    micros = (
        Decimal(cache_hit_tokens) * p.cache_hit
        + Decimal(cache_miss_tokens) * p.cache_miss
        + Decimal(completion_tokens) * p.output
    )
    return int(micros.quantize(Decimal(1), rounding=ROUND_HALF_UP)), period
