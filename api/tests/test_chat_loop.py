"""Orchestrator tests with a scripted FakeLLM against the seeded test DB (real tools)."""

import json

import pytest

from app.chat import history
from app.chat.orchestrator import RESPOND_FORCED
from tests.chat_helpers import (
    FakeLLM,
    clean_chat_tables,
    last_tool_json,
    llm_error,
    make_deps,
    new_session,
    reply,
    respond,
    rows,
    run_one,
    tc,
)


@pytest.fixture(autouse=True)
async def clean():
    await clean_chat_tables()
    yield
    await clean_chat_tables()


def cheapest_then_respond(messages):
    top = last_tool_json(messages)["items"][0]
    return reply(respond(f"The cheapest is {top['name']} at {top['price']}.", items=[top["id"]]))


async def test_search_then_respond():
    llm = FakeLLM([reply(tc("search_menu", query="", sort="price_asc")), cheapest_then_respond])
    deps = make_deps(llm)
    sid = await new_session(deps)
    result, msg, events = await run_one(deps, sid, "What is your cheapest dish?")
    assert result.outcome == "answered" and msg["status"] == "answered"
    assert len(llm.calls) == 2 and llm.calls[0]["tool_choice"] == "required"
    assert [e for e, _ in events] == ["status", "status", "status"]
    assert [d["stage"] for _, d in events] == ["thinking", "searching_menu", "checking_answer"]
    assert msg["citations"][0]["kind"] == "menu_item" and msg["allergen_notice"] is None
    # persisted logs: 2 llm calls, 1 tool call, no prompt text anywhere
    assert (await rows("SELECT count(*) FROM llm_calls"))[0][0] == 2
    (tool,) = await rows("SELECT name, status, arguments, result_ids FROM tool_calls")
    assert tool.name == "search_menu" and tool.status == "ok"
    assert tool.result_ids["items"] and tool.arguments["sort"] == "price_asc"
    cols = {r[0] for r in await rows("SELECT column_name FROM information_schema.columns "
                                     "WHERE table_name='llm_calls'")}  # fmt: skip
    assert not {"prompt", "messages", "content"} & cols


async def test_greeting_with_clarify_is_not_blocked():
    welcome = "Hi, welcome to Lantern Kitchen! I can help with the menu, prices and policies."
    llm = FakeLLM([reply(respond(welcome, "clarify"))])
    deps = make_deps(llm)
    result, msg, _ = await run_one(deps, await new_session(deps), "hi")
    assert result.outcome == "clarify" and msg["status"] == "clarify"
    assert result.guardrails == [] and len(llm.calls) == 1


async def test_invalid_args_get_error_message_and_retry():
    llm = FakeLLM(
        [
            reply(tc("search_menu", raw="{not json")),
            reply(tc("search_menu", query="", sort="price_asc")),
            cheapest_then_respond,
        ]
    )
    deps = make_deps(llm)
    result, _, _ = await run_one(deps, await new_session(deps), "cheapest dish?")
    assert json.loads(llm.calls[1]["messages"][-1]["content"])["error"] == "invalid_arguments"
    assert result.outcome == "answered" and result.llm_calls == 3
    statuses = [r[0] for r in await rows("SELECT status FROM tool_calls ORDER BY id")]
    assert statuses == ["invalid_arguments", "ok"]


async def test_unknown_tool_is_an_error_message_not_a_crash():
    llm = FakeLLM([reply(tc("drop_tables", x=1)), reply(respond("I don't know.", "not_found"))])
    deps = make_deps(llm)
    result, _, _ = await run_one(deps, await new_session(deps), "do something odd")
    assert json.loads(llm.calls[1]["messages"][-1]["content"])["error"] == "unknown_tool"
    assert result.outcome == "not_found"
    assert (await rows("SELECT status FROM tool_calls"))[0][0] == "unknown_tool"


