"""Conversation history for the LLM (plan decision 12).

The DB is the source of truth. Only user/assistant text is replayed: tool calls and tool results of
earlier turns are never resent (they would bloat the prompt and are re-derivable by calling tools
again). Prior assistant turns are rendered `answer + " (items: 12, 31)"` so the model keeps the ids
it needs for follow-ups ("add the second one") without seeing raw tool output.

Block truncation keeps the cacheable prefix stable: a plain sliding window changes the first history
message on every turn once the session is long, which defeats prefix caching. Instead the window
start only moves when the window overflows (more than `max_pairs` pairs or ~3k tokens), and then it
jumps forward to keep just the last `keep` pairs. The start index is a pure function of the pair
sizes, so it is reproducible and stays put for the next several turns.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatMessage

TOKEN_CAP = 3000
KEEP_PAIRS = 3


@dataclass(frozen=True)
class HistoryPair:
    turn_index: int
    user: str
    assistant: str  # raw answer text
    item_ids: tuple[int, ...] = field(default=())

    @property
    def assistant_rendered(self) -> str:
        if not self.item_ids:
            return self.assistant
        return f"{self.assistant} (items: {', '.join(str(i) for i in self.item_ids)})"

    @property
    def tokens(self) -> int:
        """Cheap estimate (4 chars per token); only used to decide when to truncate."""
        return (len(self.user) + len(self.assistant_rendered)) // 4 + 8


async def load_pairs(
    session: AsyncSession, session_id: uuid.UUID, before_turn_index: int
) -> list[HistoryPair]:
    """Completed user/assistant pairs of earlier turns, oldest first. A turn without an assistant
    row (in flight, crashed or cancelled) is skipped."""
    rows = (
        await session.execute(
            select(
                ChatMessage.turn_index,
                ChatMessage.role,
                ChatMessage.content,
                ChatMessage.content_llm,
                ChatMessage.citations,
            )
            .where(ChatMessage.session_id == session_id, ChatMessage.turn_index < before_turn_index)
            .order_by(ChatMessage.turn_index, ChatMessage.role.desc())  # user before assistant
        )
    ).all()
    by_turn: dict[int, dict] = {}
    for idx, role, content, content_llm, citations in rows:
        slot = by_turn.setdefault(idx, {})
        if role == "user":
            slot["user"] = content_llm or content
        else:
            slot["assistant"] = content
            slot["items"] = tuple(
                c["menu_item_id"]
                for c in (citations or [])
                if isinstance(c, dict) and c.get("kind") == "menu_item"
            )
    return [
        HistoryPair(idx, s["user"], s["assistant"], s.get("items", ()))
        for idx, s in sorted(by_turn.items())
        if "user" in s and "assistant" in s
    ]


def window_start(
    sizes: list[int], max_pairs: int, token_cap: int = TOKEN_CAP, keep: int = KEEP_PAIRS
):
    """Index of the first pair to keep. Replays the conversation pair by pair: whenever the window
    exceeds `max_pairs` pairs or `token_cap` tokens it is cut down to the last `keep` pairs."""
    keep = max(1, min(keep, max_pairs))
    start = 0
    for k in range(1, len(sizes) + 1):
        if k - start > max_pairs or sum(sizes[start:k]) > token_cap:
            start = max(k - keep, start)
    return start


def select_window(
    pairs: list[HistoryPair], max_pairs: int, token_cap: int = TOKEN_CAP, keep: int = KEEP_PAIRS
) -> list[HistoryPair]:
    if max_pairs <= 0:
        return []
    return pairs[window_start([p.tokens for p in pairs], max_pairs, token_cap, keep) :]


def to_messages(window: list[HistoryPair]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for p in window:
        out.append({"role": "user", "content": p.user})
        out.append({"role": "assistant", "content": p.assistant_rendered})
    return out
