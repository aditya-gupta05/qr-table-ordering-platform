"""Chunkers: fixed_token, recursive_md, sentence. Pure functions over text + a token counter.

All strategies work on *units* (pre-tokenised words with their WordPiece token counts), so a chunk
boundary never falls inside a word and `token_count` of the chunk text equals the sum of its
units. Offsets (start_char/end_char) index into the cleaned document text and are what the eval
labels resolve against, so they stay valid for every chunk configuration.

Budget: a chunk's *content* may use at most `max_tokens - reserve` tokens, where `reserve` is the
longest heading path when `prepend_headings` is on (the heading is added to the text that is
embedded, and the embedder silently truncates beyond 512 tokens). Violations raise.
"""

import bisect
import re
from dataclasses import dataclass
from typing import Literal

from tokenizers import Tokenizer

Strategy = Literal["fixed_token", "recursive_md", "sentence"]
MIN_BUDGET = 16


class ChunkingError(ValueError):
    pass


@dataclass(frozen=True)
class ChunkSpec:
    strategy: Strategy
    max_tokens: int
    overlap_tokens: int = 0
    prepend_headings: bool = False


@dataclass(frozen=True)
class Unit:
    start: int
    end: int
    ntok: int


@dataclass(frozen=True)
class Chunk:
    index: int
    start: int
    end: int
    text: str
    token_count: int
    section_path: str


class TokenCounter:
    """Wraps a `tokenizers.Tokenizer` (no special tokens, no truncation)."""

    def __init__(self, tokenizer: Tokenizer):
        self.tok = tokenizer

    def count(self, text: str) -> int:
        return len(self.tok.encode(text, add_special_tokens=False).ids)

    def units(self, text: str, max_unit: int) -> list[Unit]:
        """Word-level units. A word longer than `max_unit` tokens is split into token units."""
        enc = self.tok.encode(text, add_special_tokens=False)
        out: list[Unit] = []
        group: list[tuple[int, int]] = []
        last_wid: int | None = None

        def flush():
            if not group:
                return
            if len(group) > max_unit:
                out.extend(Unit(s, e, 1) for s, e in group)
            else:
                out.append(Unit(group[0][0], group[-1][1], len(group)))
            group.clear()

        for wid, off in zip(enc.word_ids, enc.offsets, strict=True):
            if wid != last_wid:
                flush()
                last_wid = wid
            group.append(off)
        flush()
        return out


# --- structure helpers -------------------------------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.MULTILINE)


def heading_paths(text: str) -> tuple[list[int], list[str]]:
    """Positions of markdown headings and the `A > B > C` path in force from each one onward.
    The level-1 document title is left out of the path."""
    positions: list[int] = []
    paths: list[str] = []
    stack: list[tuple[int, str]] = []
    for m in _HEADING.finditer(text):
        level, title = len(m.group(1)), m.group(2)
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        positions.append(m.start())
        paths.append(" > ".join(t for lv, t in stack if lv >= 2))
    return positions, paths


def section_path_at(positions: list[int], paths: list[str], pos: int) -> str:
    i = bisect.bisect_right(positions, pos) - 1
    return paths[i] if i >= 0 else ""


def _is_heading_only(piece: str) -> bool:
    """True when every non-blank line is a heading (a chain like `## A` + `### B` counts)."""
    lines = [ln.strip() for ln in piece.splitlines() if ln.strip()]
    return bool(lines) and all(ln.startswith("#") for ln in lines)


def glue_headings(parts: list[tuple[int, int]], text: str) -> list[tuple[int, int]]:
    """Attach heading-only pieces to the piece after them so a heading is never stranded."""
    out: list[tuple[int, int]] = []
    carry: int | None = None
    for s, e in parts:
        if carry is not None:
            s, carry = carry, None
        if _is_heading_only(text[s:e]):
            carry = s
            continue
        out.append((s, e))
    if carry is not None:  # trailing heading with nothing after it
        if out:
            out[-1] = (out[-1][0], len(text))
        else:
            out.append((carry, len(text)))
    return out


def _split_at(text: str, s: int, e: int, boundaries: list[int]) -> list[tuple[int, int]]:
    cuts = [s] + [b for b in boundaries if s < b < e] + [e]
    return [(a, b) for a, b in zip(cuts, cuts[1:], strict=False) if b > a]