async def test_cap_forces_named_respond_on_last_call():
    search = reply(tc("search_menu", query="", sort="price_asc"))
    llm = FakeLLM([search, search, reply(respond("Not sure.", "clarify"))])
    deps = make_deps(llm, max_llm_calls=3)
    result, _, _ = await run_one(deps, await new_session(deps), "hmm")
    assert [c["tool_choice"] for c in llm.calls] == ["required", "required", RESPOND_FORCED]
    assert result.llm_calls == 3 and result.outcome == "clarify"


async def test_ungrounded_answer_repaired_then_accepted():
    llm = FakeLLM(
        [
            reply(respond("We open at nine.", "answered")),
            reply(respond("I don't know.", "not_found")),
        ]
    )
    deps = make_deps(llm)
    result, _, _ = await run_one(deps, await new_session(deps), "when do you open?")
    err = json.loads(llm.calls[1]["messages"][-1]["content"])
    assert err["error"] == "answer_rejected" and "ungrounded_answer" in err["checks"]
    assert result.outcome == "not_found"
    assert [g["check"] for g in result.guardrails] == ["ungrounded_answer"]


async def test_ungrounded_twice_is_blocked_with_waiter_action():
    bad = reply(respond("We open at nine.", "answered"))
    llm = FakeLLM([bad, bad])
    deps = make_deps(llm)
    result, msg, _ = await run_one(deps, await new_session(deps), "when do you open?")
    assert result.outcome == "blocked" and msg["status"] == "blocked"
    assert "Call a waiter" in msg["text"] and "nine" not in msg["text"]
    assert [a["type"] for a in msg["actions"]] == ["call_waiter"]
    assert len(result.guardrails) == 2 and len(llm.calls) == 2


async def test_allergen_turn_always_has_notice_even_when_blocked():
    bad = reply(respond("It is totally safe for you.", "answered"))
    llm = FakeLLM([bad, bad])
    deps = make_deps(llm)
    result, msg, _ = await run_one(deps, await new_session(deps), "I'm allergic to peanuts, ok?")
    assert result.outcome == "blocked"
    assert msg["allergen_notice"]["disclaimer"]


async def test_llm_503_gives_error_fallback():
    llm = FakeLLM([llm_error("server", 503)])
    deps = make_deps(llm)
    sid = await new_session(deps)
    result, msg, _ = await run_one(deps, sid, "hello there")
    assert result.outcome == "error" and result.error_code == "llm_unavailable"
    assert msg["status"] == "error" and msg["actions"][0]["type"] == "call_waiter"
    assert llm.calls[0]["deadline_s"] <= deps.settings.turn_deadline_s
    (row,) = await rows("SELECT status, error_kind FROM llm_calls")
    assert (row.status, row.error_kind) == ("error", "server")
    assert (await rows("SELECT busy_until FROM chat_sessions"))[0][0] is None


async def test_llm_timeout_maps_to_timeout_code():
    deps = make_deps(FakeLLM([llm_error("timeout", None)]))
    result, _, _ = await run_one(deps, await new_session(deps), "hello there")
    assert result.error_code == "timeout"


async def test_prefix_bytes_identical_across_sessions_and_tables():
    ok = reply(respond("Only restaurant questions, sorry.", "off_topic"))
    llm = FakeLLM([ok, ok])
    deps = make_deps(llm)
    await run_one(deps, await new_session(deps, "T1"), "who won the game?")
    await run_one(deps, await new_session(deps, "T2"), "tell me a joke")
    a, b = llm.calls
    assert json.dumps(a["messages"][0]) == json.dumps(b["messages"][0])
    assert json.dumps(a["tools"]) == json.dumps(b["tools"])
    assert a["messages"][1] != b["messages"][1]  # session context is per table
    shas = {r[0] for r in await rows("SELECT prefix_sha256 FROM llm_calls")}
    assert len(shas) == 1


async def test_prior_tool_traffic_not_resent_and_items_rendered():
    llm = FakeLLM(
        [
            reply(tc("search_menu", query="", sort="price_asc")),
            cheapest_then_respond,
            reply(respond("Anything else?", "clarify")),
        ]
    )
    deps = make_deps(llm)
    sid = await new_session(deps)
    await run_one(deps, sid, "cheapest dish?")
    await run_one(deps, sid, "and then?")
    msgs = llm.calls[-1]["messages"]
    assert not [m for m in msgs if m["role"] == "tool" or m.get("tool_calls")]
    assert [m["role"] for m in msgs] == ["system", "system", "user", "assistant", "user"]
    assert "(items: " in msgs[3]["content"]


