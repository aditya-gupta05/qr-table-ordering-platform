"""Allergen synonym table and allergy-cue detection (deterministic; no LLM involved).

REVIEW THIS FILE (see docs/reviews/allergen-terms-review.md for a table view).

Why it exists: the model is told to put allergens into `exclude_allergens`, but models drop or
mis-map synonyms ("nuts" -> only tree_nut, "coeliac" -> nothing). Before a menu search runs, the
server therefore reads the diner's own message, maps what it finds through TERMS, and UNIONS the
result into the filters. `widen()` can only add exclusions, never remove one the model chose.

Design rules
- A term is only consulted AFTER an allergy cue ("allergic to ...", "can't eat ...", "X allergy"),
  or when the model hands it to us in `other_allergy_terms`. A bare word such as "tofu" or "butter"
  in "show me tofu dishes" is never an allergy declaration.
- Mapping is deliberately WIDE (over-excluding is the safe direction): "nuts" means peanut AND
  tree_nut, "seafood" means fish AND shellfish.
- A term that is not in TERMS is returned as an *untracked term*: the bot must say it is not
  tracked and offer a waiter. Absence from the table is a decision, not an oversight (see
  DELIBERATELY_UNMAPPED).
"""

import re
from dataclasses import dataclass, field

from app.vocab import ALLERGENS

