import pytest

from app.retrieval.chunking import (
    ChunkingError,
    ChunkSpec,
    TokenCounter,
    chunk_document,
    embed_text,
    heading_paths,
    window_pack,
)
from tests.retrieval_helpers import toy_tokenizer

SAMPLE = """# Guide

Intro sentence one. Intro sentence two is a little longer than the first. Third intro sentence.

## Hours

### When do you open?
We open at noon every day. The kitchen closes thirty minutes before the doors do. Last orders
are taken at the bar until close.

### Do you take bookings?
Yes we take bookings by phone or on the website. Large parties need a card guarantee and a
deposit. Walk-ins are always welcome when we have space.

## Fryers

A long paragraph about the shared fryer. It holds oil that is changed every third day. Staff
filter it daily. The gluten free fryer is separate and has its own baskets and scoop. Even so
a busy service can splash oil between the two. Ask the chef if you are unsure about anything.
Another sentence to make this paragraph comfortably longer than the smaller budgets used here.
"""


def counter(text: str = SAMPLE) -> TokenCounter:
    return TokenCounter(toy_tokenizer(text))


def numbered(n: int) -> str:
    return " ".join(f"w{i}" for i in range(n))


def non_ws_covered(text: str, chunks) -> bool:
    covered = [False] * len(text)
    for c in chunks:
        for i in range(c.start, c.end):
            covered[i] = True
    return all(covered[i] or text[i].isspace() for i in range(len(text)))


@pytest.mark.parametrize("overlap", [0, 7, 15])
def test_fixed_exact_token_overlap(overlap):
    text = numbered(200)
    c = counter(text)
    chunks = chunk_document(text, ChunkSpec("fixed_token", 50, overlap), c)
    assert all(ch.token_count <= 50 for ch in chunks)
    assert chunks[0].token_count == 50
    for a, b in zip(chunks, chunks[1:], strict=False):
        ta, tb = a.text.split(), b.text.split()
        shared = len([w for w in tb if w in set(ta)])
        assert shared == overlap
        assert tb[:overlap] == ta[len(ta) - overlap :] if overlap else True
    assert non_ws_covered(text, chunks)


@pytest.mark.parametrize(
    "spec",
    [
        ChunkSpec("fixed_token", 30, 5),
        ChunkSpec("recursive_md", 30, 5),
        ChunkSpec("recursive_md", 60, 0),
        ChunkSpec("sentence", 30, 8),
    ],
)
def test_full_character_coverage_and_budget(spec):
    c = counter()
    chunks = chunk_document(SAMPLE, spec, c)
    assert len(chunks) > 1
    assert non_ws_covered(SAMPLE, chunks)
    for ch in chunks:
        assert ch.text == SAMPLE[ch.start : ch.end]
        assert ch.token_count == c.count(ch.text) <= spec.max_tokens


def test_prepended_heading_counts_against_budget():
    c = counter()
    spec = ChunkSpec("recursive_md", 40, 5, prepend_headings=True)
    chunks = chunk_document(SAMPLE, spec, c)
    assert chunks
    for ch in chunks:
        assert c.count(embed_text(ch, True)) <= 40
    assert any(ch.section_path for ch in chunks)


def test_recursive_never_splits_a_heading():
    c = counter()
    for max_tokens in (20, 30, 45, 80):
        chunks = chunk_document(SAMPLE, ChunkSpec("recursive_md", max_tokens, 4), c)
        heading_lines = [
            (m_start, m_start + len(line))
            for m_start, line in _lines(SAMPLE)
            if line.startswith("#")
        ]
        for ch in chunks:
            for hs, he in heading_lines:
                inter = min(ch.end, he) - max(ch.start, hs)
                assert inter <= 0 or (ch.start <= hs and ch.end >= he), (max_tokens, ch.text)
            last_line = ch.text.strip().splitlines()[-1]
            assert not last_line.startswith("#"), "chunk ends on a stranded heading"


def _lines(text):
    pos = 0
    for line in text.split("\n"):
        yield pos, line
        pos += len(line) + 1


def test_sentences_are_kept_whole():
    c = counter()
    chunks = chunk_document(SAMPLE, ChunkSpec("sentence", 30, 8), c)
    for ch in chunks:
        t = ch.text.strip()
        assert t[0].isupper() or t[0] == "#", t
        assert t[-1] in ".?", t


def test_tiny_document_is_one_chunk():
    text = "# T\n\nJust one short sentence."
    for strategy in ("fixed_token", "recursive_md", "sentence"):
        chunks = chunk_document(text, ChunkSpec(strategy, 128, 10), counter(text))
        assert len(chunks) == 1 and chunks[0].text == text


def test_section_path_tracks_headings():
    positions, paths = heading_paths(SAMPLE)
    assert "Hours > When do you open?" in paths
    chunks = chunk_document(SAMPLE, ChunkSpec("recursive_md", 30, 0), counter())
    assert any(ch.section_path == "Fryers" for ch in chunks)


def test_invalid_overlap_rejected():
    with pytest.raises(ChunkingError):
        chunk_document(SAMPLE, ChunkSpec("fixed_token", 30, 30), counter())
    with pytest.raises(ChunkingError):
        chunk_document(SAMPLE, ChunkSpec("fixed_token", 8, 0), counter())


def test_window_pack_always_advances_and_covers():
    sizes = [3, 3, 3, 3, 3, 3, 3]
    wins = window_pack(sizes, budget=9, overlap=8)
    assert wins[0] == (0, 3)
    starts = [w[0] for w in wins]
    assert starts == sorted(set(starts)) and wins[-1][1] == len(sizes)
