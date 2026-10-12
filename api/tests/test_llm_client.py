"""DeepSeekClient tests with httpx.MockTransport (no network)."""

import asyncio
import json
import logging
from datetime import UTC, datetime

import httpx
import pytest
import structlog

from app.chat.llm import DeepSeekClient, LLMError
from app.chat.pricing import cost_usd_micros, price_period
from app.chat.settings import ChatSettings

KEY = "sk-test-SECRET-1234567890"

OK_BODY = {
    "choices": [
        {
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "search_menu", "arguments": '{"query": "soup"}'},
                    }
                ],
            },
        }
    ],
    "usage": {
        "prompt_tokens": 3000,
        "prompt_cache_hit_tokens": 1700,
        "prompt_cache_miss_tokens": 1300,
        "completion_tokens": 150,
        "completion_tokens_details": {"reasoning_tokens": 20},
    },
}


def make_client(handler, **kw) -> DeepSeekClient:
    settings = ChatSettings(deepseek_api_key=KEY, **kw)
    return DeepSeekClient(
        settings,
        transport=httpx.MockTransport(handler),
        backoff_s=(0.01, 0.01),
    )


MSGS = [{"role": "user", "content": "hi"}]


async def test_usage_parsing_and_offpeak_cost():
    c = make_client(lambda req: httpx.Response(200, json=OK_BODY))
    res = await c.complete(MSGS, tools=[{"type": "function"}])
    u = res.usage
    assert (u.prompt_tokens, u.cache_hit_tokens, u.cache_miss_tokens) == (3000, 1700, 1300)
    assert (u.completion_tokens, u.reasoning_tokens) == (150, 20)
    assert res.message.tool_calls[0].name == "search_menu"
    assert res.finish_reason == "tool_calls"
    assert res.attempts == 1 and res.http_status == 200
    await c.aclose()


def test_cost_peak_vs_offpeak():
    kw = dict(cache_hit_tokens=1700, cache_miss_tokens=1300, completion_tokens=150)
    monday_peak = datetime(2026, 10, 12, 7, 0, tzinfo=UTC)  # Mon 07:00 UTC
    monday_off = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
    # off-peak: 1700*0.003 + 1300*0.15 + 150*0.60 = 5.1 + 195 + 90 = 290.1 micros
    assert cost_usd_micros("deepseek-flash", at=monday_off, **kw) == (290, "offpeak")
    # peak is exactly double for flash
    assert cost_usd_micros("deepseek-flash", at=monday_peak, **kw) == (580, "peak")
    # v4-pro off-peak: 1700*0.022 + 1300*0.66 + 150*1.98 = 37.4 + 858 + 297 = 1192.4
    assert cost_usd_micros("deepseek-v4-pro", at=monday_off, **kw)[0] == 1192
    assert cost_usd_micros("unknown-model", at=monday_off, **kw)[0] == 0


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        (datetime(2026, 10, 12, 1, 0, tzinfo=UTC), "peak"),  # Mon 01:00
        (datetime(2026, 10, 12, 4, 0, tzinfo=UTC), "offpeak"),  # end exclusive
        (datetime(2026, 10, 12, 5, 59, tzinfo=UTC), "offpeak"),
        (datetime(2026, 10, 12, 9, 59, tzinfo=UTC), "peak"),
        (datetime(2026, 10, 17, 7, 0, tzinfo=UTC), "offpeak"),  # Saturday
    ],
)
def test_peak_window(when, expected):
    assert price_period(when) == expected


async def test_retry_503_then_200():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, json=OK_BODY)

    c = make_client(handler)
    res = await c.complete(MSGS)
    assert len(calls) == 2 and res.attempts == 2


async def test_gives_up_after_retries():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(429)

    c = make_client(handler)
    with pytest.raises(LLMError) as ei:
        await c.complete(MSGS)
    assert len(calls) == 3  # 1 try + 2 retries
    assert ei.value.kind == "rate_limited" and ei.value.attempts == 3