# --- the synonym table ------------------------------------------------------------------------
# term (lower case, singular and plural spelled out) -> allergens it excludes.
# Entries marked (?) are judgement calls and are listed in the review document.
TERMS: dict[str, tuple[str, ...]] = {
    # peanut
    "peanut": ("peanut",),
    "peanuts": ("peanut",),
    "groundnut": ("peanut",),
    "groundnuts": ("peanut",),
    "monkey nut": ("peanut",),
    "monkey nuts": ("peanut",),
    "peanut butter": ("peanut",),
    # generic nuts: wide on purpose (?)
    "nut": ("peanut", "tree_nut"),
    "nuts": ("peanut", "tree_nut"),
    "nut allergy": ("peanut", "tree_nut"),
    # tree nuts
    "tree nut": ("tree_nut",),
    "tree nuts": ("tree_nut",),
    "treenut": ("tree_nut",),
    "treenuts": ("tree_nut",),
    "almond": ("tree_nut",),
    "almonds": ("tree_nut",),
    "walnut": ("tree_nut",),
    "walnuts": ("tree_nut",),
    "cashew": ("tree_nut",),
    "cashews": ("tree_nut",),
    "pistachio": ("tree_nut",),
    "pistachios": ("tree_nut",),
    "hazelnut": ("tree_nut",),
    "hazelnuts": ("tree_nut",),
    "pecan": ("tree_nut",),
    "pecans": ("tree_nut",),
    "macadamia": ("tree_nut",),
    "macadamias": ("tree_nut",),
    "brazil nut": ("tree_nut",),
    "brazil nuts": ("tree_nut",),
    "pine nut": ("tree_nut",),  # (?) botanically a seed; the kitchen lists it with tree nuts
    "pine nuts": ("tree_nut",),  # (?)
    "chestnut": ("tree_nut",),  # (?)
    "chestnuts": ("tree_nut",),  # (?)
    # fish
    "fish": ("fish",),
    "salmon": ("fish",),
    "tuna": ("fish",),
    "cod": ("fish",),
    "haddock": ("fish",),
    "trout": ("fish",),
    "mackerel": ("fish",),
    "sardine": ("fish",),
    "sardines": ("fish",),
    "anchovy": ("fish",),
    "anchovies": ("fish",),
    "fish sauce": ("fish",),
    # shellfish
    "shellfish": ("shellfish",),
    "shell fish": ("shellfish",),
    "crustacean": ("shellfish",),
    "crustaceans": ("shellfish",),
    "prawn": ("shellfish",),
    "prawns": ("shellfish",),
    "shrimp": ("shellfish",),
    "shrimps": ("shellfish",),
    "crab": ("shellfish",),
    "crabs": ("shellfish",),
    "lobster": ("shellfish",),
    "lobsters": ("shellfish",),
    "crayfish": ("shellfish",),
    "langoustine": ("shellfish",),
    "scampi": ("shellfish",),
    # molluscs are mapped to shellfish because the menu data tags squid (calamari) as shellfish (?)
    "mollusc": ("shellfish",),
    "molluscs": ("shellfish",),
    "mollusk": ("shellfish",),
    "mollusks": ("shellfish",),
    "squid": ("shellfish",),
    "calamari": ("shellfish",),
    "octopus": ("shellfish",),
    "clam": ("shellfish",),
    "clams": ("shellfish",),
    "mussel": ("shellfish",),
    "mussels": ("shellfish",),
    "oyster": ("shellfish",),
    "oysters": ("shellfish",),
    "scallop": ("shellfish",),
    "scallops": ("shellfish",),
    # seafood: wide on purpose
    "seafood": ("fish", "shellfish"),
    "sea food": ("fish", "shellfish"),
    # gluten
    "gluten": ("gluten",),
    "wheat": ("gluten",),
    "coeliac": ("gluten",),
    "celiac": ("gluten",),
    "coeliac disease": ("gluten",),
    "celiac disease": ("gluten",),
    "barley": ("gluten",),
    "rye": ("gluten",),
    "spelt": ("gluten",),
    "semolina": ("gluten",),
    # dairy (lactose intolerance is not milk allergy, but the exclusion we can offer is the same)
    "dairy": ("dairy",),
    "milk": ("dairy",),
    "cow's milk": ("dairy",),
    "cows milk": ("dairy",),
    "lactose": ("dairy",),
    "cheese": ("dairy",),  # (?) a single dairy product, widened to the whole group
    "cream": ("dairy",),  # (?)
    "butter": ("dairy",),  # (?)
    "yogurt": ("dairy",),
    "yoghurt": ("dairy",),
    "whey": ("dairy",),
    "casein": ("dairy",),
    "ghee": ("dairy",),
    # egg
    "egg": ("egg",),
    "eggs": ("egg",),
    # sesame
    "sesame": ("sesame",),
    "sesame seed": ("sesame",),
    "sesame seeds": ("sesame",),
    "tahini": ("sesame",),
    # soy. Safe to map "tofu" here: it only fires after an allergy cue, and tofu is made of soy (?)
    "soy": ("soy",),
    "soya": ("soy",),
    "soybean": ("soy",),
    "soybeans": ("soy",),
    "soy sauce": (
        "soy",
        "gluten",
    ),  # (?) most soy sauce contains wheat; approved by user 2026-10-11
    "tofu": ("soy",),  # (?)
    "tempeh": ("soy",),
    "edamame": ("soy",),
    "miso": ("soy",),
    # mustard, celery, sulphites
    "mustard": ("mustard",),
    "celery": ("celery",),
    "celeriac": ("celery",),  # (?)
    "sulphite": ("sulphites",),
    "sulphites": ("sulphites",),
    "sulfite": ("sulphites",),
    "sulfites": ("sulphites",),
    "sulphur dioxide": ("sulphites",),
    "sulfur dioxide": ("sulphites",),
}

# Considered and left OUT on purpose. They fall through to `untracked_terms`, which makes the bot
# say "not tracked" and offer a waiter. Listed so a reviewer sees the decision.
DELIBERATELY_UNMAPPED: dict[str, str] = {
    "coconut": "kitchen policy: not treated as a tree nut; individual reactions vary",
    "nutmeg": "kitchen policy: not a tree nut",
    "wine": "could be sulphites or alcohol; ask the waiter rather than guess",
    "oats": "oat allergy is not gluten; the oat latte is tagged gluten by the kitchen",
    "garlic": "not one of the 12 tracked allergens",
    "onion": "not one of the 12 tracked allergens",
    "kiwi": "not one of the 12 tracked allergens",
    "lupin": "EU allergen but not tracked in this menu",
    "alcohol": "not an allergen in the menu data",
}

