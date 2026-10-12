"""Generate api/corpus/menu.pdf from api/seed/menu.json plus PDF-only sections.

Run: cd api && uv run python ../scripts/gen_menu_pdf.py

The layout deliberately has the extraction pitfalls a real printed menu has, so ingestion cleaning
is exercised: two-column frames, a two-dish-per-row table, a running header and "Page N of M"
footer, dot leaders between names and prices, and an allergy box drawn in `onPage` (so it comes
first in the content stream although it sits at the bottom of page 1).
Output is byte-stable (reportlab invariant=1).
"""

import json
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parent.parent
MENU = ROOT / "api" / "seed" / "menu.json"
OUT = ROOT / "api" / "corpus" / "menu.pdf"
RESTAURANT = "Lantern Kitchen"
W, H = letter
MARGIN = 0.6 * inch
GUTTER = 0.3 * inch
TOP = H - 0.95 * inch
BOTTOM = 0.9 * inch
COL_W = (W - 2 * MARGIN - GUTTER) / 2

AMBER = HexColor("#9a4a12")
INK = HexColor("#222222")
body = ParagraphStyle("body", fontName="Helvetica", fontSize=8.6, leading=11, textColor=INK)
small = ParagraphStyle("small", parent=body, fontSize=7.8, leading=10)
h1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=26, leading=30, textColor=AMBER)
h2 = ParagraphStyle(
    "h2", fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=AMBER, spaceBefore=8
)
h3 = ParagraphStyle("h3", fontName="Helvetica-Bold", fontSize=9.6, leading=12, spaceBefore=6)

TAG_CODES = {
    "vegan": "VG",
    "vegetarian": "V",
    "gluten_free": "GF",
    "halal": "H",
    "dairy_free": "DF",
}


def money(cents: int) -> str:
    return f"${cents / 100:.2f}"


class DotLeader(Flowable):
    """`Name ........ $11.50` on one line (dots are real text, so extractors see them)."""

    def __init__(self, name: str, price: str, width: float, tags: str = ""):
        super().__init__()
        self.name, self.price, self.tags, self.w = name, price, tags, width
        self.height = 13

    def wrap(self, aw, ah):
        return self.w, self.height

    def draw(self):
        c = self.canv
        c.setFont("Helvetica-Bold", 9.6)
        c.drawString(0, 2, self.name)
        nw = stringWidth(self.name, "Helvetica-Bold", 9.6)
        c.setFont("Helvetica-Bold", 9.6)
        c.drawRightString(self.w, 2, self.price)
        pw = stringWidth(self.price, "Helvetica-Bold", 9.6)
        x0 = nw + 4
        if self.tags:
            c.setFont("Helvetica", 7)
            c.drawString(x0, 2, self.tags)
            x0 += stringWidth(self.tags, "Helvetica", 7) + 4
        dot_w = stringWidth(". ", "Helvetica", 8)
        n = int((self.w - pw - 4 - x0) / dot_w)
        if n > 0:
            c.setFont("Helvetica", 8)
            c.drawString(x0, 2, ". " * n)


class NumberedCanvas(Canvas):
    """Two-pass canvas so the running footer can say `Page N of M`."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._saved: list[dict] = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self.setFont("Helvetica", 7.5)
            self.setFillColor(INK)
            self.drawString(
                MARGIN, 0.5 * inch, f"{RESTAURANT}  |  88 Harbour Street  |  (555) 014-2290"
            )
            self.drawRightString(W - MARGIN, 0.5 * inch, f"Page {self._pageNumber} of {total}")
            super().showPage()
        super().save()


def on_page(canv, doc):
    """Runs before the page's flowables: running header, and (page 1) the allergy box."""
    canv.saveState()
    canv.setFillColor(AMBER)
    canv.setFont("Helvetica-Bold", 8)
    canv.drawString(MARGIN, H - 0.55 * inch, f"{RESTAURANT.upper()}  -  SEASONAL MENU")
    canv.setStrokeColor(AMBER)
    canv.line(MARGIN, H - 0.62 * inch, W - MARGIN, H - 0.62 * inch)
    if doc.page == 1:
        # Drawn here, so it is first in the content stream but sits at the bottom of the page.
        bx, by, bw, bh = MARGIN, 0.72 * inch, W - 2 * MARGIN, 0.5 * inch
        canv.setStrokeColor(AMBER)
        canv.rect(bx, by, bw, bh)
        canv.setFillColor(INK)
        canv.setFont("Helvetica-Bold", 7.6)
        canv.drawString(bx + 6, by + bh - 12, "ALLERGY NOTICE")
        canv.setFont("Helvetica", 7.4)
        canv.drawString(
            bx + 6,
            by + bh - 23,
            "Please tell your server about any allergy before you order. Our kitchen handles all",
        )
        canv.drawString(
            bx + 6,
            by + bh - 33,
            "major allergens and cannot guarantee that any dish is free of traces. "
            "Allergen details are on the ordering menu.",
        )
    canv.restoreState()


