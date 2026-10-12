"""Opt-in live DeepSeek check: RUN_LLM_TESTS=1 uv run pytest tests/test_llm_live.py -s
Costs well under $0.001 (two tiny calls). Key comes from DEEPSEEK_API_KEY or the repo-root .env."""

import os
import re
from pathlib import Path

import pytest

from app.chat.llm import DeepSeekClient
from app.chat.settings import ChatSettings

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LLM_TESTS") != "1", reason="set RUN_LLM_TESTS=1 to call the real API"
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_item",
            "description": "Look up a menu item by id.",
            "parameters": {
                "type": "object",
                "properties": {"item_id": {"type": "integer"}},
                "required": ["item_id"],
            },
        },
    }
]
# Long enough, and identical across both calls, to be a cacheable prefix.
SYSTEM = "You are a restaurant assistant. Always call a tool to answer. " + "Be precise. " * 200


def _key() -> str:
    if os.environ.get("DEEPSEEK_API_KEY"):
        return os.environ["DEEPSEEK_API_KEY"]
    env = Path(__file__).resolve().parents[2] / ".env"
    m = re.search(r"^DEEPSEEK_API_KEY=(.+)$", env.read_text(), re.M) if env.exists() else None
    return m.group(1).strip() if m else ""


async def test_live_tool_call_and_cache_fields():
    client = DeepSeekClient(ChatSettings(deepseek_api_key=_key()))
    msgs = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "Tell me about menu item 3."},
    ]
    try:
        first = await client.complete(
            msgs, tools=TOOLS, tool_choice={"type": "function", "function": {"name": "get_item"}}
        )
        assert first.message.tool_calls and first.message.tool_calls[0].name == "get_item"
        assert first.usage.prompt_tokens > 0 and first.usage.completion_tokens > 0
        assert (
            first.usage.cache_hit_tokens + first.usage.cache_miss_tokens
            == first.usage.prompt_tokens
        )
        assert first.cost_usd_micros > 0
        second = await client.complete(msgs, tools=TOOLS, tool_choice="required")
        # Field must exist; whether it is > 0 depends on best-effort server caching.
        print("prompt_cache_hit_tokens:", second.usage.cache_hit_tokens)
        assert second.usage.cache_hit_tokens >= 0
    finally:
        await client.aclose()
