"""Retrieval evaluation metrics. Pure functions: no DB, no models, no I/O.

Labels survive re-chunking because they are not chunk ids:
  * menu items by name (grade 2)
  * evidence quotes resolved to character offsets in the *cleaned* document text
A chunk is relevant to a quote when it covers >= `MIN_COVER` (50%) of the quote's characters, so the
same labels grade every chunk config. A menu-item hit is relevant when its name is labelled.

Label groups
  Several labels can answer the same thing (the dish guide and the printed PDF both state the
  opening hours). A label may carry an optional `group`; labels sharing a group are one unit:
  recall counts the group once, covered as soon as ANY member is in the top-k. Without a group a
  label is its own group, so ungrouped datasets behave exactly as before. nDCG gives a group one
  credit (first covering hit) and the ideal DCG uses the group's MAX grade, so duplicate
  alternates can neither inflate the ideal nor be double counted.

Conventions
  * relevance grade of a hit = max grade over the labels it matches (0 = not relevant)
  * recall@k      = label groups covered by the top-k / all groups
  * success@k     = 1 if any top-k hit is relevant (the "did it find something" rate)
  * MRR           = 1 / rank of the first relevant hit
  * nDCG@k        = DCG with gain 2^grade - 1, each label credited at most once (a second hit on an
                    already-credited label gains 0), over the ideal DCG of the label grades
  * context precision@k = relevant hits / hits returned in the top-k
  * budget recall = recall over the longest prefix of hits whose summed tokens fit the budget
                    (hits are never cut in half; the hit that would overflow ends the context)
Metrics that need relevant labels return None for no-answer queries; callers average over the
answerable queries only and treat no-answer queries with the score-distribution tools below.
"""

import math
import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

MIN_COVER = 0.5
DEFAULT_KS = (1, 3, 5, 10)
BUDGET_TOKENS = 1024
_WORD = re.compile(r"[a-z0-9']+")


class QuoteError(ValueError):
    """An evidence quote does not resolve to exactly one place in its document."""


# --- labels ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceSpan:
    slug: str
    start: int
    end: int
    grade: int = 2
    group: str | None = None


@dataclass(frozen=True)
class Labels:
    items: dict[str, int] = field(default_factory=dict)  # menu item name -> grade
    spans: tuple[EvidenceSpan, ...] = ()
    item_groups: dict[str, str] = field(default_factory=dict)  # menu item name -> group

    def item_gid(self, name: str) -> str:
        g = self.item_groups.get(name)
        return f"group:{g}" if g else f"item:{name}"

    def span_gid(self, i: int) -> str:
        g = self.spans[i].group
        return f"group:{g}" if g else f"span:{i}"

    @property
    def group_grades(self) -> dict[str, int]:
        """group id -> max grade of its members (ungrouped labels are singleton groups)."""
        out: dict[str, int] = {}
        for name, g in self.items.items():
            gid = self.item_gid(name)
            out[gid] = max(out.get(gid, 0), g)
        for i, sp in enumerate(self.spans):
            gid = self.span_gid(i)
            out[gid] = max(out.get(gid, 0), sp.grade)
        return out

    @property
    def n(self) -> int:
        """Number of label groups (what recall divides by)."""
        return len(self.group_grades)

    @property
    def grades(self) -> list[int]:
        return list(self.group_grades.values())


@dataclass(frozen=True)
class RankedHit:
    """What the metrics need to know about one retrieved hit."""

    kind: str  # "menu_item" | "chunk"
    name: str | None = None  # menu item name
    slug: str | None = None  # document slug
    start: int = 0  # chunk char offsets in the cleaned document
    end: int = 0
    tokens: int = 0


def _ws_pattern(quote: str) -> str:
    return r"\s+".join(re.escape(t) for t in quote.split())


