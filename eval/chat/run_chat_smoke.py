# ruff: noqa: E501, ASYNC230, ASYNC240, T201  (CLI script: long prose strings, blocking file I/O and prints are fine)
"""Chat smoke eval (Phase 5, WP6). Drives the real orchestrator in-process (service.begin_turn /
execute_turn: same code path as POST /chat/sessions/{sid}/messages) against the real DB and retrieval.

  cd api
  uv run python ../eval/chat/run_chat_smoke.py --dry-run          # FakeLLM, free, tests the harness
  uv run python ../eval/chat/run_chat_smoke.py --label smoke1     # real DeepSeek, hard-capped

Hard caps: --max-llm-calls (default 100) and --max-cost-usd (default 0.25) are checked BEFORE every
LLM call; the run aborts (partial results are still written, exit code 2) when either would be hit.

The API key is read from the repo .env (DEEPSEEK_API_KEY only; a stale exported variable is
overridden) and is never printed. Needs the compose postgres (qr DB, seeded + ingested).

Isolation: the run creates a dedicated dining table (label SMOKE-<run id>), runs one session per
conversation on it, and DELETES the table, sessions (cascades to messages, llm_calls, tool_calls,
actions) and any waiter requests afterwards, so analytics never sees smoke traffic. Everything the
report needs is captured in per_turn.jsonl first. Actions are never accepted, so no waiter request
is ever created (smoke D therefore has no escalations by construction).

Outputs: eval/results/phase5/<run>/{run.json, per_turn.jsonl, summary.json, summary.md}.
Dataset rows: {"id", "category", "turns": [{"user", "expect": {...}}]}; see CHECKS below.
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "api"
sys.path.insert(0, str(API))

SECRET_ENV = "DEEPSEEK_API_KEY"


def load_env_key() -> bool:
    """Put DEEPSEEK_API_KEY from the repo .env into os.environ, overriding any stale exported one.
    Returns True when a non-empty key is set. Never prints it."""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith(SECRET_ENV + "="):
                os.environ[SECRET_ENV] = line.split("=", 1)[1].strip().strip("\"'")
    return bool(os.environ.get(SECRET_ENV))


# --- checks --------------------------------------------------------------------------------------

CHECKS = (
    "no_error",  # implicit on every turn: outcome != error
    "tools_any",  # at least one of these tools was called (model-visible tools; respond excluded)
    "tools_none",  # none of these tools was called
    "tool_args",  # [{tool, args}]: some call of `tool` has args that are a superset (lists: element superset)
    "status_in",
    "outcome_in",
    "must_cite_items",  # item names that must appear in citations
    "must_cite_any_items",  # at least one of these names appears in citations
    "must_not_cite_items",  # names that must not appear in citations, allergen notice or answer text
    "must_cite_passage",  # at least one passage citation
    "text_must_match",  # every regex must match the answer text
    "text_must_not_match",  # no regex may match
    "actions",  # exact multiset of action types
    "action_item",  # an add_to_cart action for this item name
    "action_qty",
    "action_item_from_turn",  # add_to_cart item is among the items cited in conversation turn N (0-based)
    "action_conflict",  # add_to_cart action's allergen_conflict is a superset
    "allergen_notice",  # True/False: presence of the DB-built notice
)


def args_superset(have: dict, want: dict) -> bool:
    for k, v in want.items():
        h = have.get(k)
        if isinstance(v, list):
            if not isinstance(h, list) or not set(map(str, v)) <= set(map(str, h)):
                return False
        elif h != v:
            return False
    return True


def evaluate(
    expect: dict, obs: dict, item_ids: dict[str, int], prior_cited: list[set[int]]
) -> dict[str, dict]:
    """Returns {check: {"pass": bool, "detail": str}} for the implicit check and every key in expect."""
    out: dict[str, dict] = {}

    def put(name: str, ok: bool, detail: str = "") -> None:
        out[name] = {"pass": bool(ok), "detail": "" if ok else detail}

    put(
        "no_error",
        obs["outcome"] != "error",
        f"outcome={obs['outcome']} error_code={obs.get('error_code')}",
    )
    tools = set(obs["tools"])
    cited = set(obs["cited_item_ids"])
    shown = cited | set(obs["notice_item_ids"])
    text = obs["text"]
    nid = lambda names: [item_ids.get(n, -1) for n in names]  # noqa: E731
    adds = [a for a in obs["actions"] if a.get("type") == "add_to_cart"]
    for key, want in expect.items():
        if key == "tools_any":
            put(key, tools & set(want), f"called={sorted(tools)}")
        elif key == "tools_none":
            put(key, not tools & set(want), f"called={sorted(tools & set(want))}")
        elif key == "tool_args":
            ok = all(
                any(
                    c["name"] == w["tool"] and args_superset(c["arguments"] or {}, w["args"])
                    for c in obs["tool_calls"]
                )
                for w in want
            )
            put(key, ok, f"calls={[(c['name'], c['arguments']) for c in obs['tool_calls']]}")
        elif key == "status_in":
            put(key, obs["status"] in want, f"status={obs['status']}")
        elif key == "outcome_in":
            put(key, obs["outcome"] in want, f"outcome={obs['outcome']}")
        elif key == "must_cite_items":
            put(key, set(nid(want)) <= cited, f"cited={sorted(obs['cited_names'])}")
        elif key == "must_cite_any_items":
            put(key, bool(set(nid(want)) & cited), f"cited={sorted(obs['cited_names'])}")
        elif key == "must_not_cite_items":
            low = text.lower()
            bad = [n for n in want if item_ids.get(n, -1) in shown or n.lower() in low]
            put(key, not bad, f"mentioned={bad}")
        elif key == "must_cite_passage":
            put(key, obs["n_passage_citations"] > 0, "no passage citation")
        elif key == "text_must_match":
            miss = [p for p in want if not re.search(p, text)]
            put(key, not miss, f"unmatched={miss}")
        elif key == "text_must_not_match":
            hit = [p for p in want if re.search(p, text)]
            put(key, not hit, f"matched={[m.group(0) for p in hit if (m := re.search(p, text))]}")
        elif key == "actions":
            put(
                key,
                sorted(a.get("type") for a in obs["actions"]) == sorted(want),
                f"actions={[a.get('type') for a in obs['actions']]}",
            )
        elif key == "action_item":
            put(
                key,
                any(a["menu_item_id"] == item_ids.get(want, -1) for a in adds),
                f"added={[a.get('name') for a in adds]}",
            )
        elif key == "action_qty":
            put(key, any(a["qty"] == want for a in adds), f"qty={[a.get('qty') for a in adds]}")
        elif key == "action_item_from_turn":
            pool = prior_cited[want] if want < len(prior_cited) else set()
            put(
                key,
                any(a["menu_item_id"] in pool for a in adds),
                f"added={[a.get('name') for a in adds]} pool_size={len(pool)}",
            )
        elif key == "action_conflict":
            put(
                key,
                any(set(want) <= set(a.get("allergen_conflict") or []) for a in adds),
                f"conflicts={[a.get('allergen_conflict') for a in adds]}",
            )
        elif key == "allergen_notice":
            put(
                key,
                bool(obs["allergen_notice"]) == want,
                f"notice={'present' if obs['allergen_notice'] else 'absent'}",
            )
        else:
            raise ValueError(f"unknown check {key!r}")
    return out


# --- LLM wrappers ---------------------------------------------------------------------------------


class BudgetExceeded(Exception):
    pass


class MeteredLLM:
    """Wraps any LLMClient: counts calls and cost, measures wall time, and aborts BEFORE a call that
    would exceed the caps."""

    def __init__(self, inner, max_calls: int, max_cost_usd: float):
        self.inner, self.max_calls, self.max_cost = inner, max_calls, max_cost_usd
        self.calls = 0
        self.cost_micros = 0
        self.turn_wall_ms = 0.0
        self.abort_reason: str | None = (
            None  # the orchestrator swallows exceptions, so also flag it here
        )

    async def complete(self, messages, **kw):
        if self.calls >= self.max_calls:
            self.abort_reason = f"max-llm-calls {self.max_calls} reached"
            raise BudgetExceeded(self.abort_reason)
        if self.cost_micros / 1e6 >= self.max_cost:
            self.abort_reason = f"max-cost-usd {self.max_cost} reached"
            raise BudgetExceeded(self.abort_reason)
        self.calls += 1
        t0 = time.perf_counter()
        try:
            res = await self.inner.complete(messages, **kw)
        finally:
            self.turn_wall_ms += (time.perf_counter() - t0) * 1000
        self.cost_micros += getattr(res, "cost_usd_micros", 0) or 0
        return res

    async def aclose(self):
        await self.inner.aclose()


def make_dry_llm():
    """Heuristic stand-in for DeepSeek: search with the diner's words, then answer from the first
    returned item (or not_found). No network, no cost beyond the synthetic usage."""
    from tests.chat_helpers import reply, respond, tc

    class HeuristicLLM:
        async def aclose(self):
            pass

        async def complete(self, messages, **kw):
            last = messages[-1]
            if last["role"] == "tool":
                payload = json.loads(last["content"])
                items = payload.get("items") or []
                if items:
                    top = items[0]
                    return reply(
                        respond(
                            f"We have {top['name']} at {top['price']}. The kitchen cannot rule out cross-contact.",
                            items=[top["id"]],
                        )
                    )
                return reply(
                    respond("I don't have that information. Tap Call a waiter.", "not_found")
                )
            user = [m for m in messages if m["role"] == "user"][-1]["content"]
            return reply(tc("search_menu", query=user[:80]))

    return HeuristicLLM()


# --- stats ----------------------------------------------------------------------------------------


def pct(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def git_info() -> dict:
    def run(*a):
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()

    return {
        "sha": run("rev-parse", "HEAD"),
        "dirty": bool(run("status", "--porcelain", "--", "api", "eval")),
    }


# --- run ------------------------------------------------------------------------------------------


async def run(args) -> int:
    from sqlalchemy import text

    from app.chat import analytics_core as core
    from app.chat import service
    from app.chat.guardrails import ItemFact  # noqa: F401  (import check only)
    from app.chat.orchestrator import ChatDeps
    from app.chat.service import session_config
    from app.chat.settings import ChatSettings, get_chat_settings
    from app.db import SessionLocal

    started = datetime.now(UTC)
    run_id = args.label or started.strftime("%Y%m%dT%H%M%SZ")
    run_id = ("dry_" if args.dry_run else "") + run_id
    out_dir = Path(args.out_root) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    dataset_path = Path(args.dataset)
    rows = [json.loads(line) for line in dataset_path.read_text().splitlines() if line.strip()]
    if args.only:
        rows = [r for r in rows if r["id"] in args.only]

    if args.dry_run:
        settings = ChatSettings(abstain_signal=False)
        metered = MeteredLLM(make_dry_llm(), args.max_llm_calls, args.max_cost_usd)
        deps = ChatDeps(llm=metered, settings=settings, sessionmaker=SessionLocal)
    else:
        if not load_env_key():
            print("error: DEEPSEEK_API_KEY missing from repo .env", file=sys.stderr)
            return 1
        get_chat_settings.cache_clear()
        deps = service.get_chat_deps()
        metered = MeteredLLM(deps.llm, args.max_llm_calls, args.max_cost_usd)
        deps.llm = metered
        settings = deps.settings

    async with SessionLocal() as db:
        item_ids = {
            r.name: r.id for r in (await db.execute(text("SELECT id, name FROM menu_items"))).all()
        }
        label = f"SMOKE-{run_id}"[:40]
        token = uuid.uuid4().hex
        await db.execute(
            text("INSERT INTO dining_tables (label, qr_token) VALUES (:l, :t)"),
            {"l": label, "t": token},
        )
        await db.commit()

    per_turn: list[dict] = []
    sessions: list[str] = []
    aborted: str | None = None
    prompt_version = ""
    cfg_hash = ""
    try:
        for conv in rows:
            async with SessionLocal() as db:
                created = await service.create_session(db, deps, token)
            sid = created["session_id"]
            sessions.append(sid)
            if not cfg_hash:
                cfg_hash = session_config(deps)["config_hash"]
            prior_cited: list[set[int]] = []
            for ti, turn in enumerate(conv["turns"]):
                metered.turn_wall_ms = 0.0
                t0 = time.perf_counter()
                try:
                    async with SessionLocal() as db:
                        begun = await service.begin_turn(
                            db, deps, uuid.UUID(sid), turn["user"], uuid.uuid4().hex
                        )

                    async def emit(event, data):
                        return None

                    result, message = await service.execute_turn(
                        deps, begun, f"smoke-{run_id}", emit
                    )
                except BudgetExceeded as exc:
                    aborted = str(exc)
                    break
                except Exception as exc:  # noqa: BLE001
                    if isinstance(exc.__cause__, BudgetExceeded) or "BudgetExceeded" in repr(exc):
                        aborted = str(exc)
                        break
                    raise
                if metered.abort_reason:
                    aborted = metered.abort_reason
                    break
                wall_ms = (time.perf_counter() - t0) * 1000
                prompt_version = result.prompt_version or prompt_version
                async with SessionLocal() as db:
                    llm_rows = (
                        await db.execute(
                            text(
                                "SELECT call_index, attempt, prompt_tokens, cache_hit_tokens, cache_miss_tokens, completion_tokens, "
                                "cost_usd_micros, price_period, latency_ms, status FROM llm_calls WHERE turn_id=:t ORDER BY id"
                            ),
                            {"t": begun.turn_id},
                        )
                    ).all()
                    tool_rows = (
                        await db.execute(
                            text(
                                "SELECT name, arguments, status, latency_ms FROM tool_calls WHERE turn_id=:t ORDER BY id"
                            ),
                            {"t": begun.turn_id},
                        )
                    ).all()
                cites = message["citations"]
                cited_ids = [c["menu_item_id"] for c in cites if c["kind"] == "menu_item"]
                notice = message["allergen_notice"]
                obs = {
                    "text": message["text"],
                    "status": message["status"],
                    "outcome": result.outcome,
                    "error_code": result.error_code,
                    "tools": [r.name for r in tool_rows],
                    "tool_calls": [
                        {
                            "name": r.name,
                            "arguments": r.arguments,
                            "status": r.status,
                            "latency_ms": r.latency_ms,
                        }
                        for r in tool_rows
                    ],
                    "cited_item_ids": cited_ids,
                    "cited_names": [c["name"] for c in cites if c["kind"] == "menu_item"],
                    "n_passage_citations": sum(c["kind"] == "passage" for c in cites),
                    "notice_item_ids": [i["menu_item_id"] for i in (notice or {}).get("items", [])],
                    "allergen_notice": notice,
                    "actions": message["actions"],
                }
                checks = evaluate(turn.get("expect", {}), obs, item_ids, prior_cited)
                prior_cited.append(set(cited_ids))
                llm_ms = sum(r.latency_ms or 0 for r in llm_rows)
                tool_ms = sum(r.latency_ms or 0 for r in tool_rows)
                retrieval_ms = sum(
                    r.latency_ms or 0
                    for r in tool_rows
                    if r.name in ("search_menu", "search_info", "get_item")
                )
                per_turn.append(
                    {
                        "conversation": conv["id"],
                        "category": conv["category"],
                        "turn": ti,
                        "turn_index_in_session": begun.turn_index,
                        "user": turn["user"],
                        "answer": message["text"],
                        "status": message["status"],
                        "outcome": result.outcome,
                        "guardrails": result.guardrails,
                        "tools": obs["tools"],
                        "tool_calls": obs["tool_calls"],
                        "cited_names": obs["cited_names"],
                        "n_passage_citations": obs["n_passage_citations"],
                        "actions": [
                            {
                                k: a.get(k)
                                for k in ("type", "name", "qty", "allergen_conflict", "reason")
                            }
                            for a in obs["actions"]
                        ],
                        "allergen_notice": bool(notice),
                        "llm_calls": len(llm_rows),
                        "prompt_tokens": sum(r.prompt_tokens for r in llm_rows),
                        "cache_hit_tokens": sum(r.cache_hit_tokens for r in llm_rows),
                        "cache_miss_tokens": sum(r.cache_miss_tokens for r in llm_rows),
                        "completion_tokens": sum(r.completion_tokens for r in llm_rows),
                        "cost_usd_micros": sum(r.cost_usd_micros for r in llm_rows),
                        "price_periods": sorted(
                            {r.price_period for r in llm_rows if r.price_period}
                        ),
                        "llm_per_call": [
                            {
                                "call_index": r.call_index,
                                "prompt": r.prompt_tokens,
                                "hit": r.cache_hit_tokens,
                                "miss": r.cache_miss_tokens,
                                "latency_ms": r.latency_ms,
                            }
                            for r in llm_rows
                        ],
                        "latency_ms": result.latency_ms,
                        "wall_ms": round(wall_ms, 1),
                        "llm_ms": llm_ms,
                        "tool_ms": tool_ms,
                        "retrieval_ms": retrieval_ms,
                        "other_ms": max(0.0, round(wall_ms - llm_ms - tool_ms, 1)),
                        "created_at": datetime.now(UTC),
                        "session_id": sid,
                        "checks": checks,
                        "pass": all(c["pass"] for c in checks.values()),
                    }
                )
                mark = "PASS" if per_turn[-1]["pass"] else "FAIL"
                print(
                    f"[{mark}] {conv['id']}#{ti} outcome={result.outcome} calls={len(llm_rows)} {wall_ms:.0f}ms"
                )
            if aborted:
                break
    finally:
        async with SessionLocal() as db:  # cleanup: nothing from the smoke run stays in the DB
            if sessions:
                sids = [uuid.UUID(s) for s in sessions]
                await db.execute(
                    text("DELETE FROM waiter_requests WHERE session_id = ANY(:s)"), {"s": sids}
                )
                await db.execute(text("DELETE FROM chat_sessions WHERE id = ANY(:s)"), {"s": sids})
            await db.execute(
                text(
                    "DELETE FROM waiter_requests WHERE table_id IN (SELECT id FROM dining_tables WHERE label=:l)"
                ),
                {"l": label},
            )
            await db.execute(text("DELETE FROM dining_tables WHERE label=:l"), {"l": label})
            await db.commit()
        await metered.aclose()

    # --- aggregate -------------------------------------------------------------------------------
    by_check: dict[str, list[bool]] = defaultdict(list)
    by_cat_turns: dict[str, list[bool]] = defaultdict(list)
    conv_pass: dict[str, list[bool]] = defaultdict(list)
    cat_of: dict[str, str] = {}
    for t in per_turn:
        for name, c in t["checks"].items():
            by_check[name].append(c["pass"])
        by_cat_turns[t["category"]].append(t["pass"])
        conv_pass[t["conversation"]].append(t["pass"])
        cat_of[t["conversation"]] = t["category"]
    by_cat_conv: dict[str, list[bool]] = defaultdict(list)
    for cid, ps in conv_pass.items():
        by_cat_conv[cat_of[cid]].append(all(ps))

    def rate(v: list[bool]) -> dict:
        return {"passed": sum(v), "n": len(v), "rate": ratio(sum(v), len(v))}

    hit = sum(t["cache_hit_tokens"] for t in per_turn)
    prompt = sum(t["prompt_tokens"] for t in per_turn)
    calls_all = [c for t in per_turn for c in t["llm_per_call"]]
    calls2 = [c for c in calls_all if c["call_index"] >= 2]
    turns2 = [t for t in per_turn if t["turn"] >= 1]
    hit2 = sum(c["hit"] for c in calls2)
    p2 = sum(c["prompt"] for c in calls2)
    hitT = sum(t["cache_hit_tokens"] for t in turns2)
    pT = sum(t["prompt_tokens"] for t in turns2)
    cost = sum(t["cost_usd_micros"] for t in per_turn) / 1e6
    n_turns, n_convs = len(per_turn), len(conv_pass)

    def lat(key: str) -> dict:
        v = [t[key] for t in per_turn]
        return {
            "p50": round(pct(v, 0.5), 1) if v else None,
            "p95": round(pct(v, 0.95), 1) if v else None,
            "mean": round(statistics.fmean(v), 1) if v else None,
        }

    turn_rows = [
        core.TurnRow(t["session_id"], t["outcome"], t["created_at"], None) for t in per_turn
    ]
    deflected, in_scope = core.turn_deflection(turn_rows, [])
    outcomes: dict[str, int] = defaultdict(int)
    for t in per_turn:
        outcomes[t["outcome"]] += 1
    gr: dict[str, int] = defaultdict(int)
    for t in per_turn:
        for g in t["guardrails"]:
            gr[g.get("check", "?") if isinstance(g, dict) else str(g)] += 1

    summary = {
        "run_id": run_id,
        "dry_run": args.dry_run,
        "aborted": aborted,
        "conversations": n_convs,
        "turns": n_turns,
        "turn_pass": rate([t["pass"] for t in per_turn]),
        "conversation_pass": rate([all(v) for v in conv_pass.values()]),
        "pass_by_check": {k: rate(v) for k, v in sorted(by_check.items())},
        "pass_by_category_conversations": {k: rate(v) for k, v in sorted(by_cat_conv.items())},
        "pass_by_category_turns": {k: rate(v) for k, v in sorted(by_cat_turns.items())},
        "tokens": {
            "prompt": prompt,
            "cache_hit": hit,
            "cache_miss": sum(t["cache_miss_tokens"] for t in per_turn),
            "completion": sum(t["completion_tokens"] for t in per_turn),
            "llm_calls": len(calls_all),
        },
        "cache_hit_ratio": {
            "all_calls": ratio(hit, prompt),
            "calls_index_ge2_within_turn": ratio(hit2, p2),
            "turns_index_ge2_within_conversation": ratio(hitT, pT),
        },
        "cost_usd": {
            "total": round(cost, 6),
            "per_turn": round(cost / n_turns, 6) if n_turns else None,
            "per_conversation": round(cost / n_convs, 6) if n_convs else None,
            "per_llm_call": round(cost / len(calls_all), 6) if calls_all else None,
            "price_periods": sorted({p for t in per_turn for p in t["price_periods"]}),
        },
        "latency_ms": {
            "turn_total": lat("latency_ms"),
            "llm": lat("llm_ms"),
            "retrieval_tools": lat("retrieval_ms"),
            "other": lat("other_ms"),
        },
        "llm_calls_per_turn": {
            "mean": round(len(calls_all) / n_turns, 2) if n_turns else None,
            "max": max((t["llm_calls"] for t in per_turn), default=0),
        },
        "turn_outcomes": dict(sorted(outcomes.items())),
        "guardrail_triggers": dict(sorted(gr.items())),
        "smoke_deflection_D": {
            **core.proportion(deflected, in_scope),
            "label": f"smoke, n={in_scope} in-scope turns ({n_convs} conversations); no escalations by construction",
        },
    }

    run_json = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "git": git_info(),
        "config_hash": cfg_hash,
        "prompt_version": prompt_version,
        "model": settings.model,
        "thinking": settings.thinking,
        "price_periods": summary["cost_usd"]["price_periods"],
        "dry_run": args.dry_run,
        "caps": {"max_llm_calls": args.max_llm_calls, "max_cost_usd": args.max_cost_usd},
        "dataset": {
            "path": str(dataset_path),
            "sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
            "conversations": len(rows),
        },
        "settings": {k: v for k, v in settings.model_dump().items() if k != "deepseek_api_key"},
        "cleanup": "dedicated table + sessions + waiter requests deleted after the run",
        "aborted": aborted,
    }
    (out_dir / "run.json").write_text(json.dumps(run_json, indent=2, default=str))
    with open(out_dir / "per_turn.jsonl", "w") as f:
        for t in per_turn:
            f.write(json.dumps(t, ensure_ascii=False, default=str) + "\n")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    (out_dir / "summary.md").write_text(render_md(summary, run_json, per_turn))
    print(
        f"wrote {out_dir}  llm_calls={metered.calls} cost=${cost:.4f}"
        + (f"  ABORTED: {aborted}" if aborted else "")
    )
    return 2 if aborted else 0


def render_md(s: dict, rj: dict, turns: list[dict]) -> str:
    p = lambda d: f"{d['passed']}/{d['n']} ({d['rate']:.0%})" if d["n"] else "n/a"  # noqa: E731
    L = [f"# Chat smoke eval: {s['run_id']}", ""]
    L.append(
        f"git `{rj['git']['sha'][:10]}`{' (dirty)' if rj['git']['dirty'] else ''} | model `{rj['model']}` | prompt_version `{rj['prompt_version']}` | config_hash `{rj['config_hash']}` | price period {', '.join(rj['price_periods']) or 'n/a'} | started {rj['started_at']}"
    )
    if s["dry_run"]:
        L.append("\n**DRY RUN with a heuristic fake LLM; numbers are not model results.**")
    if s["aborted"]:
        L.append(f"\n**ABORTED: {s['aborted']}**")
    L += [
        "",
        f"Smoke test, not a benchmark: {s['conversations']} conversations / {s['turns']} turns, written by the developer. Quality claims wait for Phase 7.",
        "",
    ]
    L += [
        f"Turn pass: {p(s['turn_pass'])} | conversation pass: {p(s['conversation_pass'])}",
        "",
        "## Pass rate by category (conversations)",
        "",
        "| category | passed |",
        "|---|---|",
    ]
    L += [f"| {k} | {p(v)} |" for k, v in s["pass_by_category_conversations"].items()]
    L += ["", "## Pass rate by check", "", "| check | passed |", "|---|---|"]
    L += [f"| {k} | {p(v)} |" for k, v in s["pass_by_check"].items()]
    t = s["tokens"]
    c = s["cache_hit_ratio"]
    f = lambda x: "n/a" if x is None else f"{x:.1%}"  # noqa: E731
    L += [
        "",
        "## Tokens and cache",
        "",
        f"- LLM calls {t['llm_calls']} (mean {s['llm_calls_per_turn']['mean']}/turn, max {s['llm_calls_per_turn']['max']}); prompt {t['prompt']} (hit {t['cache_hit']}, miss {t['cache_miss']}); completion {t['completion']}",
    ]
    L += [
        f"- Cache-hit ratio (prompt tokens): all calls {f(c['all_calls'])}; calls 2+ within a turn {f(c['calls_index_ge2_within_turn'])}; turns 2+ within a conversation {f(c['turns_index_ge2_within_conversation'])}"
    ]
    co = s["cost_usd"]
    L += [
        "",
        "## Cost",
        "",
        f"- Total ${co['total']:.4f}; per turn ${co['per_turn']}; per conversation ${co['per_conversation']}; per LLM call ${co['per_llm_call']} ({', '.join(co['price_periods'])})",
    ]
    L += ["", "## Latency (ms per turn)", "", "| part | p50 | p95 | mean |", "|---|---|---|---|"]
    L += [f"| {k} | {v['p50']} | {v['p95']} | {v['mean']} |" for k, v in s["latency_ms"].items()]
    L += [
        "",
        f"Outcomes: {s['turn_outcomes']}",
        f"Guardrail triggers: {s['guardrail_triggers'] or 'none'}",
    ]
    d = s["smoke_deflection_D"]
    ci = d["ci95"]
    L += [
        "",
        "## Smoke deflection D",
        "",
        f"D = {d['numerator']}/{d['denominator']}"
        + (f" = {d['rate']:.1%}, Wilson 95% CI [{ci[0]:.1%}, {ci[1]:.1%}]" if ci else "")
        + f" ({d['label']})",
    ]
    fails = [x for x in turns if not x["pass"]]
    L += ["", f"## Failures ({len(fails)})", ""]
    for x in fails:
        bad = "; ".join(f"{k}: {v['detail']}" for k, v in x["checks"].items() if not v["pass"])
        L.append(
            f"- `{x['conversation']}#{x['turn']}` outcome={x['outcome']} | {bad}\n  - user: {x['user']}\n  - bot: {x['answer'][:300]}"
        )
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset", default=str(ROOT / "eval/datasets/chat_smoke_v1.jsonl"))
    ap.add_argument("--out-root", default=str(ROOT / "eval/results/phase5"))
    ap.add_argument("--label", default="")
    ap.add_argument(
        "--dry-run", action="store_true", help="heuristic FakeLLM, no network, no spend"
    )
    ap.add_argument("--max-llm-calls", type=int, default=100)
    ap.add_argument("--max-cost-usd", type=float, default=0.25)
    ap.add_argument("--only", nargs="*", help="run only these conversation ids")
    args = ap.parse_args()
    os.environ.setdefault("LOG_LEVEL", "WARNING")
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
