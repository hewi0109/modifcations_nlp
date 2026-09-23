"""
PDF report text extraction
==========================

Reads colonoscopy report PDFs and writes the raw report text, one row per
report, for loading into the backend table.

Tries the PDF's embedded text layer first; falls back to OCR (Poppler's
pdftoppm + Tesseract) for scanned reports. Both tools run locally — no report
text leaves the machine.
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def normalise_text(text: str) -> str:
    text = re.sub(r"_x000D_|_x000A_|\r", "\n", str(text or ""), flags=re.I)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_embedded_text(pdf: Path) -> str:
    from pypdf import PdfReader
    return normalise_text("\n\n".join(p.extract_text() or "" for p in PdfReader(str(pdf)).pages))


def ocr(pdf: Path, tesseract: str, pdftoppm: str, dpi: int, psm: str) -> str:
    with tempfile.TemporaryDirectory(prefix="pdf_ocr_") as tmp:
        subprocess.run([pdftoppm, "-png", "-r", str(dpi), str(pdf), str(Path(tmp) / "page")],
                       check=True, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
        pages = []
        for img in sorted(Path(tmp).glob("page-*.png")):
            r = subprocess.run([tesseract, str(img), "stdout", "-l", "eng", "--psm", psm],
                               check=True, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            pages.append(r.stdout)
    return normalise_text("\n\n".join(pages))


def extract(pdf: Path, tesseract: str, pdftoppm: str, dpi: int, psm: str,
            min_chars: int = 80) -> tuple[str, str]:
    """Return (text, method)."""
    embedded = read_embedded_text(pdf)
    # A scan with a digital header stamped on top can clear the character
    # threshold on header text alone, so require a section header too.
    if len(re.sub(r"\s+", "", embedded)) >= min_chars and re.search(r"\bFindings?\s*:", embedded, re.I):
        return embedded, "embedded_text"
    if not (tesseract and pdftoppm):
        raise RuntimeError(f"{pdf.name} needs OCR, but Tesseract/Poppler were not found on PATH")
    return ocr(pdf, tesseract, pdftoppm, dpi, psm), "ocr_tesseract"


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract raw report text from colonoscopy PDFs")
    ap.add_argument("input", help="A PDF file, or a folder of PDFs")
    ap.add_argument("--output", default="data/output/report_text.csv")
    ap.add_argument("--tesseract", default="")
    ap.add_argument("--pdftoppm", default="")
    ap.add_argument("--ocr-dpi", type=int, default=300)
    ap.add_argument("--ocr-psm", default="6")
    args = ap.parse_args()

    tesseract = args.tesseract or shutil.which("tesseract") or ""
    pdftoppm = args.pdftoppm or shutil.which("pdftoppm") or ""

    src = Path(args.input).expanduser()
    pdfs = sorted(p for p in src.iterdir() if p.suffix.lower() == ".pdf") if src.is_dir() else [src]
    if not pdfs:
        sys.exit(f"No PDFs found in {src}")

    rows = []
    for pdf in pdfs:
        try:
            text, method = extract(pdf, tesseract, pdftoppm, args.ocr_dpi, args.ocr_psm)
            rows.append({"source_file": pdf.name, "extraction_method": method,
                         "char_count": len(text), "endoscopy_report_text": text})
            print(f"{pdf.name:45} {method:15} {len(text):>7} chars")
        except Exception as exc:
            rows.append({"source_file": pdf.name, "extraction_method": "FAILED",
                         "char_count": 0, "endoscopy_report_text": ""})
            print(f"{pdf.name:45} FAILED: {exc}", file=sys.stderr)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["source_file", "extraction_method",
                                           "char_count", "endoscopy_report_text"])
        w.writeheader()
        w.writerows(rows)
    print(f"\n{len(rows)} rows -> {out}")

if __name__ == "__main__":
    main()