def resolve_quote(text: str, quote: str) -> tuple[int, int]:
    """Offsets of `quote` in the cleaned document `text`. Whitespace-insensitive (PDF line wraps),
    otherwise verbatim. Raises QuoteError if it is missing or ambiguous."""
    if not quote.strip():
        raise QuoteError("empty quote")
    found = list(re.finditer(_ws_pattern(quote), text))
    if not found:
        raise QuoteError(f"quote not found: {quote[:60]!r}")
    if len(found) > 1:
        raise QuoteError(f"quote is ambiguous ({len(found)} matches): {quote[:60]!r}")
    return found[0].start(), found[0].end()


def overlap_fraction(span: EvidenceSpan, slug: str | None, start: int, end: int) -> float:
    """Fraction of the quote's characters covered by chunk [start, end) of document `slug`."""
    if slug != span.slug:
        return 0.0
    ov = min(end, span.end) - max(start, span.start)
    return max(0, ov) / (span.end - span.start)


# --- relevance ------------------------------------------------------------------------------------


def hit_matches(
    hit: RankedHit, labels: Labels, min_cover: float = MIN_COVER
) -> list[tuple[str, int]]:
    """[(group_id, grade)] this hit satisfies. Group ids: 'item:<name>' / 'span:<index>' for
    ungrouped labels, 'group:<name>' for grouped ones."""
    out: list[tuple[str, int]] = []
    if hit.kind == "menu_item":
        if hit.name in labels.items:
            out.append((labels.item_gid(hit.name), labels.items[hit.name]))
        return out
    for i, sp in enumerate(labels.spans):
        if overlap_fraction(sp, hit.slug, hit.start, hit.end) >= min_cover:
            out.append((labels.span_gid(i), sp.grade))
    return out


def hit_grades(hits: Sequence[RankedHit], labels: Labels) -> list[int]:
    return [max((g for _, g in hit_matches(h, labels)), default=0) for h in hits]


def covered_labels(hits: Sequence[RankedHit], labels: Labels) -> set[str]:
    return {lid for h in hits for lid, _ in hit_matches(h, labels)}


# --- ranking metrics ------------------------------------------------------------------------------


def recall_at_k(hits: Sequence[RankedHit], labels: Labels, k: int) -> float | None:
    if labels.n == 0:
        return None
    return len(covered_labels(hits[:k], labels)) / labels.n


def success_at_k(hits: Sequence[RankedHit], labels: Labels, k: int) -> float | None:
    if labels.n == 0:
        return None
    return 1.0 if any(hit_grades(hits[:k], labels)) else 0.0


def reciprocal_rank(hits: Sequence[RankedHit], labels: Labels) -> float | None:
    if labels.n == 0:
        return None
    for rank, g in enumerate(hit_grades(hits, labels), start=1):
        if g > 0:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(hits: Sequence[RankedHit], labels: Labels, k: int) -> float | None:
    if labels.n == 0:
        return None
    credited: set[str] = set()
    dcg = 0.0
    for rank, h in enumerate(hits[:k], start=1):
        fresh = [(lid, g) for lid, g in hit_matches(h, labels) if lid not in credited]
        if not fresh:
            continue
        lid, g = max(fresh, key=lambda x: x[1])
        credited.add(lid)
        dcg += (2**g - 1) / math.log2(rank + 1)
    ideal = sorted(labels.grades, reverse=True)[:k]
    idcg = sum((2**g - 1) / math.log2(r + 1) for r, g in enumerate(ideal, start=1))
    return dcg / idcg if idcg else 0.0


def context_precision_at_k(hits: Sequence[RankedHit], labels: Labels, k: int) -> float | None:
    if labels.n == 0:
        return None
    top = hits[:k]
    if not top:
        return 0.0
    return sum(1 for g in hit_grades(top, labels) if g > 0) / len(top)


def budget_prefix_len(token_counts: Sequence[int], budget: int = BUDGET_TOKENS) -> int:
    """Number of leading hits whose summed tokens fit `budget`. The first hit that would overflow
    ends the context (later, smaller hits are not skipped into: rank order is the contract)."""
    total = 0
    for i, t in enumerate(token_counts):
        total += t
        if total > budget:
            return i
    return len(token_counts)