def split_headings(text: str, s: int, e: int) -> list[tuple[int, int]]:
    return glue_headings(
        _split_at(text, s, e, [m.start() for m in _HEADING.finditer(text, s, e)]), text
    )


_PARA = re.compile(r"\n[ \t]*\n+")


def split_paragraphs(text: str, s: int, e: int) -> list[tuple[int, int]]:
    parts = _split_at(text, s, e, [m.end() for m in _PARA.finditer(text, s, e)])
    return glue_headings(parts, text)


_SENT_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[A-Z0-9$#])")


def split_sentences(text: str, s: int, e: int) -> list[tuple[int, int]]:
    bounds = [m.end() for m in _SENT_END.finditer(text, s, e)]
    bounds += [m.start() for m in _HEADING.finditer(text, s, e)]  # a heading starts a sentence
    parts = _split_at(text, s, e, sorted(set(bounds)))
    return glue_headings(parts, text)


# --- shared pieces -----------------------------------------------------------------------------


def window_pack(sizes: list[int], budget: int, overlap: int) -> list[tuple[int, int]]:
    """Greedy sliding windows over items with token `sizes`: each window holds <= budget tokens and
    the next window starts with the last items of the previous one totalling <= overlap tokens
    (always advancing by >= 1 item). Returns [i, j) index pairs."""
    n = len(sizes)
    out: list[tuple[int, int]] = []
    i = 0
    while i < n:
        j, tot = i, 0
        while j < n and tot + sizes[j] <= budget:
            tot += sizes[j]
            j += 1
        if j == i:  # single oversize item: caller guarantees this cannot happen
            raise ChunkingError("item larger than budget")
        out.append((i, j))
        if j >= n:
            break
        k, ov = j, 0
        while k - 1 > i and ov + sizes[k - 1] <= overlap:
            k -= 1
            ov += sizes[k]
        while k < j and ov + sizes[j] > budget:  # leave room for the first new item
            ov -= sizes[k]
            k += 1
        i = k
    return out


class _Doc:
    """Text + units + prefix sums, so any char span can be priced in O(log n)."""

    def __init__(self, text: str, counter: TokenCounter, budget: int):
        self.text = text
        self.counter = counter
        self.units = counter.units(text, budget)
        self.starts = [u.start for u in self.units]
        self.ends = [u.end for u in self.units]
        self.prefix = [0]
        for u in self.units:
            self.prefix.append(self.prefix[-1] + u.ntok)

    def tokens(self, s: int, e: int) -> int:
        lo = bisect.bisect_left(self.starts, s)
        hi = bisect.bisect_right(self.ends, e)
        return self.prefix[hi] - self.prefix[lo] if hi > lo else 0

    def unit_range(self, s: int, e: int) -> tuple[int, int]:
        return bisect.bisect_left(self.starts, s), bisect.bisect_right(self.ends, e)


def _hard_cut(d: _Doc, s: int, e: int, budget: int) -> list[tuple[int, int]]:
    lo, hi = d.unit_range(s, e)
    if hi <= lo:
        return [(s, e)]
    sizes = [u.ntok for u in d.units[lo:hi]]
    wins = window_pack(sizes, budget, 0)
    return [(d.units[lo + i].start, d.units[lo + j - 1].end) for i, j in wins]


def _merge(d: _Doc, parts: list[tuple[int, int]], budget: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for s, e in parts:
        if out and d.tokens(out[-1][0], e) <= budget:
            out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


# --- strategies --------------------------------------------------------------------------------


def _fixed_spans(d: _Doc, budget: int, overlap: int) -> list[tuple[int, int]]:
    wins = window_pack([u.ntok for u in d.units], budget, overlap)
    return [(d.units[i].start, d.units[j - 1].end) for i, j in wins]


def _sentence_spans(d: _Doc, budget: int, overlap: int) -> list[tuple[int, int]]:
    sents: list[tuple[int, int]] = []
    for s, e in split_sentences(d.text, 0, len(d.text)):
        if d.tokens(s, e) > budget:
            sents.extend(_hard_cut(d, s, e, budget))
        else:
            sents.append((s, e))
    sizes = [d.tokens(s, e) for s, e in sents]
    wins = window_pack(sizes, budget, overlap)
    return [(sents[i][0], sents[j - 1][1]) for i, j in wins]


_LEVELS = (split_headings, split_paragraphs, split_sentences)


def _recurse(d: _Doc, s: int, e: int, level: int, budget: int) -> list[tuple[int, int]]:
    if d.tokens(s, e) <= budget:
        return [(s, e)]
    if level >= len(_LEVELS):
        return _hard_cut(d, s, e, budget)
    parts = _LEVELS[level](d.text, s, e)
    if len(parts) <= 1:
        return _recurse(d, s, e, level + 1, budget)
    flat: list[tuple[int, int]] = []
    for ps, pe in parts:
        flat.extend(_recurse(d, ps, pe, level + 1, budget))
    return _merge(d, flat, budget)


def _line_bounds(text: str, pos: int) -> tuple[int, int]:
    ls = text.rfind("\n", 0, pos) + 1
    le = text.find("\n", pos)
    return ls, len(text) if le < 0 else le


def _recursive_spans(d: _Doc, budget: int, overlap: int) -> list[tuple[int, int, int]]:
    """Returns (overlap_start, body_start, end). Body <= budget - overlap, so with the overlap
    prefix the chunk stays within budget."""
    body_budget = budget - overlap if budget - overlap >= MIN_BUDGET else budget
    ov = overlap if body_budget != budget else 0
    spans = _recurse(d, 0, len(d.text), 0, body_budget)
    out: list[tuple[int, int, int]] = []
    for idx, (s, e) in enumerate(spans):
        os_ = s
        if idx > 0 and ov > 0:
            k = bisect.bisect_left(d.starts, s)  # first unit at/after body start
            tot = 0
            while k - 1 >= 0 and tot + d.units[k - 1].ntok <= ov:
                k -= 1
                tot += d.units[k].ntok
            if k < len(d.units) and d.units[k].start < s:
                os_ = d.units[k].start
                ls, le = _line_bounds(d.text, os_)
                if os_ > ls and d.text[ls:le].lstrip().startswith("#"):
                    os_ = min(le, s)  # never start mid-heading: skip the rest of that line
        out.append((os_, s, e))
    return out


# --- entry point -------------------------------------------------------------------------------


def chunk_document(text: str, spec: ChunkSpec, counter: TokenCounter) -> list[Chunk]:
    positions, paths = heading_paths(text)
    reserve = 0
    if spec.prepend_headings and paths:
        reserve = max(counter.count(p) for p in paths) + 1
    budget = spec.max_tokens - reserve
    if budget < MIN_BUDGET:
        raise ChunkingError(
            f"budget {budget} too small (max_tokens={spec.max_tokens}, reserve={reserve})"
        )
    if spec.overlap_tokens < 0 or spec.overlap_tokens >= budget:
        raise ChunkingError("overlap_tokens must be in [0, budget)")

    d = _Doc(text, counter, budget)
    if not d.units:
        return []
    raw: list[tuple[int, int, int]]  # (start, body_start, end)
    if d.prefix[-1] <= budget:
        raw = [(d.units[0].start, d.units[0].start, d.units[-1].end)]
    elif spec.strategy == "fixed_token":
        raw = [(s, s, e) for s, e in _fixed_spans(d, budget, spec.overlap_tokens)]
    elif spec.strategy == "sentence":
        raw = [(s, s, e) for s, e in _sentence_spans(d, budget, spec.overlap_tokens)]
    elif spec.strategy == "recursive_md":
        raw = _recursive_spans(d, budget, spec.overlap_tokens)
    else:
        raise ChunkingError(f"unknown strategy {spec.strategy!r}")

    chunks: list[Chunk] = []
    for s, body_s, e in raw:
        seg = text[s:e]
        lead = len(seg) - len(seg.lstrip())
        s, e = s + lead, s + len(seg.rstrip())
        if e <= s:
            continue
        content = text[s:e]
        n = counter.count(content)
        if n > budget:
            raise ChunkingError(f"chunk {len(chunks)} has {n} tokens > budget {budget}")
        chunks.append(
            Chunk(len(chunks), s, e, content, n, section_path_at(positions, paths, max(body_s, s)))
        )
    return chunks


def embed_text(chunk: Chunk, prepend_headings: bool) -> str:
    return (
        f"{chunk.section_path}\n{chunk.text}"
        if prepend_headings and chunk.section_path
        else chunk.text
    )