# Words that mean "nothing specific" after a cue ("allergic to everything").
IGNORE_TERMS = frozenset(
    [
        "anything",
        "everything",
        "nothing",
        "food",
        "foods",
        "it",
        "this",
        "that",
        "them",
        "these",
        "those",
        "stuff",
        "things",
        "something",
        "someone",
        "one",
        "some",
        "lots",
        "lot",
    ]
)

# --- cue detection ---------------------------------------------------------------------------

# Cues that introduce a list of things the diner cannot have. (?) = judgement call.
CUES_BEFORE_LIST: tuple[str, ...] = (
    r"allergic\s+to",
    r"allergy\s+to",
    r"allergies\s+to",
    r"allerg(?:y|ies)\s*(?::|-|is|are|include|includes)",
    r"intoleran(?:t|ce)\s+to",
    r"sensitiv(?:e|ity)\s+to",
    r"react(?:s)?\s+to",
    r"reaction\s+to",
    r"can'?t\s+(?:eat|have)",
    r"cannot\s+(?:eat|have)",
    r"can\s+not\s+(?:eat|have)",
    r"cant\s+(?:eat|have)",
    r"unable\s+to\s+eat",
    r"not\s+able\s+to\s+eat",
    r"must\s+not\s+eat",
    r"mustn'?t\s+eat",
    r"not\s+allowed\s+(?:to\s+eat\s+)?",
    r"(?:must|have\s+to|has\s+to|need\s+to|needs\s+to)\s+avoid",  # (?) bare "avoid" is not a cue
)

# "<term> allergy|allergic|intolerant|intolerance" ("I have a nut allergy", "lactose intolerant").
POST_TERM_CUE = r"(?:allerg(?:y|ies|ic)|intoleran(?:t|ce))"
# The word before the term must declare a condition ("a nut allergy"), so "your allergy policy"
# or "what is the nut allergy policy" does not count. (?)
DECLARING_PRECEDERS = frozenset(
    [
        "a",
        "an",
        "have",
        "has",
        "got",
        "with",
        "severe",
        "bad",
        "mild",
        "serious",
        "real",
        "really",
        "slight",
        "small",
        "life-threatening",
        "i",
        "my",
        "he",
        "she",
        "they",
        "we",
        "is",
        "am",
        "are",
        "very",
    ]
)

# Words that end a list of allergy terms ("allergic to kiwi but what can I eat").
_STOP_WORDS = frozenset(
    [
        "i",
        "i'm",
        "im",
        "me",
        "my",
        "we",
        "you",
        "he",
        "she",
        "they",
        "it",
        "what",
        "which",
        "who",
        "how",
        "can",
        "could",
        "do",
        "does",
        "did",
        "is",
        "are",
        "was",
        "were",
        "will",
        "would",
        "should",
        "please",
        "so",
        "but",
        "because",
        "then",
        "show",
        "give",
        "recommend",
        "suggest",
        "want",
        "like",
        "need",
        "looking",
        "there",
        "there's",
        "any",
        "thanks",
        "thank",
        "for",
        "on",
        "at",
        "in",
        "when",
        "where",
        "why",
        "if",
    ]
)
_LEADING_FILLER = re.compile(
    r"^(?:(?:any|all|the|a|an|some|severe(?:ly)?|mild|really|very|bad|badly|also|to|of|from|"
    r"eating|having|anything|food|foods|dishes|dish|with|containing|that|has|have|made|using|"
    r"my|his|her|our|their)\s+)+"
)
_NEGATION_BEFORE = re.compile(r"(?:\bnot\b|n't|\bno\b|\bnever\b|\bwithout\b)[^.?!;]{0,25}$")
_LIST_SPLIT = re.compile(r",|;|&|/|\band\b|\bor\b|\bas well as\b|\bplus\b|\balso\b")
_COELIAC = re.compile(r"\b(?:coeliac|celiac)\b")
_NO_PLEASE = re.compile(r"\bno\s+([a-z' ]{2,30}?)\s*,?\s+please\b")
_GENERIC_CUE = re.compile(
    r"\ballergic\b(?!\s+to\b)|\b(?:have|has|got)\s+(?:(?:an?|some|severe|food|bad)\s+)*allerg(?:y|ies)\b"
)
_ALLERGY_TOPIC = re.compile(
    r"\b(?:allerg\w*|intoleran\w*|coeliac|celiac|anaphyla\w*|epipen|gluten|dairy|lactose|"
    r"vegan|vegetarian|halal|nut|nuts|peanut\w*|shellfish|sesame|dietary|safe)\b"
)