def recall_within_budget(
    hits: Sequence[RankedHit], labels: Labels, budget: int = BUDGET_TOKENS
) -> float | None:
    if labels.n == 0:
        return None
    n = budget_prefix_len([h.tokens for h in hits], budget)
    return len(covered_labels(hits[:n], labels)) / labels.n


def query_metrics(
    hits: Sequence[RankedHit],
    labels: Labels,
    ks: Sequence[int] = DEFAULT_KS,
    budget: int = BUDGET_TOKENS,
) -> dict[str, float | None]:
    m: dict[str, float | None] = {}
    for k in ks:
        m[f"recall@{k}"] = recall_at_k(hits, labels, k)
        m[f"success@{k}"] = success_at_k(hits, labels, k)
        m[f"ndcg@{k}"] = ndcg_at_k(hits, labels, k)
        m[f"ctx_precision@{k}"] = context_precision_at_k(hits, labels, k)
    m["mrr"] = reciprocal_rank(hits, labels)
    m[f"recall@{budget}tok"] = recall_within_budget(hits, labels, budget)
    return m


def mean_metrics(per_query: Sequence[dict[str, float | None]]) -> dict[str, float | None]:
    """Mean per metric over the queries where it is defined; None if no query defines it."""
    keys = sorted({k for m in per_query for k in m})
    out: dict[str, float | None] = {}
    for k in keys:
        vals = [m[k] for m in per_query if m.get(k) is not None]
        out[k] = statistics.fmean(vals) if vals else None  # type: ignore[arg-type]
    return out


# --- no-answer scores and abstention threshold ----------------------------------------------------


def distribution(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "min": None, "p25": None, "median": None, "p75": None, "max": None}
    s = sorted(values)

    def q(p: float) -> float:
        i = (len(s) - 1) * p
        lo, hi = math.floor(i), math.ceil(i)
        return s[lo] + (s[hi] - s[lo]) * (i - lo)

    return {
        "n": len(s),
        "min": s[0],
        "p25": q(0.25),
        "median": q(0.5),
        "p75": q(0.75),
        "max": s[-1],
    }


@dataclass(frozen=True)
class Threshold:
    """Rule: answer when top-1 score >= threshold, otherwise abstain."""

    threshold: float
    balanced_accuracy: float
    answer_rate: float  # fraction of answerable queries answered (recall of the gate)
    abstain_rate: float  # fraction of no-answer queries correctly abstained


def apply_threshold(
    t: float, answerable: Sequence[float], unanswerable: Sequence[float]
) -> tuple[float, float]:
    ar = sum(s >= t for s in answerable) / len(answerable) if answerable else 0.0
    ab = sum(s < t for s in unanswerable) / len(unanswerable) if unanswerable else 0.0
    return ar, ab


def select_threshold(
    answerable: Sequence[float], unanswerable: Sequence[float]
) -> Threshold | None:
    """Threshold maximising balanced accuracy of the answer/abstain gate. Candidates are midpoints
    between consecutive distinct scores plus the extremes; ties go to the larger threshold (the
    safer side: abstaining beats hallucinating). None when either side is empty."""
    if not answerable or not unanswerable:
        return None
    pts = sorted(set(answerable) | set(unanswerable))
    cands = [
        pts[0] - 1e-9,
        *((a + b) / 2 for a, b in zip(pts, pts[1:], strict=False)),
        pts[-1] + 1e-9,
    ]
    best: Threshold | None = None
    for t in cands:
        ar, ab = apply_threshold(t, answerable, unanswerable)
        cur = Threshold(t, (ar + ab) / 2, ar, ab)
        if best is None or cur.balanced_accuracy >= best.balanced_accuracy:
            best = cur
    return best


# --- leakage check --------------------------------------------------------------------------------


def tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def token_jaccard(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.0


def token_containment(query: str, doc: str) -> float:
    """Share of the query tokens that also occur in `doc` (high = copied wording)."""
    tq = tokens(query)
    return len(tq & tokens(doc)) / len(tq) if tq else 0.0