async def test_history_citations_count_as_known_ids():
    """A follow-up may cite an item shown in an earlier turn without searching again."""
    holder = {}

    def first(messages):
        top = last_tool_json(messages)["items"][0]
        holder["item"] = top
        return reply(respond(f"{top['name']} is {top['price']}.", items=[top["id"]]))

    def second(messages):
        t = holder["item"]
        return reply(respond(f"As said, {t['name']} is {t['price']}.", items=[t["id"]]))

    llm = FakeLLM([reply(tc("search_menu", query="", sort="price_asc")), first, second])
    deps = make_deps(llm)
    sid = await new_session(deps)
    await run_one(deps, sid, "cheapest dish?")
    result, _, _ = await run_one(deps, sid, "what was that again?")
    assert result.outcome == "answered" and result.guardrails == []


async def test_sse_busy_released_after_turn_and_declared_allergens_union():
    llm = FakeLLM([reply(respond("Please tell your server.", "needs_staff"))])
    deps = make_deps(llm)
    sid = await new_session(deps)
    result, msg, _ = await run_one(deps, sid, "I'm allergic to peanuts")
    assert result.outcome == "escalation_offered"
    (r,) = await rows("SELECT declared_allergens, busy_until, turn_count FROM chat_sessions")
    assert "peanut" in r.declared_allergens and r.busy_until is None and r.turn_count == 1
    assert msg["allergen_notice"] is not None


# --- history window (pure) ---


def pair(i, size=40):
    return history.HistoryPair(i, "q" * size, "a" * size)


def test_block_truncation_keeps_window_stable_between_cuts():
    pairs = [pair(i) for i in range(12)]
    starts = {n: history.window_start([p.tokens for p in pairs[:n]], 6) for n in range(1, 13)}
    assert all(starts[n] == 0 for n in range(1, 7))  # up to 6 pairs: everything
    assert starts[7] == 4  # >6 pairs: cut to the last 3
    assert starts[8] == starts[9] == starts[10] == 4  # stable: cache-friendly
    assert starts[11] == 8  # next cut
    assert len(history.select_window(pairs[:7], 6)) == 3


def test_token_cap_triggers_truncation():
    big = [pair(i, size=2000) for i in range(5)]  # ~1000 tokens per pair
    assert len(history.select_window(big, 6, token_cap=3000)) <= 3


async def test_declared_peanut_then_named_dish_add_to_cart_flow():
    from tests.chat_helpers import item_id

    pad = await item_id("Pad Thai")

    def fin(messages):
        cart = last_tool_json(messages)
        assert cart["allergen_conflict"] == ["peanut"]
        return reply(
            respond(
                "I can suggest adding the Pad Thai, but it contains peanut, which you declared. "
                "Please check with your server; the kitchen cannot rule out cross-contact.",
                items=[pad],
            )
        )

    def add(messages):
        got = last_tool_json(messages)
        assert got["name"] == "Pad Thai" and got["allergen_conflict"] == ["peanut"]
        return reply(tc("add_to_cart", item_id=got["id"], qty=1))

    llm = FakeLLM(
        [
            reply(respond("Noted, I will keep peanuts out of suggestions.", "clarify")),
            reply(tc("get_item", raw='{"item_id":0,"name":"Pad Thai"}')),
            add,
            fin,
        ]
    )
    deps = make_deps(llm)
    sid = await new_session(deps)
    await run_one(deps, sid, "I'm allergic to peanuts.")
    result, msg, _ = await run_one(deps, sid, "ok add the Pad Thai please")
    (act,) = msg["actions"]
    assert act["type"] == "add_to_cart" and act["allergen_conflict"] == ["peanut"]
    assert msg["allergen_notice"] and "safe" not in msg["text"].lower()
    assert result.outcome != "blocked"
