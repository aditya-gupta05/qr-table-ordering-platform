"""Semantic answer cache (plan decision 14): CHAT_ANSWER_CACHE = off | shadow | on.

Off by default. A turn costs ~$0.001, so the only real gain is latency, and the risk is a false hit
on a paraphrase that flips the meaning ("do you take reservations" vs "do you NOT take..."). So:

* Eligibility is narrow: first turn of a session, no allergen / diet / action cue, no declared
  allergens; and only answers that came from `search_info` alone, status answered, no guardrail
  violation, no actions, are stored.
* The key is versioned: prompt_version + model + corpus hash + menu hash. Any change to the prompt,
  tools, documents or menu makes old rows unreachable (a miss), never wrong.
* Lookup is an exact scan (cosine >= threshold, newest first on ties) over rows younger than 24 h.
* `shadow` runs the full pipeline, records what the cache WOULD have served next to the fresh
  answer in answer_cache_shadow, and never serves it. Phase 7 reads those pairs to measure the
  false-hit rate before anyone turns `on`.
"""

import json
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.allergen_terms import extract_declaration, mentions_allergen_topic
from app.retrieval.embedder import to_pgvector

TTL = timedelta(hours=24)

_ACTION_CUE = re.compile(
    r"\b(?:add|order|ordering|cart|basket|waiter|waitress|server|staff|manager|bill|check please|"
    r"book|reserve me|call|bring|get me|i(?:'|’)ll have|i would like|i want)\b",
    re.I,
)


@dataclass(frozen=True)
class Versions:
    prompt_version: str
    model: str
    corpus_version: str
    menu_version: str


@dataclass(frozen=True)
class Candidate:
    cache_id: int
    similarity: float
    message: dict


def normalize_question(question: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace."""
    return " ".join(re.sub(r"[^\w\s']", " ", question.lower()).split())


def question_eligible(question: str, *, is_first_turn: bool, declared: tuple[str, ...]) -> bool:
    """May this question be answered from / stored in the cache at all?"""
    if not is_first_turn or declared:
        return False
    if mentions_allergen_topic(question) or extract_declaration(question).cue:
        return False
    return not _ACTION_CUE.search(question)


def answer_storable(
    *,
    tool_names: list[str],
    status: str,
    outcome: str,
    violations: int,
    actions: int,
    allergen_sensitive: bool,
) -> bool:
    """May this fresh answer be stored? Only pure `search_info` answers that passed every check."""
    return (
        bool(tool_names)
        and set(tool_names) == {"search_info"}
        and status == "answered"
        and outcome == "answered"
        and violations == 0
        and actions == 0
        and not allergen_sensitive
    )


async def current_versions(db: AsyncSession, prompt_version: str, model: str) -> Versions:
    row = (
        await db.execute(
            text(
                "SELECT "
                "(SELECT coalesce(md5(string_agg(slug || ':' || content_hash, ';' "
                "ORDER BY slug)), '') "
                " FROM documents), "
                "(SELECT coalesce(md5(string_agg(concat_ws('|', id, name, price_cents, "
                " is_available, allergens::text, dietary_tags::text), ';' ORDER BY id)), '') "
                " FROM menu_items)"
            )
        )
    ).one()
    return Versions(prompt_version, model, row[0], row[1])


_VER = (
    "prompt_version = :pv AND model = :model AND corpus_version = :cv AND menu_version = :mv "
    "AND created_at > now() - make_interval(secs => :ttl)"
)


def _params(v: Versions) -> dict:
    return {
        "pv": v.prompt_version,
        "model": v.model,
        "cv": v.corpus_version,
        "mv": v.menu_version,
        "ttl": TTL.total_seconds(),
    }


async def lookup(
    db: AsyncSession, embedding: list[float], versions: Versions, threshold: float
) -> Candidate | None:
    """Best cached answer for the same versions with cosine similarity >= threshold."""
    row = (
        await db.execute(
            text(
                "SELECT id, message, 1 - (embedding <=> CAST(:v AS vector)) AS sim "
                f"FROM answer_cache WHERE {_VER} "
                "ORDER BY embedding <=> CAST(:v AS vector), id DESC LIMIT 1"
            ),
            {"v": to_pgvector(embedding), **_params(versions)},
        )
    ).first()
    if row is None or row.sim < threshold:
        return None
    return Candidate(row.id, float(row.sim), row.message)


async def store(
    db: AsyncSession, question: str, embedding: list[float], message: dict, versions: Versions
) -> int:
    row = (
        await db.execute(
            text(
                "INSERT INTO answer_cache (question_norm, embedding, message, prompt_version, "
                "model, corpus_version, menu_version) VALUES (:q, CAST(:v AS vector), "
                "CAST(:m AS jsonb), :pv, :model, :cv, :mv) RETURNING id"
            ),
            {
                "q": normalize_question(question),
                "v": to_pgvector(embedding),
                "m": json.dumps(message, ensure_ascii=False),
                **{k: v for k, v in _params(versions).items() if k != "ttl"},
            },
        )
    ).one()
    return row.id


async def bump_hits(db: AsyncSession, cache_id: int) -> None:
    await db.execute(text("UPDATE answer_cache SET hits = hits + 1 WHERE id = :i"), {"i": cache_id})


async def record_shadow(
    db: AsyncSession,
    turn_id: uuid.UUID,
    candidate: Candidate,
    fresh_message_id: int | None,
) -> None:
    await db.execute(
        text(
            "INSERT INTO answer_cache_shadow (turn_id, cache_id, similarity, fresh_message_id) "
            "VALUES (:t, :c, :s, :f) ON CONFLICT (turn_id) DO NOTHING"
        ),
        {"t": turn_id, "c": candidate.cache_id, "s": candidate.similarity, "f": fresh_message_id},
    )
