"""Render the fictional Riverside record set (backend/tests/fixtures/riverside/*.txt) into text-readable PDFs.

One PDF page per form-feed (\\f) separated page; the first line of each page is the page title.
Output: demo-data/riverside/<same name>.pdf (upload these to the configured Box folder for the live demo).

    backend/.venv/bin/python backend/scripts/make_demo_pdfs.py [--out DIR]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

BACKEND = Path(__file__).resolve().parent.parent
SRC = BACKEND / "tests" / "fixtures" / "riverside"
OUT = BACKEND.parent / "demo-data" / "riverside"

_TRANSLIT = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "−": "-", "…": "..."}


def latin1(text: str) -> str:
    t = "".join(_TRANSLIT.get(ch, ch) for ch in text)
    return t.encode("latin-1", errors="replace").decode("latin-1")


class _DemoPDF(FPDF):
    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 5, "Fictional record for the ScopeGuard demo.", align="C")


def render(pages: list[str], title: str) -> bytes:
    pdf = _DemoPDF(format="Letter")
    pdf.set_creation_date(datetime(2026, 10, 16, tzinfo=timezone.utc))  # deterministic bytes
    pdf.set_title(latin1(title))
    pdf.set_author("Fictional demo data - ScopeGuard")
    pdf.set_margins(22, 22, 22)
    pdf.set_auto_page_break(auto=True, margin=20)
    for page_text in pages:
        lines = [ln.strip() for ln in page_text.strip().splitlines() if ln.strip()]
        pdf.add_page()
        if not lines:
            continue
        pdf.set_font("Helvetica", "B", 15)
        pdf.multi_cell(pdf.epw, 8, latin1(lines[0]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(4)
        pdf.set_font("Helvetica", "", 11)
        for line in lines[1:]:
            pdf.multi_cell(pdf.epw, 6, latin1(line), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(2.5)
    return bytes(pdf.output())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    count = 0
    for src in sorted(SRC.glob("*.txt")):
        pages = src.read_text(encoding="utf-8").split("\f")
        target = args.out / (src.stem + ".pdf")
        target.write_bytes(render(pages, src.stem))
        count += 1
        print(f"{target.name}: {len(pages)} page(s)")
    print(f"Wrote {count} PDFs to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
