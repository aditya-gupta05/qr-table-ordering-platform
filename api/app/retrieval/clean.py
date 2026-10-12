"""Text cleaning. The stored `documents.content` is the cleaned text; chunk offsets index into it.

PDF pitfalls handled here (each is present in corpus/menu.pdf, see scripts/gen_menu_pdf.py):
  * running header/footer repeated on every page ("Page N of M" digits differ per page)
  * dot leaders between a dish name and its price (`Name . . . . . $11.50`)
  * hyphenated line wraps ("Slow-\\nroasted")
  * out-of-order reading (allergy box drawn first) -- NOT fixed, only measured; see the phase report
"""

import re
from collections import Counter
from dataclasses import dataclass, field

_DOT_ONLY = re.compile(r"^[\s.·…]{4,}$")
_DOT_RUN = re.compile(r"(?:[ \t]*[.·…]){4,}[ \t]*")
_PAGE_NO = re.compile(r"^\s*(?:page\s+)?\d+\s*(?:of\s+\d+)?\s*$", re.IGNORECASE)
EDGE_LINES = 3
_PRICE = re.compile(r"^\s*\$\d")
_SOFT_WRAP = re.compile(r"([A-Za-z])-\n([a-z])")


def _norm_key(line: str) -> str:
    return re.sub(r"\d+", "#", line.strip().lower())


def clean_markdown(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


@dataclass
class PdfCleanResult:
    text: str
    page_spans: list[tuple[int, int, int]]  # (start_char, end_char, page_number) in `text`
    stats: dict[str, int] = field(default_factory=dict)


def clean_pdf_pages(pages: list[str], repeat_threshold: float = 0.5) -> PdfCleanResult:
    """Clean per-page extracted text and join into one document with page spans."""
    n_pages = len(pages)
    per_page_lines = [[ln.strip() for ln in p.splitlines()] for p in pages]

    # Lines (digits normalised) that sit in the top/bottom EDGE lines of >= threshold of pages are
    # running headers/footers. Restricting to edge lines matters: with digits normalised, every
    # "$11.50"-style price line looks like a repeated line (the first attempt deleted every price).
    seen = Counter()
    for lines in per_page_lines:
        nonempty = [ln for ln in lines if ln]
        edge = nonempty[:EDGE_LINES] + nonempty[-EDGE_LINES:]
        seen.update({_norm_key(ln) for ln in edge if not _PRICE.match(ln)})
    min_pages = max(2, int(n_pages * repeat_threshold + 0.999))
    repeated = {k for k, c in seen.items() if c >= min_pages and n_pages >= 2}

    stats = {"repeated_lines": 0, "page_numbers": 0, "dot_leader_lines": 0, "dot_runs": 0}
    cleaned_pages: list[str] = []
    for lines in per_page_lines:
        kept: list[str] = []
        for ln in lines:
            if not ln:
                kept.append("")
                continue
            if _norm_key(ln) in repeated:
                stats["repeated_lines"] += 1
                continue
            if _PAGE_NO.match(ln):
                stats["page_numbers"] += 1
                continue
            if _DOT_ONLY.match(ln):
                stats["dot_leader_lines"] += 1
                continue
            new, n = _DOT_RUN.subn(" ", ln)
            stats["dot_runs"] += n
            kept.append(re.sub(r"[ \t]{2,}", " ", new).strip())
        page = "\n".join(kept)
        page = _SOFT_WRAP.sub(r"\1-\2", page)  # keep the hyphen: "Slow-\nroasted" -> "Slow-roasted"
        page = re.sub(r"\n{3,}", "\n\n", page).strip()
        cleaned_pages.append(page)

    text = ""
    spans: list[tuple[int, int, int]] = []
    for i, page in enumerate(cleaned_pages, start=1):
        if text:
            text += "\n\n"
        start = len(text)
        text += page
        spans.append((start, len(text), i))
    return PdfCleanResult(text=text + "\n", page_spans=spans, stats=stats)
