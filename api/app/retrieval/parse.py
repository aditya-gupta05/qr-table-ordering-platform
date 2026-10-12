"""Load corpus files into ParsedDoc (cleaned text + metadata). Pure, no DB."""

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader

from app.retrieval import PIPELINE_VERSION
from app.retrieval.clean import clean_markdown, clean_pdf_pages

# file stem -> (slug, source_type). Anything not listed is rejected so a stray file is loud.
SOURCES: dict[str, str] = {
    "faq": "faq",
    "policies": "policy",
    "allergen-kitchen-practices": "allergen_practices",
    "dish-guide": "dish_guide",
    "about-sourcing": "about",
    "menu": "menu_pdf",
}
SOURCE_TYPES = tuple(SOURCES.values())


@dataclass
class ParsedDoc:
    slug: str
    source_type: str
    title: str
    uri: str
    content: str  # cleaned
    content_hash: str
    metadata: dict = field(default_factory=dict)


def content_hash(text: str) -> str:
    return hashlib.sha256((text + "\x00" + PIPELINE_VERSION).encode()).hexdigest()


def _md_title(text: str, fallback: str) -> str:
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else fallback


def parse_markdown(path: Path, source_type: str) -> ParsedDoc:
    text = clean_markdown(path.read_text(encoding="utf-8"))
    return ParsedDoc(
        slug=path.stem,
        source_type=source_type,
        title=_md_title(text, path.stem),
        uri=f"corpus/{path.name}",
        content=text,
        content_hash=content_hash(text),
        metadata={"format": "markdown"},
    )


def extract_pdf_pages(path: Path) -> list[str]:
    return [p.extract_text() or "" for p in PdfReader(str(path)).pages]


def parse_pdf(path: Path, source_type: str, clean: bool = True) -> ParsedDoc:
    pages = extract_pdf_pages(path)
    if clean:
        res = clean_pdf_pages(pages)
        text, spans, stats = res.text, res.page_spans, res.stats
    else:  # raw baseline for the "cleaning matters" measurement
        text, spans, stats = "", [], {}
        for i, p in enumerate(pages, start=1):
            if text:
                text += "\n\n"
            spans.append((len(text), len(text) + len(p), i))
            text += p
    return ParsedDoc(
        slug="menu-pdf",
        source_type=source_type,
        title="Lantern Kitchen printed menu (PDF)",
        uri=f"corpus/{path.name}",
        content=text,
        content_hash=content_hash(text),
        metadata={
            "format": "pdf",
            "pages": len(pages),
            "page_spans": spans,
            "clean_stats": stats,
            "cleaned": clean,
        },
    )


def load_corpus(corpus_dir: Path) -> list[ParsedDoc]:
    docs: list[ParsedDoc] = []
    for path in sorted(corpus_dir.iterdir()):
        if path.suffix not in (".md", ".pdf"):
            continue
        source_type = SOURCES.get(path.stem)
        if source_type is None:
            raise ValueError(f"unknown corpus file {path.name}; add it to parse.SOURCES")
        docs.append(
            parse_pdf(path, source_type)
            if path.suffix == ".pdf"
            else parse_markdown(path, source_type)
        )
    return docs