@dataclass(frozen=True)
class Declaration:
    """What the diner's message says about their allergies."""

    cue: bool = False  # an allergy cue was found (even with no recognisable term)
    allergens: tuple[str, ...] = ()  # mapped, vocab-ordered
    untracked: tuple[str, ...] = ()  # terms after a cue that are not in TERMS
    phrases: tuple[str, ...] = field(default=(), compare=False)  # mapped phrases, for logs


def _norm(message: str) -> str:
    return message.lower().replace("’", "'").replace("‘", "'")


def _ordered(allergens: set[str]) -> tuple[str, ...]:
    return tuple(a for a in ALLERGENS if a in allergens)


def lookup(phrase: str) -> tuple[str, ...] | None:
    """Allergens for a phrase: exact entry, else the union over its words/bigrams, else None."""
    p = re.sub(r"[^a-z' -]", "", phrase.lower().replace("’", "'")).strip(" -'")
    p = re.sub(r"\s+", " ", p)
    if not p:
        return None
    if p in TERMS:
        return TERMS[p]
    words = p.split()
    found: set[str] = set()
    for i, w in enumerate(words):
        if w in TERMS:
            found.update(TERMS[w])
        if i + 1 < len(words) and f"{w} {words[i + 1]}" in TERMS:
            found.update(TERMS[f"{w} {words[i + 1]}"])
    return _ordered(found) if found else None


def _parse_list(tail: str, allergens: set[str], untracked: list[str], phrases: list[str]) -> None:
    tail = re.split(r"[.?!\n]", tail, maxsplit=1)[0][:120]
    for raw in _LIST_SPLIT.split(tail):
        piece = _LEADING_FILLER.sub("", raw.strip())
        if not piece:
            continue
        words = piece.split()
        cut = next((i for i, w in enumerate(words) if w in _STOP_WORDS), len(words))
        words = words[:cut]
        done = cut < len(piece.split())  # a stop word ended the list after this piece
        phrase = " ".join(words).strip(" -'")
        if phrase and not all(w in IGNORE_TERMS for w in words):
            mapped = lookup(phrase)
            if mapped:
                allergens.update(mapped)
                phrases.append(phrase)
            elif len(words) <= 3:
                untracked.append(phrase)
        if done or not words:
            break


