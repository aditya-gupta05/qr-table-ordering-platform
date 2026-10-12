"""LLM client: Protocol + raw-httpx DeepSeek adapter (OpenAI-compatible /chat/completions).

Why raw httpx and not the openai SDK: no extra dependency, direct access to DeepSeek-specific usage
fields (prompt_cache_hit_tokens), and httpx.MockTransport makes the whole client testable offline.

Non-streaming only (plan decision 5). While a request is queued, DeepSeek keeps the connection open
with blank lines / ': keep-alive' comments before the JSON body; those reset httpx's read timeout,
so the ONLY real bound on a call is the overall deadline enforced here with asyncio.timeout.
"""

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
import structlog

from app.chat import metrics
from app.chat.pricing import cost_usd_micros
from app.chat.settings import ChatSettings, get_chat_settings

log = structlog.get_logger(__name__)

RETRY_STATUSES = frozenset({429, 500, 503})
# error kind by HTTP status; used for metrics and llm_calls.error_kind
_KIND_BY_STATUS = {
    400: "bad_request",
    401: "auth",
    402: "payment",
    422: "bad_request",
    429: "rate_limited",
    500: "server",
    503: "server",
}


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string as sent by the model; the caller validates it


@dataclass(frozen=True)
class AssistantMessage:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning_content: str | None = None


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    completion_tokens: int = 0  # includes reasoning tokens (billed as output)
    reasoning_tokens: int = 0


@dataclass(frozen=True)
class LLMResult:
    message: AssistantMessage
    finish_reason: str | None
    usage: Usage
    model: str
    latency_ms: int
    attempts: int
    http_status: int
    cost_usd_micros: int
    price_period: str


class LLMError(Exception):
    """Raised when a call fails for good. Never carries the API key or request body."""

    def __init__(
        self,
        kind: str,
        *,
        http_status: int | None = None,
        attempts: int = 1,
        latency_ms: int = 0,
        detail: str = "",
    ):
        self.kind = kind  # timeout|connect|rate_limited|server|auth|bad_request|payment|parse
        self.http_status = http_status
        self.attempts = attempts
        self.latency_ms = latency_ms
        self.detail = detail
        super().__init__(f"llm_error kind={kind} status={http_status} attempts={attempts}")


class LLMClient(Protocol):
    async def complete(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        deadline_s: float | None = None,
    ) -> LLMResult: ...

    async def aclose(self) -> None: ...


def parse_body(text: str) -> dict[str, Any]:
    """Parse a non-streaming body that may start with keep-alive blank/comment lines."""
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith(":")]
    try:
        data = json.loads("\n".join(lines))
    except json.JSONDecodeError as exc:
        raise LLMError("parse", detail="body is not JSON") from exc
    if not isinstance(data, dict):
        raise LLMError("parse", detail="body is not an object")
    return data


