"""Reciprocal Rank Fusion. Rank-based so lists with incomparable scales (cosine vs ts_rank_cd,
short menu rows vs long chunks) can be combined without tuning weights."""

from collections.abc import Hashable
from dataclasses import dataclass, field


@dataclass
class Fused:
    key: Hashable
    score: float
    ranks: dict[str, int] = field(default_factory=dict)  # source -> 1-based rank


def rrf(ranked: dict[str, list[Hashable]], k: int = 60) -> list[Fused]:
    """score(d) = sum over lists of 1 / (k + rank_in_list). Ties break by best rank in any list,
    then by key, so output is deterministic."""
    acc: dict[Hashable, Fused] = {}
    for source, keys in ranked.items():
        seen: set[Hashable] = set()
        for rank, key in enumerate(keys, start=1):
            if key in seen:  # a duplicate inside one list must not count twice
                continue
            seen.add(key)
            f = acc.setdefault(key, Fused(key, 0.0))
            f.score += 1.0 / (k + rank)
            f.ranks[source] = rank
    return sorted(acc.values(), key=lambda f: (-f.score, min(f.ranks.values()), str(f.key)))
