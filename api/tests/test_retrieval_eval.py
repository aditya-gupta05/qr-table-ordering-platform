"""Pure metric tests on toy rankings (no DB, no models)."""

import math

import pytest

from app.retrieval.evaluation import (
    EvidenceSpan,
    Labels,
    QuoteError,
    RankedHit,
    apply_threshold,
    budget_prefix_len,
    context_precision_at_k,
    distribution,
    mean_metrics,
    ndcg_at_k,
    query_metrics,
    recall_at_k,
    recall_within_budget,
    reciprocal_rank,
    resolve_quote,
    select_threshold,
    success_at_k,
    token_containment,
    token_jaccard,
)


def chunk(start, end, slug="faq", tokens=100):
    return RankedHit("chunk", slug=slug, start=start, end=end, tokens=tokens)


def item(name, tokens=20):
    return RankedHit("menu_item", name=name, tokens=tokens)


LABELS = Labels(
    items={"Pad Thai": 2},
    spans=(EvidenceSpan("faq", 100, 200, 2), EvidenceSpan("policies", 0, 50, 1)),
)
MISS = chunk(500, 600)


def test_perfect_ranking():
    hits = [item("Pad Thai"), chunk(90, 210), chunk(0, 60, "policies")]
    assert recall_at_k(hits, LABELS, 3) == 1.0
    assert recall_at_k(hits, LABELS, 1) == pytest.approx(1 / 3)
    assert reciprocal_rank(hits, LABELS) == 1.0
    assert ndcg_at_k(hits, LABELS, 3) == pytest.approx(1.0)
    assert context_precision_at_k(hits, LABELS, 3) == 1.0


def test_miss_then_hit():
    hits = [MISS, MISS, chunk(100, 200)]
    assert reciprocal_rank(hits, LABELS) == pytest.approx(1 / 3)
    assert success_at_k(hits, LABELS, 2) == 0.0 and success_at_k(hits, LABELS, 3) == 1.0
    assert context_precision_at_k(hits, LABELS, 3) == pytest.approx(1 / 3)
    assert recall_at_k([MISS], LABELS, 5) == 0.0


def test_graded_ndcg_prefers_grade2_first():
    labels = Labels(spans=(EvidenceSpan("faq", 0, 10, 2), EvidenceSpan("faq", 100, 110, 1)))
    good = [chunk(0, 10), chunk(100, 110)]
    bad = [chunk(100, 110), chunk(0, 10)]
    assert ndcg_at_k(good, labels, 2) == pytest.approx(1.0)
    ideal = (2**2 - 1) + (2**1 - 1) / math.log2(3)
    assert ndcg_at_k(bad, labels, 2) == pytest.approx(
        ((2**1 - 1) + (2**2 - 1) / math.log2(3)) / ideal
    )
    assert ndcg_at_k(bad, labels, 2) < 1.0


def test_duplicate_hits_on_one_label_credit_it_once():
    labels = Labels(spans=(EvidenceSpan("faq", 0, 10, 2),))
    hits = [chunk(0, 10), chunk(0, 12)]  # two overlapping chunks both cover the quote
    assert ndcg_at_k(hits, labels, 2) == pytest.approx(1.0)  # not > 1
    assert recall_at_k(hits, labels, 2) == 1.0
    assert context_precision_at_k(hits, labels, 2) == 1.0  # both are relevant context


def test_offset_overlap_quote_split_across_two_chunks():
    quote = EvidenceSpan("faq", 100, 200, 2)  # 100 chars, chunk boundary at 160
    labels = Labels(spans=(quote,))
    big, small = chunk(0, 160), chunk(160, 300)
    assert recall_at_k([big], labels, 1) == 1.0  # covers 60/100 chars -> relevant
    assert success_at_k([big], labels, 1) == 1.0
    assert success_at_k([small], labels, 1) == 0.0  # covers only 40/100
    even = Labels(spans=(EvidenceSpan("faq", 100, 200, 2),))
    assert success_at_k([chunk(0, 150)], even, 1) == 1.0  # exactly 50% counts
    assert success_at_k([chunk(0, 149)], even, 1) == 0.0
    # wrong document never matches, even with identical offsets
    assert success_at_k([chunk(100, 200, "policies")], labels, 1) == 0.0


def test_budget_truncation():
    assert budget_prefix_len([400, 400, 400], 1024) == 2
    assert budget_prefix_len([400, 1500, 10], 1024) == 1  # overflow ends the context
    assert budget_prefix_len([1500], 1024) == 0
    assert budget_prefix_len([512, 512], 1024) == 2  # exactly at budget fits
    assert budget_prefix_len([], 1024) == 0
    labels = Labels(spans=(EvidenceSpan("faq", 100, 200, 2),))
    hits = [chunk(0, 50, tokens=600), chunk(100, 200, tokens=600)]
    assert recall_at_k(hits, labels, 5) == 1.0
    assert recall_within_budget(hits, labels, 1024) == 0.0  # relevant chunk falls off the budget
    assert recall_within_budget(hits, labels, 1200) == 1.0