def parse_usage(raw: dict[str, Any] | None) -> Usage:
    raw = raw or {}
    prompt = int(raw.get("prompt_tokens") or 0)
    hit = raw.get("prompt_cache_hit_tokens")
    if hit is None:
        hit = (raw.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    hit = int(hit)
    miss = raw.get("prompt_cache_miss_tokens")
    miss = int(miss) if miss is not None else max(prompt - hit, 0)
    completion = int(raw.get("completion_tokens") or 0)
    reasoning = int((raw.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
    return Usage(prompt, hit, miss, completion, reasoning)


def parse_completion(data: dict[str, Any]) -> tuple[AssistantMessage, str | None, Usage]:
    try:
        choice = data["choices"][0]
        msg = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("parse", detail="no choices in response") from exc
    calls = [
        ToolCall(
            id=tc.get("id", ""),
            name=(tc.get("function") or {}).get("name", ""),
            arguments=(tc.get("function") or {}).get("arguments") or "",
        )
        for tc in (msg.get("tool_calls") or [])
    ]
    return (
        AssistantMessage(msg.get("content"), calls, msg.get("reasoning_content")),
        choice.get("finish_reason"),
        parse_usage(data.get("usage")),
    )


class DeepSeekClient:
    def __init__(
        self,
        settings: ChatSettings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        backoff_s: Sequence[float] = (0.5, 1.5),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._s = settings or get_chat_settings()
        self._key = self._s.deepseek_api_key.get_secret_value()
        self._backoff = tuple(backoff_s)
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            base_url=self._s.effective_base_url,
            transport=transport,
            timeout=httpx.Timeout(
                connect=self._s.connect_timeout_s,
                read=self._s.read_timeout_s,
                write=5.0,
                pool=5.0,
            ),
        )

    def __repr__(self) -> str:  # never include the key
        return f"DeepSeekClient(model={self._s.model!r}, base_url={self._s.effective_base_url!r})"

    async def aclose(self) -> None:
        await self._http.aclose()

    def build_body(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]] | None,
        tool_choice: str | dict[str, Any] | None,
        model: str,
        max_tokens: int,
    ) -> dict[str, Any]:
        s = self._s
        body: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "max_tokens": max_tokens,
        }
        if s.thinking == "off":
            # Thinking is ON by default server-side, so "off" must be sent explicitly.
            body["thinking"] = {"type": "disabled"}
            body["temperature"] = s.temperature
        else:
            # Thinking mode ignores temperature and rejects required/named tool_choice (400).
            body["thinking"] = {"type": "enabled"}
            body["reasoning_effort"] = s.thinking
            if tool_choice not in (None, "auto", "none"):
                tool_choice = "auto"
        if tools:
            body["tools"] = list(tools)
            if tool_choice is not None:
                body["tool_choice"] = tool_choice
        return body

    async def complete(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        deadline_s: float | None = None,
    ) -> LLMResult:
        model = model or self._s.model
        body = self.build_body(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            model=model,
            max_tokens=max_tokens or self._s.max_tokens,
        )
        started = time.perf_counter()
        attempts = 0
        status: int | None = None
        try:
            async with asyncio.timeout(deadline_s or self._s.turn_deadline_s):
                while True:
                    attempts += 1
                    retry_reason, status, data = await self._attempt(body, attempts, started)
                    if data is not None:
                        return self._finish(data, model, attempts, status, started)
                    if attempts > self._s.retries:
                        raise self._error(retry_reason, status, attempts, started)
                    delay = self._backoff[min(attempts - 1, len(self._backoff) - 1)]
                    await self._sleep(delay + random.uniform(0, delay * 0.25))
        except TimeoutError:
            err = LLMError(
                "timeout", http_status=status, attempts=attempts, latency_ms=_ms(started)
            )
        except LLMError as exc:
            exc.attempts = attempts
            exc.latency_ms = _ms(started)
            err = exc
        metrics.CHAT_LLM_ERRORS.labels(err.kind).inc()
        metrics.CHAT_LLM_CALL_SECONDS.labels(model, "error").observe(err.latency_ms / 1000)
        log.warning(
            "llm_call_failed",
            model=model,
            kind=err.kind,
            http_status=err.http_status,
            attempts=err.attempts,
            latency_ms=err.latency_ms,
        )
        raise err

    async def _attempt(
        self, body: dict[str, Any], attempt: int, started: float
    ) -> tuple[str, int | None, dict[str, Any] | None]:
        """One HTTP try. Returns (retry_kind, status, data); data is None when retryable.
        Non-retryable failures raise LLMError."""
        t0 = time.perf_counter()
        try:
            resp = await self._http.post(
                "/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self._key}"},
            )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            log.info("llm_http_attempt", attempt=attempt, outcome="connect_error")
            return "connect", None, None
        status = resp.status_code
        log.info("llm_http_attempt", attempt=attempt, status=status, latency_ms=_ms(t0))
        if status in RETRY_STATUSES:
            return _KIND_BY_STATUS[status], status, None
        if status >= 400:
            raise LLMError(
                _KIND_BY_STATUS.get(status, "bad_request" if status < 500 else "server"),
                http_status=status,
                detail=self._scrub(_error_message(resp.text)),
            )
        return "", status, parse_body(resp.text)

    def _scrub(self, text: str) -> str:
        return text.replace(self._key, "***")[:300] if self._key else text[:300]

    @staticmethod
    def _error(kind: str, status: int | None, attempts: int, started: float) -> LLMError:
        return LLMError(kind, http_status=status, attempts=attempts, latency_ms=_ms(started))

    def _finish(
        self, data: dict[str, Any], model: str, attempts: int, status: int | None, started: float
    ) -> LLMResult:
        message, finish_reason, usage = parse_completion(data)
        cost, period = cost_usd_micros(
            model,
            cache_hit_tokens=usage.cache_hit_tokens,
            cache_miss_tokens=usage.cache_miss_tokens,
            completion_tokens=usage.completion_tokens,
        )
        latency_ms = _ms(started)
        m = metrics
        m.CHAT_LLM_CALL_SECONDS.labels(model, "ok").observe(latency_ms / 1000)
        for kind, n in (
            ("cache_hit", usage.cache_hit_tokens),
            ("cache_miss", usage.cache_miss_tokens),
            ("completion", usage.completion_tokens),
            ("reasoning", usage.reasoning_tokens),
        ):
            m.CHAT_LLM_TOKENS.labels(model, kind).inc(n)
        m.CHAT_LLM_COST_USD.labels(model).inc(cost / 1_000_000)
        log.info(
            "llm_call",
            model=model,
            attempts=attempts,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
            prompt_tokens=usage.prompt_tokens,
            prompt_cache_hit_tokens=usage.cache_hit_tokens,
            prompt_cache_miss_tokens=usage.cache_miss_tokens,
            completion_tokens=usage.completion_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            cost_usd_micros=cost,
            price_period=period,
            tool_calls=len(message.tool_calls),
        )
        return LLMResult(
            message=message,
            finish_reason=finish_reason,
            usage=usage,
            model=model,
            latency_ms=latency_ms,
            attempts=attempts,
            http_status=status or 200,
            cost_usd_micros=cost,
            price_period=period,
        )


def _ms(since: float) -> int:
    return int((time.perf_counter() - since) * 1000)


def _error_message(text: str) -> str:
    try:
        err = parse_body(text).get("error")
        return str(err.get("message") if isinstance(err, dict) else err or "")
    except LLMError:
        return ""
