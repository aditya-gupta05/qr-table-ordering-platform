"""PII redaction for diner text: emails, phone numbers, payment-card numbers.

Applied before storing a message and before sending it to the LLM (plan decision 15). Regex based
and deliberately a bit conservative about what counts as a phone number, so prices ("$14.50"),
table labels ("T3"), dates ("2026-10-11") and quantities are left alone. Card-like digit runs are
only redacted when they pass the Luhn check, so a random 16-digit number is not mangled.
"""

import re
from dataclasses import dataclass

EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
# 13-19 digits, optionally grouped by single spaces or dashes
CARD = re.compile(r"(?<![\d])(?:\d[ \-]?){12,18}\d(?![\d])")
# International: +CC then 7-14 more digits with common separators; or 00CC...
PHONE_INTL = re.compile(r"(?<![\w+])(?:\+|00)\d{1,3}(?:[\s.\-()]*\d){7,13}(?!\d)")
# North-American style and common national formats with separators
PHONE_NATIONAL = re.compile(
    r"(?<![\w.])(?:"
    r"(?:\+?1[\s.\-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.\-])\d{3}[\s.\-]\d{4}"
    r"|\d{10,11}"  # 7805551234
    r"|0\d{2,4}[\s.\-]\d{3,4}[\s.\-]?\d{3,4}"  # 020 7946 0958, 07911 123456
    r")(?![\w])"
)
PHONE_LOCAL = re.compile(r"(?<![\w.\-])\d{3}[\s\-]\d{4}(?![\w\-])")  # 555-1234


def luhn_ok(digits: str) -> bool:
    if not 13 <= len(digits) <= 19 or not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass(frozen=True)
class Redaction:
    text: str
    emails: int = 0
    phones: int = 0
    cards: int = 0

    @property
    def total(self) -> int:
        return self.emails + self.phones + self.cards


def redact_with_counts(text: str) -> Redaction:
    counts = {"emails": 0, "phones": 0, "cards": 0}

    def card_sub(m: re.Match) -> str:
        if luhn_ok(re.sub(r"\D", "", m.group(0))):
            counts["cards"] += 1
            return "[card]"
        return m.group(0)

    def phone_sub(m: re.Match) -> str:
        counts["phones"] += 1
        return "[phone]"

    def count_sub(key: str):
        def sub(_m: re.Match) -> str:
            counts[key] += 1
            return "[email]"

        return sub

    out = EMAIL.sub(count_sub("emails"), text)
    out = CARD.sub(card_sub, out)
    for pat in (PHONE_INTL, PHONE_NATIONAL, PHONE_LOCAL):
        out = pat.sub(phone_sub, out)
    return Redaction(out, **counts)


def redact(text: str) -> str:
    return redact_with_counts(text).text