def test_no_answer_queries_have_no_ranking_metrics():
    empty = Labels()
    m = query_metrics([MISS], empty)
    assert all(v is None for v in m.values())


def test_query_metrics_keys_and_mean_skips_none():
    m1 = query_metrics([item("Pad Thai")], LABELS)
    m2 = query_metrics([MISS], LABELS)
    m3 = query_metrics([MISS], Labels())
    assert {"recall@1", "recall@10", "mrr", "ndcg@5", "ctx_precision@3", "recall@1024tok"} <= set(
        m1
    )
    mean = mean_metrics([m1, m2, m3])
    assert mean["mrr"] == pytest.approx(0.5)  # the None query is skipped
    assert mean["recall@5"] == pytest.approx((1 / 3 + 0) / 2)


def test_resolve_quote():
    text = "Alpha beta.\n\nGamma delta\nepsilon zeta. Alpha beta."
    s, e = resolve_quote(text, "Gamma delta epsilon zeta.")  # PDF-style line wrap
    assert text[s:e] == "Gamma delta\nepsilon zeta."
    with pytest.raises(QuoteError):
        resolve_quote(text, "not there")
    with pytest.raises(QuoteError):
        resolve_quote(text, "Alpha beta.")  # twice
    with pytest.raises(QuoteError):
        resolve_quote(text, "  ")


def test_threshold_selection():
    ans = [0.9, 0.8, 0.7, 0.4]
    no = [0.3, 0.2, 0.5]
    t = select_threshold(ans, no)
    assert t is not None
    assert 0.5 < t.threshold < 0.7 or 0.4 < t.threshold < 0.5
    ar, ab = apply_threshold(t.threshold, ans, no)
    assert (ar, ab) == (t.answer_rate, t.abstain_rate)
    assert t.balanced_accuracy == pytest.approx((ar + ab) / 2)
    # separable -> perfect gate
    sep = select_threshold([0.9, 0.8], [0.1, 0.2])
    assert sep and sep.balanced_accuracy == 1.0 and 0.2 < sep.threshold < 0.8
    assert select_threshold([], [0.1]) is None and select_threshold([0.1], []) is None
    # all overlapping equal scores: ties go to the larger (abstain-safe) threshold
    flat = select_threshold([0.5], [0.5])
    assert flat and flat.threshold > 0.5


def test_distribution():
    d = distribution([1.0, 2.0, 3.0, 4.0, 5.0])
    assert (d["min"], d["median"], d["max"], d["n"]) == (1.0, 3.0, 5.0, 5)
    assert distribution([])["n"] == 0


def test_token_overlap():
    assert token_jaccard("the quick fox", "the quick fox") == 1.0
    assert token_jaccard("a b", "c d") == 0.0
    assert token_jaccard("a b c", "b c d") == pytest.approx(2 / 4)
    assert token_containment("is wifi free", "free wifi for all") == pytest.approx(2 / 3)


def test_grouped_recall_counts_a_group_once():
    # the dish guide (0-100) and the PDF (500-600) both state the same fact: one group
    labels = Labels(
        items={"Pad Thai": 2},
        spans=(
            EvidenceSpan("dish-guide", 0, 100, 2, group="prep"),
            EvidenceSpan("menu-pdf", 500, 600, 1, group="prep"),
            EvidenceSpan("faq", 0, 50, 2),  # ungrouped: its own group
        ),
    )
    assert labels.n == 3  # Pad Thai, prep, faq (not 4)
    assert labels.grades == [2, 2, 2]  # group "prep" takes its max grade
    pdf_only = [chunk(500, 600, "menu-pdf")]
    # either member covers the group
    assert recall_at_k(pdf_only, labels, 1) == pytest.approx(1 / 3)
    both = [chunk(0, 100, "dish-guide"), chunk(500, 600, "menu-pdf")]
    assert recall_at_k(both, labels, 2) == pytest.approx(1 / 3)  # the second member adds nothing
    full = [item("Pad Thai"), chunk(0, 100, "dish-guide"), chunk(0, 50, "faq")]
    assert recall_at_k(full, labels, 3) == 1.0


def test_grouped_ndcg_uses_group_max_grade_and_credits_once():
    labels = Labels(
        spans=(
            EvidenceSpan("faq", 0, 10, 1, group="g"),
            EvidenceSpan("policies", 0, 10, 2, group="g"),
        )
    )
    best_first = [chunk(0, 10, "policies"), chunk(0, 10, "faq")]
    # ideal = one grade-2 credit, not two
    assert ndcg_at_k(best_first, labels, 2) == pytest.approx(1.0)
    weak_first = [chunk(0, 10, "faq"), chunk(0, 10, "policies")]
    # only the first covering hit is credited, at ITS grade (1): finding the best member late costs
    assert ndcg_at_k(weak_first, labels, 2) == pytest.approx((2**1 - 1) / (2**2 - 1))


def test_item_groups_let_alternate_menu_items_share_a_group():
    labels = Labels(items={"A": 2, "B": 2}, item_groups={"A": "pick", "B": "pick"})
    assert labels.n == 1
    assert recall_at_k([item("B")], labels, 1) == 1.0