@pytest.mark.parametrize(
    ("status", "kind"), [(400, "bad_request"), (401, "auth"), (402, "payment")]
)
async def test_no_retry_on_client_errors(status, kind):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(status, json={"error": {"message": "nope"}})

    c = make_client(handler)
    with pytest.raises(LLMError) as ei:
        await c.complete(MSGS)
    assert len(calls) == 1
    assert ei.value.kind == kind and ei.value.http_status == status


async def test_keepalive_blank_lines_then_json():
    body = "\n\n: keep-alive\n\n" + json.dumps(OK_BODY)
    c = make_client(lambda req: httpx.Response(200, content=body.encode()))
    res = await c.complete(MSGS)
    assert res.usage.cache_hit_tokens == 1700


async def test_stalled_server_hits_overall_deadline():
    async def handler(req):
        await asyncio.sleep(5)
        return httpx.Response(200, json=OK_BODY)

    c = make_client(handler)
    with pytest.raises(LLMError) as ei:
        await c.complete(MSGS, deadline_s=0.2)
    assert ei.value.kind == "timeout"
    assert ei.value.latency_ms < 1500


async def test_request_body_disables_thinking():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        seen["auth"] = req.headers["authorization"]
        seen["url"] = str(req.url)
        return httpx.Response(200, json=OK_BODY)

    c = make_client(handler)
    await c.complete(MSGS, tools=[{"type": "function"}], tool_choice="required")
    b = seen["body"]
    assert b["thinking"] == {"type": "disabled"}
    assert b["model"] == "deepseek-flash"
    assert b["temperature"] == 0.0 and b["max_tokens"] == 600
    assert b["tool_choice"] == "required"
    assert seen["auth"] == f"Bearer {KEY}"
    assert seen["url"] == "https://api.deepseek.com/chat/completions"


async def test_thinking_mode_params_and_strict_beta_url():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        seen["url"] = str(req.url)
        return httpx.Response(200, json=OK_BODY)

    c = make_client(handler, thinking="high", strict_tools=True)
    await c.complete(MSGS, tools=[{"type": "function"}], tool_choice="required")
    b = seen["body"]
    assert b["thinking"] == {"type": "enabled"} and b["reasoning_effort"] == "high"
    assert "temperature" not in b
    assert b["tool_choice"] == "auto"  # required is rejected by the API in thinking mode
    assert seen["url"] == "https://api.deepseek.com/beta/chat/completions"


async def test_api_key_never_in_logs_or_errors(capsys):
    structlog.reset_defaults()
    from app.logging import configure_logging

    configure_logging("DEBUG")
    handler_log = logging.getLogger().handlers[0]

    def handler(req):
        # a hostile upstream that echoes the key back in its error message
        return httpx.Response(400, json={"error": {"message": f"bad key {KEY}"}})

    c = make_client(handler)
    with pytest.raises(LLMError) as ei:
        await c.complete(MSGS)
    ok = make_client(lambda req: httpx.Response(200, json=OK_BODY))
    await ok.complete(MSGS)
    handler_log.flush()
    out = capsys.readouterr().out
    assert "llm_http_attempt" in out and "llm_call" in out
    assert KEY not in out
    assert KEY not in str(ei.value) and KEY not in repr(ei.value) and KEY not in ei.value.detail
    assert KEY not in repr(c) and KEY not in repr(ChatSettings(deepseek_api_key=KEY))
    assert KEY not in ChatSettings(deepseek_api_key=KEY).model_dump_json()


def test_settings_defaults_and_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-env")
    monkeypatch.setenv("CHAT_THINKING", "low")
    s = ChatSettings()
    assert s.deepseek_api_key.get_secret_value() == "from-env"
    assert s.thinking == "low" and s.model == "deepseek-flash" and s.abstain_threshold == -3.25
