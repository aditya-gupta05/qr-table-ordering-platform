"""Chat metrics. Histogram/Counter only: Gauges need extra multiprocess config under several
uvicorn workers (see app/metrics.py)."""

from prometheus_client import Counter, Histogram

_LATENCY_BUCKETS = (0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 12, 20, 30)

CHAT_TURN_SECONDS = Histogram(
    "chat_turn_seconds", "End-to-end chat turn latency", ["outcome"], buckets=_LATENCY_BUCKETS
)
CHAT_LLM_CALL_SECONDS = Histogram(
    "chat_llm_call_seconds",
    "Latency of one LLM call, including retries",
    ["model", "status"],
    buckets=_LATENCY_BUCKETS,
)
CHAT_LLM_CALLS_PER_TURN = Histogram(
    "chat_llm_calls_per_turn", "LLM calls used by one turn", buckets=(1, 2, 3, 4, 5, 6)
)
CHAT_TOOL_SECONDS = Histogram(
    "chat_tool_seconds",
    "Tool execution time",
    ["tool"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
)
CHAT_LLM_TOKENS = Counter(
    "chat_llm_tokens_total", "LLM tokens", ["model", "kind"]
)  # kind: cache_hit|cache_miss|completion|reasoning
CHAT_LLM_COST_USD = Counter("chat_llm_cost_usd_total", "LLM spend in USD", ["model"])
CHAT_LLM_ERRORS = Counter(
    "chat_llm_errors_total", "Failed LLM calls", ["kind"]
)  # kind: timeout|rate_limited|server|auth|bad_request|payment|connect|parse
CHAT_TOOL_CALLS = Counter("chat_tool_calls_total", "Tool calls", ["tool", "status"])
CHAT_TURN_OUTCOMES = Counter("chat_turn_outcomes_total", "Chat turn outcomes", ["outcome"])
CHAT_GUARDRAIL_TRIGGERS = Counter(
    "chat_guardrail_triggers_total", "Deterministic post-check failures", ["check"]
)
CHAT_ESCALATIONS = Counter(
    "chat_escalations_total", "Waiter escalations", ["stage"]
)  # stage: offered|confirmed
CHAT_ANSWER_CACHE = Counter(
    "chat_answer_cache_total", "Semantic answer cache lookups", ["mode", "result"]
)
WAITER_REQUESTS = Counter("waiter_requests_total", "Waiter requests created", ["source"])
