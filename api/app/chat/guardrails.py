"""Deterministic post-checks on the model's final answer (plan section 4). Pure functions: no DB,
no LLM, no clock. The orchestrator gathers the inputs (tool results this turn / session, DB facts
for the 42 menu items) and acts on the returned violations.

Checks (counter label = `Violation.check`):
 1 unknown_citation          cited ids must have been returned by a tool in this session
 2 ungrounded_answer         an `answered` reply needs a tool result this turn or a known citation
 3 price_mismatch            every $ amount must appear in tool results (or the diner's own message)
 4 unsafe_allergen_language  "safe", "allergen-free", "guarantee", "no risk", "100%" ... on
                             allergen-sensitive turns (negated uses such as "can't guarantee" pass)
 5 allergen_contradiction    answer says an item has NO allergen X but the DB lists X for it
 6 prompt_leak               canary or a long run of words from the system prompt
 7 url_or_markup             URLs, HTML and markdown are stripped (auto-fixed, not repaired)

Repair policy lives in the orchestrator: one repair call naming the failed check, then
`build_fallback_message` (DB facts only) with outcome `blocked`.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache

from app.chat.allergen_terms import TERMS

DISCLAIMER = (
    "Allergen information comes from our menu data and may not cover cross-contact in the "
    "kitchen. Please confirm with your server before ordering."
)
DISCLAIMER_DOC_SLUG = "allergen-kitchen-practices"  # source of the notice's source_chunk_id


# --- DB facts and formatting ---------------------------------------------------------------------


@dataclass(frozen=True)
class ItemFact:
    """One menu item as stored in the DB. The truth the guardrails compare answers against."""

    id: int
    name: str
    category: str = ""
    price_cents: int = 0
    allergens: tuple[str, ...] = ()
    dietary_tags: tuple[str, ...] = ()
    spice_level: int = 0
    available: bool = True
    description: str = ""


def fmt_price(cents: int) -> str:
    return f"${cents // 100}.{cents % 100:02d}"


_PRICE = re.compile(r"\$\s?(\d{1,5})(?:[.,](\d{2}))?(?!\d)")
_PRICE_WORDS = re.compile(r"(?<![\d.])(\d{1,5})[.,](\d{2})\s*(?:dollars|usd)\b", re.I)


def extract_price_cents(text: str) -> list[int]:
    """Every price in `text` as integer cents. "$14" -> 1400, "$14.50" -> 1450."""
    out = [int(a) * 100 + int(b or 0) for a, b in _PRICE.findall(text)]
    out += [int(a) * 100 + int(b) for a, b in _PRICE_WORDS.findall(text)]
    return out


# --- item-name detection --------------------------------------------------------------------------


def name_variants(name: str) -> set[str]:
    """'Pinot Grigio (glass)' -> {full, 'Pinot Grigio'}; '&' <-> 'and'."""
    base = {name.strip()}
    if "(" in name:
        base.add(re.sub(r"\s*\(.*?\)\s*", " ", name).strip())
    out = set()
    for v in base:
        out.add(v)
        if "&" in v:
            out.add(re.sub(r"\s*&\s*", " and ", v))
        if re.search(r"\band\b", v, re.I):
            out.add(re.sub(r"\band\b", "&", v, flags=re.I))
    return {re.sub(r"\s+", " ", v).strip().lower() for v in out if v}


@dataclass(frozen=True)
class Mention:
    start: int
    end: int
    item_id: int


class NameMatcher:
    """Case-insensitive, word-boundary detection of menu item names (and their variants)."""

    def __init__(self, items: Iterable[ItemFact]):
        self.variant_to_id: dict[str, int] = {}
        for it in items:
            for v in name_variants(it.name):
                self.variant_to_id.setdefault(v, it.id)
        if self.variant_to_id:
            alts = sorted(self.variant_to_id, key=len, reverse=True)  # longest first
            pat = "|".join(r"\s+".join(re.escape(w) for w in a.split()) for a in alts)
            self._re = re.compile(rf"(?<!\w)(?:{pat})(?!\w)", re.I)
        else:
            self._re = None

    def find(self, text: str) -> list[Mention]:
        if self._re is None:
            return []
        return [
            Mention(m.start(), m.end(), self.variant_to_id[re.sub(r"\s+", " ", m.group()).lower()])
            for m in self._re.finditer(text)
        ]


# --- violations and report ------------------------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    check: str
    detail: str = ""
    repairable: bool = True  # False: already fixed in the sanitized answer, no repair call needed


@dataclass(frozen=True)
class AnswerContext:
    status: str  # respond() status
    cited_item_ids: tuple[int, ...] = ()
    cited_passage_ids: tuple[int, ...] = ()
    known_item_ids: frozenset[int] = frozenset()  # returned by tools, whole session
    known_passage_ids: frozenset[int] = frozenset()
    grounded_this_turn: bool = False  # a search/get tool succeeded this turn
    allowed_price_cents: frozenset[int] = frozenset()  # tool results this turn + diner's message
    facts: Mapping[int, ItemFact] = field(default_factory=dict)  # all menu items from the DB
    allergen_sensitive: bool = False
    canary: str = ""
    system_prompt: str = ""


@dataclass(frozen=True)
class GuardrailReport:
    violations: tuple[Violation, ...]
    answer: str  # sanitized (URLs/markup stripped); other violations leave the text unchanged

    @property
    def needs_repair(self) -> bool:
        return any(v.repairable for v in self.violations)

    @property
    def checks(self) -> list[str]:
        return [v.check for v in self.violations]


# --- 1, 2: citations and grounding ----------------------------------------------------------------


def check_citations(ctx: AnswerContext) -> list[Violation]:
    out = []
    bad_items = sorted(set(ctx.cited_item_ids) - ctx.known_item_ids)
    bad_passages = sorted(set(ctx.cited_passage_ids) - ctx.known_passage_ids)
    if bad_items or bad_passages:
        out.append(Violation("unknown_citation", f"items={bad_items} passages={bad_passages}"))
    if ctx.status == "answered" and ctx.grounded_this_turn:
        valid = (set(ctx.cited_item_ids) & ctx.known_item_ids) | (
            set(ctx.cited_passage_ids) & ctx.known_passage_ids
        )
        if not valid:
            out.append(Violation("unknown_citation", "answered without any valid citation"))
    return out


def check_grounding(ctx: AnswerContext) -> list[Violation]:
    if ctx.status != "answered" or ctx.grounded_this_turn:
        return []
    cited_known = (set(ctx.cited_item_ids) & ctx.known_item_ids) | (
        set(ctx.cited_passage_ids) & ctx.known_passage_ids
    )
    if cited_known:  # follow-up answered from an earlier turn's results
        return []
    return [Violation("ungrounded_answer", "answered with no tool result behind it")]


# --- 3: prices ------------------------------------------------------------------------------------


def check_prices(answer: str, ctx: AnswerContext) -> list[Violation]:
    bad = sorted({c for c in extract_price_cents(answer) if c not in ctx.allowed_price_cents})
    if not bad:
        return []
    return [
        Violation("price_mismatch", "not in tool results: " + ", ".join(fmt_price(c) for c in bad))
    ]


# --- 4: unsafe allergen language ------------------------------------------------------------------

_FREE_WORDS = (
    r"allergen|nut|peanut|tree[ -]nut|gluten|dairy|egg|soy|shellfish|fish|sesame|wheat|lactose|"
    r"mustard|celery|sulphite|sulfite"
)
_NEG_WINDOW = re.compile(
    r"(?:\bnot\b|n't\b|\bcannot\b|\bnever\b|\bunable\b|\bno\b|\bwithout\b|\brule out\b)"
)
_TAG_AFTER = re.compile(r"\W*(?:tag|tags|tagged|label|labels|labelled|labeled|filter|filters)\b")
_TAG_BEFORE = re.compile(
    r"(?:tagged|tag|labelled|labeled|marked|listed)\s+(?:as\s+)?(?:the\s+)?$", re.I
)
_UNSAFE: tuple[tuple[str, re.Pattern], ...] = tuple(
    (name, re.compile(pat, re.I))
    for name, pat in (
        (
            "safe",
            r"\bsafe (?:for|to eat|to order|to have|option|choice|bet|dish|meal|pick)\b"
            r"|\b(?:is|are|it's|be|perfectly|totally|completely|entirely|absolutely|100%) safe\b",
        ),
        ("allergen_free", rf"\b(?:{_FREE_WORDS})[ -]free\b"),
        ("free_of", rf"\bfree (?:of|from) (?:any |all )?(?:{_FREE_WORDS})s?\b"),
        ("guarantee", r"\bguarantee[sd]?\b"),
        (
            "no_risk",
            r"\bno (?:risk|chance|danger)\b|\brisk[- ]free\b|\bzero risk\b"
            r"|\bwithout (?:any )?risk\b",
        ),
        ("hundred_percent", r"\b100\s?%"),
        (
            "completely_free",
            r"\b(?:completely|totally|entirely|fully|absolutely)\s+(?:free|clean|pure)\b",
        ),
        ("no_cross_contact", r"\b(?:no|without) cross[- ](?:contact|contamination)\b"),
    )
)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def check_unsafe_language(answer: str, ctx: AnswerContext) -> list[Violation]:
    if not ctx.allergen_sensitive:
        return []
    found: list[str] = []
    for sent in _sentences(answer):
        for name, pat in _UNSAFE:
            for m in pat.finditer(sent):
                before = sent[max(0, m.start() - 60) : m.start()]
                if _NEG_WINDOW.search(before):
                    continue  # "can't guarantee", "I cannot say it is safe"
                if name in ("allergen_free", "free_of") and (
                    _TAG_AFTER.match(sent, m.end()) or _TAG_BEFORE.search(before)
                ):
                    continue  # "gluten-free tag", "tagged gluten-free"
                found.append(f"{name}: {m.group()!r}")
    return [Violation("unsafe_allergen_language", "; ".join(dict.fromkeys(found)))] if found else []


# --- 5: allergen contradiction --------------------------------------------------------------------

_ALLERGEN_WORDS = "|".join(re.escape(t) for t in sorted(TERMS, key=len, reverse=True))
_NEG = re.compile(
    r"\bno\b|\bnot\b|n't\b|\bnever\b|\bwithout\b|\bfree (?:of|from)\b|\blacks?\b|\bomits?\b"
    r"|\bexcludes?\b|\bnor\b|\bneither\b"
)
_FIRST_ALLERGEN = re.compile(rf"(?:\s+\w+){{0,3}}?\s+(?:any |a |an )?({_ALLERGEN_WORDS})\b")
_NEXT_ALLERGEN = re.compile(
    rf"\s*(?:,\s*(?:and |or |nor )?|/|\band\b|\bor\b|\bnor\b)"
    rf"\s*(?:no |any |a )?({_ALLERGEN_WORDS})\b"
)
_ALLERGEN_FREE = re.compile(rf"\b({_ALLERGEN_WORDS})[ _-]free\b")


def _negated_allergens(sent: str) -> list[tuple[int, set[str]]]:
    """[(position, allergens)] for each 'no X' / 'X-free' / 'does not contain X, Y' in `sent`."""
    out: list[tuple[int, set[str]]] = []
    low = sent.lower()
    for m in _ALLERGEN_FREE.finditer(low):
        out.append((m.start(), set(TERMS[m.group(1)])))
    for m in _NEG.finditer(low):
        first = _FIRST_ALLERGEN.match(low, m.end())
        if not first:
            continue
        al = set(TERMS[first.group(1)])
        pos = first.end()
        while (nxt := _NEXT_ALLERGEN.match(low, pos)) is not None:
            al |= set(TERMS[nxt.group(1)])
            pos = nxt.end()
        out.append((m.start(), al))
    return out


def check_contradictions(
    answer: str, ctx: AnswerContext, matcher: NameMatcher | None = None
) -> list[Violation]:
    matcher = matcher or NameMatcher(ctx.facts.values())
    found: list[str] = []
    last_item: int | None = None  # subject carried over to the next sentence ("It has no nuts")
    for sent in _sentences(answer):
        mentions = matcher.find(sent)
        masked = list(sent)
        for mn in mentions:  # item names must not look like allergen words ("Fish and Chips")
            masked[mn.start : mn.end] = "#" * (mn.end - mn.start)
        for pos, allergens in _negated_allergens("".join(masked)):
            before = [mn for mn in mentions if mn.start < pos]
            after = [mn for mn in mentions if mn.start >= pos]
            if before:
                item_id = before[-1].item_id
            elif after:
                item_id = after[0].item_id
            else:
                item_id = last_item
            fact = ctx.facts.get(item_id) if item_id is not None else None
            if fact and (clash := sorted(allergens & set(fact.allergens))):
                found.append(f"{fact.name}: says no {'/'.join(clash)} but menu data lists it")
        if mentions:
            last_item = mentions[-1].item_id
    return [Violation("allergen_contradiction", "; ".join(dict.fromkeys(found)))] if found else []


# --- 6: prompt leak -------------------------------------------------------------------------------

_LEAK_N = 12  # words per shingle
_LEAK_MIN_SHINGLES = 2  # distinct shingles required => a verbatim run of >= 13 words
_LEAK_MIN_CONTENT = 5  # a shingle needs this many non-stop-words to count as distinctive
_WORD = re.compile(r"[a-z0-9']+")
_STOP_TEXT = (
    "a an and are as at be but by can do for from has have i if in is it its me my no not of on "
    "or our so that the their them then there they this to us we what when where which who "
    "will with you your"
)
_STOP = frozenset(_STOP_TEXT.split())


def _public_scope(prompt: str) -> str:
    """Prompt without its SCOPE block: the bot is allowed to tell diners what it can help with, so
    that wording must not count as a leak."""
    blocks = prompt.split("\n\n")
    return "\n\n".join(b for b in blocks if not b.lstrip().startswith("SCOPE"))


@lru_cache(maxsize=4)
def _prompt_shingles(prompt: str) -> frozenset[tuple[str, ...]]:
    w = _WORD.findall(_public_scope(prompt).lower())
    out = set()
    for i in range(len(w) - _LEAK_N + 1):
        sh = tuple(w[i : i + _LEAK_N])
        if sum(1 for t in sh if t not in _STOP) >= _LEAK_MIN_CONTENT:
            out.add(sh)
    return frozenset(out)


def check_prompt_leak(answer: str, ctx: AnswerContext) -> list[Violation]:
    """The canary always fires. Otherwise the answer must reproduce >= 2 distinct, content-heavy
    12-word shingles of the system prompt (a >= 13-word verbatim run). Ordinary phrases the prompt
    also uses ("I can help with the menu") are far too short to match."""
    if ctx.canary and ctx.canary.lower() in answer.lower():
        return [Violation("prompt_leak", "canary")]
    if ctx.system_prompt:
        w = _WORD.findall(answer.lower())
        shingles = _prompt_shingles(ctx.system_prompt)
        hit = {
            sh for i in range(len(w) - _LEAK_N + 1) if (sh := tuple(w[i : i + _LEAK_N])) in shingles
        }
        if len(hit) >= _LEAK_MIN_SHINGLES:
            return [Violation("prompt_leak", f"{_LEAK_N}-word runs from the system prompt")]
    return []


# --- 7: URLs and markup ---------------------------------------------------------------------------

_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(
    r"https?://\S+|www\.\S+|\b[\w-]+(?:\.[\w-]+)*\.(?:com|org|net|io|co|app|ca|uk|dev)\b(?:/\S*)?",
    re.I,
)
_HTML = re.compile(r"</?[a-zA-Z][^>]*>")


def strip_markup(text: str) -> str:
    out = _MD_LINK.sub(r"\1", text)
    out = _URL.sub("", out)
    out = _HTML.sub("", out)
    out = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", out)  # headings
    out = re.sub(r"\*\*|__|`", "", out)
    out = re.sub(r"(?<!\w)\*(?!\s)|(?<=\S)\*(?!\w)", "", out)  # *emphasis* (keeps "5 * 2")
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


def check_markup(answer: str) -> tuple[list[Violation], str]:
    clean = strip_markup(answer)
    if clean == answer.strip():
        return [], answer
    return [Violation("url_or_markup", "stripped", repairable=False)], clean


# --- orchestration of the seven checks ------------------------------------------------------------


def run_checks(
    answer: str, ctx: AnswerContext, matcher: NameMatcher | None = None
) -> GuardrailReport:
    markup_v, clean = check_markup(answer)
    violations = [
        *check_citations(ctx),
        *check_grounding(ctx),
        *check_prices(clean, ctx),
        *check_unsafe_language(clean, ctx),
        *check_contradictions(clean, ctx, matcher),
        *check_prompt_leak(answer, ctx),  # on the raw text: stripping must not hide a leak
        *markup_v,
    ]
    return GuardrailReport(tuple(violations), clean)


# --- DB-built allergen notice and fallback ---------------------------------------------------


def build_allergen_notice(
    items: Iterable[ItemFact], disclaimer: str = DISCLAIMER, source_chunk_id: int | None = None
) -> dict:
    """The structured notice attached to every allergen-sensitive turn, built from the DB only."""
    seen: dict[int, ItemFact] = {}
    for it in items:
        seen.setdefault(it.id, it)
    return {
        "items": [
            {
                "menu_item_id": it.id,
                "name": it.name,
                "allergens": sorted(it.allergens),
                "dietary_tags": sorted(it.dietary_tags),
            }
            for it in seen.values()
        ],
        "disclaimer": disclaimer,
        "source_chunk_id": source_chunk_id,
    }


def items_for_notice(
    answer: str,
    cited_item_ids: Iterable[int],
    facts: Mapping[int, ItemFact],
    matcher: NameMatcher | None = None,
) -> list[ItemFact]:
    """Items an allergen notice should cover: cited ids first, then any item named in the answer
    text. Unknown ids are skipped. Order is stable and de-duplicated."""
    matcher = matcher or NameMatcher(facts.values())
    ids = list(cited_item_ids) + [m.item_id for m in matcher.find(answer)]
    return [facts[i] for i in dict.fromkeys(ids) if i in facts]


def _join(parts: Sequence[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def build_fallback_message(items: Iterable[ItemFact], max_items: int = 5) -> str:
    """Replacement text when the model's answer fails a check twice. Only DB facts, no model text.
    Passes its own guardrails (no safety claims; absence is worded as 'none listed')."""
    by_id = {it.id: it for it in items}
    shown = list(by_id)[:max_items]
    if not shown:
        return (
            "I can't give you a reliable answer to that. Please ask a member of staff, "
            "or tap Call a waiter and I will let them know."
        )
    lines = []
    for iid in shown:
        it = by_id[iid]
        al = _join(sorted(it.allergens)) if it.allergens else "no allergens listed"
        lines.append(f"{it.name} ({fmt_price(it.price_cents)}): {al}.")
    return (
        "I can't give you a checked answer to that, so here is what our menu data lists. "
        + " ".join(lines)
        + " The kitchen cannot rule out cross-contact, so please speak to your server. "
        "Tap Call a waiter and I will let them know."
    )
