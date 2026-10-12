"""Shared helpers for the chat orchestrator / API tests: a scripted FakeLLM and deps builder."""

import copy
import inspect
import json
import uuid

from sqlalchemy import text

from app.chat.llm import AssistantMessage, LLMError, LLMResult, ToolCall, Usage
from app.chat.orchestrator import ChatDeps
from app.chat.pricing import cost_usd_micros
from app.chat.settings import ChatSettings
from app.db import SessionLocal
from app.retrieval.cache import QueryEmbedCache
from app.retrieval.chunking import TokenCounter
from app.retrieval.config import RetrievalConfig
from tests.retrieval_helpers import FakeEmbedder, toy_tokenizer


def tc(name: str, raw: str | None = None, **args) -> ToolCall:
    return ToolCall(id=f"c-{uuid.uuid4().hex[:8]}", name=name, arguments=raw or json.dumps(args))


def respond(answer="ok", status="answered", items=(), passages=()) -> ToolCall:
    return tc(
        "respond",
        answer=answer,
        status=status,
        cited_item_ids=list(items),
        cited_passage_ids=list(passages),
    )


def reply(*calls: ToolCall, prompt=1000, hit=600) -> LLMResult:
    usage = Usage(prompt, hit, prompt - hit, 50, 0)
    cost, period = cost_usd_micros(
        "deepseek-flash", cache_hit_tokens=hit, cache_miss_tokens=prompt - hit, completion_tokens=50
    )
    return LLMResult(
        message=AssistantMessage(None, list(calls)),
        finish_reason="tool_calls",
        usage=usage,
        model="deepseek-flash",
        latency_ms=5,
        attempts=1,
        http_status=200,
        cost_usd_micros=cost,
        price_period=period,
    )


class FakeLLM:
    """Plays a script: each entry is an LLMResult, an LLMError (raised) or a callable(messages)."""

    def __init__(self, script=()):
        self.script = list(script)
        self.calls: list[dict] = []

    def add(self, *entries):
        self.script.extend(entries)

    async def complete(self, messages, *, tools=None, tool_choice=None, model=None,
                       max_tokens=None, deadline_s=None):  # fmt: skip
        self.calls.append(
            {
                "messages": copy.deepcopy(list(messages)),
                "tools": tools,
                "tool_choice": tool_choice,
                "deadline_s": deadline_s,
            }
        )
        assert self.script, "FakeLLM script exhausted"
        entry = self.script.pop(0)
        if callable(entry):
            entry = entry(messages)
        if inspect.isawaitable(entry):
            entry = await entry
        if isinstance(entry, Exception):
            raise entry
        return entry

    async def aclose(self):
        pass


def llm_error(kind="server", status=503) -> LLMError:
    return LLMError(kind, http_status=status, attempts=3, latency_ms=10)


def make_deps(llm: FakeLLM, **settings) -> ChatDeps:
    settings.setdefault("abstain_signal", False)  # no cross-encoder model in tests
    return ChatDeps(
        llm=llm,
        settings=ChatSettings(**settings),
        sessionmaker=SessionLocal,
        embedder=FakeEmbedder(),
        cache=QueryEmbedCache(16),
        counter=TokenCounter(toy_tokenizer("a b c")),
        retrieval_base=RetrievalConfig(chunk_config="t_tools"),
    )


async def clean_chat_tables() -> None:
    async with SessionLocal() as db:
        for t in ("answer_cache_shadow", "answer_cache", "waiter_requests", "tool_calls",
                  "llm_calls", "chat_actions", "chat_messages", "chat_sessions"):  # fmt: skip
            await db.execute(text(f"DELETE FROM {t}"))  # noqa: S608
        await db.commit()


async def item_id(name: str) -> int:
    async with SessionLocal() as db:
        return (
            await db.execute(text("SELECT id FROM menu_items WHERE name = :n"), {"n": name})
        ).scalar_one()


async def new_session(deps: ChatDeps, label: str = "T1") -> str:
    from app.chat import service

    async with SessionLocal() as db:
        token = (
            await db.execute(
                text("SELECT qr_token FROM dining_tables WHERE label=:l"), {"l": label}
            )
        ).scalar_one()
        return (await service.create_session(db, deps, token))["session_id"]


async def run_one(deps: ChatDeps, sid: str, msg: str, cmid: str | None = None):
    """Admit + execute one turn. Returns (TurnResult, Message, events)."""
    from app.chat import service

    async with SessionLocal() as db:
        begun = await service.begin_turn(db, deps, uuid.UUID(sid), msg, cmid or uuid.uuid4().hex)
    events: list[tuple[str, dict]] = []

    async def emit(event, data):
        events.append((event, data))

    result, message = await service.execute_turn(deps, begun, "rid-test", emit)
    return result, message, events


def last_tool_json(messages) -> dict:
    return json.loads([m for m in messages if m["role"] == "tool"][-1]["content"])


async def rows(sql: str, **params):
    async with SessionLocal() as db:
        return (await db.execute(text(sql), params)).all()