def extract_declaration(message: str) -> Declaration:
    """Find allergy declarations in a diner message. Conservative about *cues*; liberal about
    *mapping* (see module docstring). Negated cues ("not allergic to nuts") are ignored."""
    m = _norm(message)
    allergens: set[str] = set()
    untracked: list[str] = []
    phrases: list[str] = []
    cue = False

    for pat in CUES_BEFORE_LIST:
        for hit in re.finditer(r"\b" + pat + r"\b\s*", m):
            if _NEGATION_BEFORE.search(m[: hit.start()]):
                continue
            cue = True
            _parse_list(m[hit.end() :], allergens, untracked, phrases)

    for hit in re.finditer(r"([a-z' -]{1,40}?)\s+" + POST_TERM_CUE + r"\b", m):
        if _NEGATION_BEFORE.search(m[: hit.start() + len(hit.group(1))]):
            continue
        words = hit.group(1).split()
        # 'a nut allergy', 'tree nut allergy', 'lactose intolerant': the term is the last 1-2
        # words and the word before it must declare a condition.
        for n in (2, 1):
            if len(words) < n:
                continue
            term = " ".join(words[-n:])
            before = words[-n - 1] if len(words) > n else None
            ok_before = before in DECLARING_PRECEDERS or (before is None and hit.start() == 0)
            if term in TERMS and ok_before:
                cue = True
                allergens.update(TERMS[term])
                phrases.append(term)
                break
        else:
            if len(words) >= 2 and words[-2] in DECLARING_PRECEDERS - {"i", "am", "is", "are"}:
                cue = True  # "a kiwi allergy": declared, not mappable
                if words[-1] not in _STOP_WORDS and words[-1] not in IGNORE_TERMS:
                    untracked.append(words[-1])

    # generic declarations with no term: "I'm allergic, what can I eat?", "I have a food allergy"
    for hit in _GENERIC_CUE.finditer(m):
        if not _NEGATION_BEFORE.search(m[: hit.start()]):
            cue = True

    if _COELIAC.search(m) and not _NEGATION_BEFORE.search(m[: _COELIAC.search(m).start()]):  # type: ignore[union-attr]
        cue = True
        allergens.add("gluten")
        phrases.append("coeliac")

    for hit in _NO_PLEASE.finditer(m):  # "no nuts please": widen on known terms, never flag unknown
        mapped = lookup(hit.group(1))
        if mapped:
            cue = True
            allergens.update(mapped)
            phrases.append(hit.group(1).strip())

    # an untracked term that is also mapped elsewhere in the message is not untracked
    untracked_final = tuple(dict.fromkeys(t for t in untracked if lookup(t) is None))
    return Declaration(cue, _ordered(allergens), untracked_final, tuple(dict.fromkeys(phrases)))


def mentions_allergen_topic(message: str) -> bool:
    """Broader than a declaration: any allergy / diet / 'safe' talk. Used to mark a turn
    allergen-sensitive (forces the DB-built allergen notice and the unsafe-language check)."""
    m = _norm(message)
    if _ALLERGY_TOPIC.search(m) or extract_declaration(m).cue:
        return True
    return any(re.search(r"\b" + re.escape(t) + r"\b", m) for t in TERMS if len(t) > 3)


@dataclass(frozen=True)
class WidenResult:
    exclude_allergens: tuple[str, ...]  # final union, vocab order
    widened_from_message: tuple[str, ...] = ()  # added because of the current message
    widened_from_session: tuple[str, ...] = ()  # added because declared earlier this session
    widened_from_terms: tuple[str, ...] = ()  # added from the model's other_allergy_terms
    untracked_terms: tuple[str, ...] = ()
    cue: bool = False

    @property
    def widened(self) -> bool:
        return bool(
            self.widened_from_message or self.widened_from_session or self.widened_from_terms
        )


def widen(
    model_args: dict, user_message: str, declared: tuple[str, ...] | list[str] = ()
) -> WidenResult:
    """Union the model's exclusions with everything the diner declared. Never removes anything.

    model_args: the (already schema-valid) search_menu arguments; reads `exclude_allergens` and
    `other_allergy_terms`. `declared`: allergens stored on the session from earlier turns.
    """
    base = {a for a in model_args.get("exclude_allergens", []) if a in ALLERGENS}
    final = set(base)

    from_terms: set[str] = set()
    untracked: list[str] = []
    for t in model_args.get("other_allergy_terms", []):
        mapped = lookup(t)
        if mapped:
            from_terms.update(mapped)
        elif t.strip() and not all(w in IGNORE_TERMS for w in t.lower().split()):
            untracked.append(t.strip().lower())
    from_terms -= final
    final |= from_terms

    decl = extract_declaration(user_message)
    from_msg = set(decl.allergens) - final
    final |= from_msg
    untracked.extend(decl.untracked)

    from_session = {a for a in declared if a in ALLERGENS} - final
    final |= from_session

    return WidenResult(
        exclude_allergens=_ordered(final),
        widened_from_message=_ordered(from_msg),
        widened_from_session=_ordered(from_session),
        widened_from_terms=_ordered(from_terms),
        untracked_terms=tuple(dict.fromkeys(untracked)),
        cue=decl.cue,
    )
