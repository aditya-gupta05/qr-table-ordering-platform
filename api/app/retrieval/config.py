"""Retrieval configuration and filter objects (immutable, validated on construction)."""

from dataclasses import dataclass, field, replace
from typing import Literal

from app.retrieval.embedder import MODELS
from app.vocab import ALLERGENS, DIETARY_TAGS

Mode = Literal["vector", "fts", "hybrid"]
Scope = Literal["menu", "docs"]


@dataclass(frozen=True)
class Filters:
    """Applied in SQL WHERE clauses (never post-filtered in Python).

    `exclude_allergens`/`dietary`/`available_only` also drop document chunks that mention a menu
    item violating them (via `chunks.menu_item_ids`), so a PDF chunk about Chicken Satay cannot
    slip past `exclude_allergens=["peanut"]`. `categories` applies to menu items only.
    """

    available_only: bool = True
    exclude_allergens: tuple[str, ...] = ()
    dietary: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    source_types: tuple[str, ...] = ()

    def __post_init__(self):
        bad = set(self.exclude_allergens) - set(ALLERGENS)
        bad |= set(self.dietary) - set(DIETARY_TAGS)
        if bad:
            raise ValueError(f"unknown filter values: {sorted(bad)}")

    @property
    def restricts_items(self) -> bool:
        return bool(self.exclude_allergens or self.dietary or self.available_only)


@dataclass(frozen=True)
class RetrievalConfig:
    mode: Mode = "hybrid"
    k: int = 5
    candidate_k: int = 50  # per ranked list, before fusion
    rrf_k: int = 60
    rerank: bool = False
    rerank_top_n: int = 20
    embed_model: str = "BAAI/bge-small-en-v1.5"
    query_prefix: str | None = None  # None -> model default; "" -> no prefix
    # Phase 4 default, chosen on dev (docs/reports/phase-4-retrieval.md)
    chunk_config: str = "fixed_128_o30"
    ef_search: int = 100
    iterative_scan: Literal["off", "strict_order", "relaxed_order"] = "relaxed_order"
    vector_index: Literal["hnsw", "exact"] = "hnsw"
    # AND: the OR list is mostly noise for natural questions (dev: hybrid nDCG@5 0.643 -> 0.694)
    fts_operator: Literal["or", "and"] = "and"
    extra: dict = field(default_factory=dict, compare=False, hash=False)

    def __post_init__(self):
        if self.mode not in ("vector", "fts", "hybrid"):
            raise ValueError(f"bad mode {self.mode!r}")
        if self.embed_model not in MODELS:
            raise ValueError(f"unsupported embedding model {self.embed_model!r}")
        if self.k < 1 or self.candidate_k < self.k or self.rrf_k < 1:
            raise ValueError("need k >= 1, candidate_k >= k, rrf_k >= 1")
        if not 1 <= self.ef_search <= 1000:
            raise ValueError("ef_search must be in [1, 1000]")

    def with_(self, **kw) -> "RetrievalConfig":
        return replace(self, **kw)