def item_block(item: dict, width: float):
    tags = " ".join(TAG_CODES[t] for t in item["dietary_tags"] if t in TAG_CODES)
    spice = "  Spice: " + "*" * item["spice_level"] if item["spice_level"] else ""
    return KeepTogether(
        [
            DotLeader(item["name"], money(item["price_cents"]), width, tags),
            Paragraph(item["description"] + spice, body),
            Spacer(1, 4),
        ]
    )


def build() -> None:
    menu = json.loads(MENU.read_text())
    cats = {c["name"]: c for c in menu["categories"]}

    cols_y0, cols_y1 = BOTTOM, TOP
    p1_y0 = 1.35 * inch  # leave room for the allergy box
    title_h = 1.5 * inch
    frames_p1 = [
        Frame(MARGIN, TOP - title_h, W - 2 * MARGIN, title_h, id="title", showBoundary=0),
        Frame(MARGIN, p1_y0, COL_W, TOP - title_h - p1_y0, id="p1l"),
        Frame(MARGIN + COL_W + GUTTER, p1_y0, COL_W, TOP - title_h - p1_y0, id="p1r"),
    ]
    frames_cols = [
        Frame(MARGIN, cols_y0, COL_W, cols_y1 - cols_y0, id="l"),
        Frame(MARGIN + COL_W + GUTTER, cols_y0, COL_W, cols_y1 - cols_y0, id="r"),
    ]
    frames_wide = [Frame(MARGIN, cols_y0, W - 2 * MARGIN, cols_y1 - cols_y0, id="wide")]

    doc = BaseDocTemplate(
        str(OUT),
        pagesize=letter,
        title=f"{RESTAURANT} Seasonal Menu",
        author=RESTAURANT,
        invariant=1,
        pageTemplates=[
            PageTemplate(id="first", frames=frames_p1, onPage=on_page, autoNextPageTemplate="cols"),
            PageTemplate(id="cols", frames=frames_cols, onPage=on_page),
            PageTemplate(id="wide", frames=frames_wide, onPage=on_page),
        ],
    )

    s: list = []
    s += [
        Paragraph(RESTAURANT, h1),
        Paragraph(
            "Seasonal menu. Wood-fired and wok-cooked food from across Asia and the Mediterranean. "
            "Prices in US dollars and include tax. Dietary key: V vegetarian, VG vegan, "
            "GF gluten free, H halal, DF dairy free. Spice: * gentle, ** spicy, *** hot. "
            "Order from the QR code on your table or from any member of staff.",
            body,
        ),
        Spacer(1, 6),
    ]
    # Fill the title frame, then flow into the columns.
    s.append(Spacer(1, title_h))

    def category(name: str):
        out = [Paragraph(name, h2)]
        out += [item_block(i, COL_W) for i in cats[name]["items"]]
        return out

    for name in ("Starters", "Bowls & Curries"):
        s += category(name)
    s.append(NextPageTemplate("cols"))
    s.append(PageBreak())
    for name in ("Mains", "Sides"):
        s += category(name)

    # Page 3: full-width, two dishes per table row (row-major extraction interleaves the dishes).
    s.append(NextPageTemplate("wide"))
    s.append(PageBreak())
    for name in ("Desserts", "Drinks"):
        s.append(Paragraph(name, h2))
        items = cats[name]["items"]
        rows = []
        cell_w = (W - 2 * MARGIN) / 2 - 8
        for i in range(0, len(items), 2):
            row = []
            for it in items[i : i + 2]:
                row.append(
                    [
                        DotLeader(it["name"], money(it["price_cents"]), cell_w - 10),
                        Paragraph(it["description"], body),
                    ]
                )
            if len(row) == 1:
                row.append("")
            rows.append(row)
        t = Table(rows, colWidths=[(W - 2 * MARGIN) / 2] * 2)
        t.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.25, HexColor("#cccccc")),
                ]
            )
        )
        s.append(t)

    s.append(Paragraph("Set lunch", h2))
    s.append(
        Paragraph(
            "Available Monday to Friday, 11:30 to 14:30. Two courses for $24.00: choose a "
            "starter and a main from the lists below, and a glass of Iced Jasmine Tea or "
            "Fresh Lemonade is included. Add any dessert for $5.00. The set lunch is ordered "
            "from your server and is not on the QR menu. "
            "Set lunch starters: Edamame, Tomato Basil Soup, Butternut Squash Soup, Roasted "
            "Cauliflower Hummus. Set lunch mains: Margherita Pizza, Mushroom Risotto, Spicy "
            "Tofu Stir-Fry, Lemon Herb Roast Chicken. Substitutions are not available on the "
            "set lunch, and it cannot be combined with other offers.",
            body,
        )
    )

    # Page 4: two-column pairings + chef's notes (PDF-only content).
    s.append(NextPageTemplate("cols"))
    s.append(PageBreak())
    s.append(Paragraph("Wine pairings", h2))
    s.append(
        Paragraph(
            "Our house wine is a Pinot Grigio, poured by the glass for $11.00. Its crisp, dry "
            "style is the pairing we suggest for most of the menu. Guests bringing their own "
            "bottle (corkage $25.00 per bottle) may like these pairings.",
            body,
        )
    )
    pairings = [
        (
            "Crispy Calamari",
            "Pinot Grigio, or any crisp, dry white. Acidity cuts through the fried batter.",
        ),
        (
            "Burrata & Heirloom Tomato",
            "A light, unoaked white such as Vermentino, or the house Pinot Grigio.",
        ),
        (
            "Miso Glazed Salmon",
            "A dry Riesling or a light Pinot Noir. Both handle the sweetness of the miso glaze.",
        ),
        (
            "Thai Green Curry",
            "An off-dry Riesling. A little sweetness calms the chilli heat. Skip heavy reds.",
        ),
        ("Chicken Tikka Masala", "A fruity rose or a lightly chilled Grenache."),
        ("Mushroom Risotto", "A medium-bodied Chardonnay or a Pinot Noir with earthy notes."),
        ("Lamb Kofta Plate", "A Syrah or Grenache blend. Spice and charcoal suit a ripe red."),
        ("Margherita Pizza", "A bright Sangiovese or a sparkling Prosecco."),
        ("Fish and Chips", "Pinot Grigio or a dry sparkling wine. Salt and fat love bubbles."),
        ("Pistachio Kulfi", "A late-harvest Moscato or a glass of cardamom tea."),
    ]
    for dish, note in pairings:
        s.append(Paragraph(f"<b>{dish}.</b> {note}", body))
        s.append(Spacer(1, 3))
    s.append(Spacer(1, 6))
    s.append(Paragraph("From the chef", h2))
    s.append(
        Paragraph(
            "Chef Mei Lin Tan's notes for this season. I wrote this menu for sharing: order a "
            "few starters, one or two bowls or mains, a side of rice, and let everything land "
            "in the middle of the table. A table of four usually does well with three starters, "
            "three mains and two sides.",
            body,
        )
    )
    s.append(Spacer(1, 4))
    s.append(
        Paragraph(
            "Start with the Chicken Satay or its plant-based sibling, the Satay Tofu Skewers, "
            "then take a curry or the Pad Thai for the middle of the meal. If you like heat, "
            "the Spicy Tofu Stir-Fry and the Thai Green Curry are the hottest dishes I cook. "
            "If you do not, the Chicken Tikka Masala is a warm, rich dish with a gentler spice. "
            "For a quiet dinner, the Lemon Herb Roast Chicken from the wood-fired oven is the "
            "dish I would cook at home.",
            body,
        )
    )
    s.append(Spacer(1, 4))
    s.append(
        Paragraph(
            "Dessert is Priya's territory. The Pistachio Kulfi is made the traditional way, "
            "slowly, with cardamom. The Spiced Apple Crumble is what I want on a cold night. "
            "Tell us if you are celebrating and we will add a candle. Allergies: please talk "
            "to us. I would much rather spend two minutes at your table than have you worry.",
            body,
        )
    )
    s.append(Spacer(1, 4))
    s.append(
        Paragraph(
            "Chef's tasting plate, for tables of two or more, is available on request at $38.00 "
            "per person. It is a seven-dish sequence chosen by the kitchen and cannot be "
            "ordered through the QR menu. Please tell us about dietary needs when you ask.",
            body,
        )
    )

    # Page 5-ish filler that is PDF-only: group feast, non-alcoholic pairings, how-to-order notes.
    s.append(Spacer(1, 8))
    s.append(Paragraph("The Lantern Feast", h2))
    s.append(
        Paragraph(
            "A sharing menu for the whole table, $42.00 per person, for tables of four or more "
            "and only if every guest at the table joins. It is served in three waves. First "
            "come Edamame, Chicken Satay and Crispy Calamari for the middle of the table. "
            "The second wave is Thai Green Curry, Lamb Kofta Plate and Garlic Greens with "
            "Steamed Jasmine Rice, and the last wave is a dessert for each guest chosen from "
            "Mango Sorbet, Coconut Rice Pudding or Spiced Apple Crumble. The Feast is ordered "
            "with your server and is not on the QR menu. Please tell us about every allergy "
            "and dietary need when you order it, because the dishes are fixed and we cannot "
            "adapt the whole sequence for one guest. Vegetarian and vegan versions of the "
            "Feast replace the meat dishes with Satay Tofu Skewers, Spicy Tofu Stir-Fry and "
            "Vegan Buddha Bowl at the same price.",
            body,
        )
    )
    s.append(Paragraph("Without wine", h2))
    s.append(
        Paragraph(
            "Not everyone drinks wine, and the food does not need it. Fresh Lemonade with its "
            "sharp mint cuts through the fried dishes. Mango Lassi is the classic partner to "
            "the hottest curries because the milk fat calms the chilli. Iced Jasmine Tea is our "
            "all-rounder and works from the first starter to the last dessert. Ginger Beer has "
            "enough bite to match the Pork Belly Bao and the Short Rib Rice Bowl. The Oat Milk "
            "Latte is the one to order with dessert.",
            body,
        )
    )
    s.append(Paragraph("How to order", h2))
    s.append(
        Paragraph(
            "Scan the QR code on your table to browse and order from your phone, or ask any "
            "member of staff. Food arrives as it is ready, so starters and sides may come "
            "together. Tell us if you would like dishes to come out in a particular order. "
            "Orders from the QR menu are sent to the kitchen straight away, and you can place "
            "more orders at any time. Payment is at the end of your meal at the table. "
            "Parties of eight or more have an automatic 18% gratuity. We are open Monday to "
            "Thursday 11:30 to 22:00, Friday and Saturday 11:30 to 23:00 and Sunday 12:00 to "
            "21:00, with last food orders 30 minutes before closing.",
            body,
        )
    )
    s.append(Paragraph("A note on spice", h2))
    s.append(
        Paragraph(
            "One star is a gentle warmth, two stars are clearly spicy and three stars are hot. "
            "We cook heat to be enjoyed rather than endured, and we are always happy to bring "
            "extra rice, cucumber or a Mango Lassi. Szechuan peppercorns, used in the Spicy Tofu "
            "Stir-Fry, add a tingling numbness that is a feature of the dish and not a sign that "
            "something has gone wrong. If you would like a dish milder, tell your server when "
            "you order, since the wok is easier to adjust before the dish is cooked than after.",
            body,
        )
    )

    doc.build(s, canvasmaker=NumberedCanvas)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    build()
