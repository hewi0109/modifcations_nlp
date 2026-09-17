"""
PDF report processing v1
========================

Convert private-clinic / external-facility PDF endoscopy reports into the same
Procedure, Findings, and Indications Excel output produced by
pipeline_v11_spacy_fixed.py.

The script first tries embedded PDF text. If the PDF is scanned/image-only, it
uses OCR via Tesseract after rendering pages with pdftoppm.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

DEFAULT_OUTPUT = "/Users/mahmed06/NLP/pdf_extraction_v1.xlsx"
BUNDLED_PDFTOPPM = (
    "/Users/mahmed06/NLP/"
    "dependencies/bin/pdftoppm"
)


class PdfTextExtractionError(RuntimeError):
    """Raised when a PDF cannot be converted into report text."""


def clean_identifier(value: object) -> str:
    text = "" if value is None else str(value).strip()
    text = re.sub(r"\.0$", "", text)
    return text


def normalize_report_text(text: str) -> str:
    """Normalize PDF/OCR text enough for the existing v11 extractor."""
    text = str(text or "")
    text = re.sub(r"_x000D_|_x000A_|\r", "\n", text, flags=re.I)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_embedded_pdf_text(pdf_path: Path) -> str:
    """Read embedded/selectable text from a PDF, if available."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""

    reader = PdfReader(str(pdf_path))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return normalize_report_text("\n\n".join(pages))


def find_pdftoppm(explicit_path: Optional[str] = None) -> Optional[str]:
    if explicit_path:
        return explicit_path
    found = shutil.which("pdftoppm")
    if found:
        return found
    if Path(BUNDLED_PDFTOPPM).exists():
        return BUNDLED_PDFTOPPM
    return None


def ocr_pdf_with_tesseract(
    pdf_path: Path,
    *,
    tesseract_path: Optional[str] = None,
    pdftoppm_path: Optional[str] = None,
    dpi: int = 300,
    lang: str = "eng",
) -> str:
    """OCR image-only PDFs using pdftoppm + Tesseract."""
    tesseract = tesseract_path or shutil.which("tesseract")
    pdftoppm = find_pdftoppm(pdftoppm_path)

    missing = []
    if not pdftoppm:
        missing.append("pdftoppm")
    if not tesseract:
        missing.append("tesseract")
    if missing:
        raise PdfTextExtractionError(
            "This PDF appears to be scanned/image-only, but OCR tools are missing: "
            + ", ".join(missing)
            + ". Install Tesseract OCR and Poppler, or provide pre-OCR text with "
            "--endoscopy-text-file."
        )

    with tempfile.TemporaryDirectory(prefix="pdf_report_ocr_") as tmp:
        prefix = str(Path(tmp) / "page")
        subprocess.run(
            [pdftoppm, "-png", "-r", str(dpi), str(pdf_path), prefix],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        page_images = sorted(Path(tmp).glob("page-*.png"))
        if not page_images:
            raise PdfTextExtractionError(f"No page images were rendered from {pdf_path}")

        page_texts = []
        for image_path in page_images:
            result = subprocess.run(
                [tesseract, str(image_path), "stdout", "-l", lang, "--psm", "6"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            page_texts.append(result.stdout)

    return normalize_report_text("\n\n".join(page_texts))


def extract_pdf_text(
    pdf_path: str,
    *,
    force_ocr: bool = False,
    min_text_chars: int = 80,
    tesseract_path: Optional[str] = None,
    pdftoppm_path: Optional[str] = None,
    ocr_dpi: int = 300,
    ocr_lang: str = "eng",
) -> tuple[str, str]:
    """Return (text, method) from embedded text or OCR."""
    path = Path(pdf_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)

    if not force_ocr:
        embedded = read_embedded_pdf_text(path)
        if len(re.sub(r"\s+", "", embedded)) >= min_text_chars:
            return embedded, "embedded_text"

    ocr_text = ocr_pdf_with_tesseract(
        path,
        tesseract_path=tesseract_path,
        pdftoppm_path=pdftoppm_path,
        dpi=ocr_dpi,
        lang=ocr_lang,
    )
    return ocr_text, "ocr_tesseract"


def read_text_file(path: Optional[str]) -> str:
    if not path:
        return ""
    return normalize_report_text(Path(path).expanduser().read_text(encoding="utf-8"))


def first_regex(text: str, pattern: str, default: str = "") -> str:
    m = re.search(pattern, text, re.I | re.M)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else default


def stable_pdf_procedure_id(pdf_path: Path, registration_id: str, procedure_date: str) -> str:
    date_digits = re.sub(r"\D+", "", procedure_date or "")
    if registration_id and date_digits:
        return f"PDF-{registration_id}-{date_digits}"
    digest = hashlib.sha1(str(pdf_path.resolve()).encode("utf-8")).hexdigest()[:10].upper()
    return f"PDF-{digest}"


def extract_pdf_metadata(endo_text: str, pdf_path: Path, explicit_procedure_id: str = "") -> dict:
    """Extract source metadata commonly found in private-clinic PDF reports."""
    registration_id = first_regex(endo_text, r"^\s*MRN\s*:\s*([A-Za-z0-9.-]+)")
    procedure_date = first_regex(
        endo_text,
        r"Procedure Date(?:\s+No Time)?\s*:\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4})",
    )
    procedure_type = first_regex(endo_text, r"^\s*Procedure\s*:\s*([^\n]{1,80})")
    if re.search(r"\bAfter obtaining informed consent\b", procedure_type, re.I):
        procedure_type = ""

    location = "Not documented"
    if re.search(r"Tennyson Centre\s+Day Hospital", endo_text, re.I):
        location = "Tennyson Centre Day Hospital"

    procedure_id = explicit_procedure_id.strip() or stable_pdf_procedure_id(
        pdf_path,
        registration_id,
        procedure_date,
    )

    return {
        "registration_id": registration_id,
        "procedure_id": procedure_id,
        "procedure_finding_id": "",
        "procedure_date": procedure_date,
        "procedure_type": procedure_type,
        "procedure_location": location,
    }


def clean_pdf_provider_name(text: str) -> str:
    text = re.sub(r"\([^)]*\)", " ", str(text or ""))
    text = re.split(
        r"\b(?:Date of Birth|DOB|Patient Name|Gender|Age|MRN|Medicare|Room|Note Status)\b\s*:?",
        text,
        maxsplit=1,
        flags=re.I,
    )[0]
    text = re.sub(
        r"\s*,\s*(?:Consultant|Colorectal Consultant|Colorectal Surgeon|General Surgeon|"
        r"Gastroenterologist|Anaesthesiologist|Anesthesiologist|Fellow|MD|FRACS|MBBS)\b.*$",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"\b(?:Dr|Doctor)\.?\s+", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" ,-")
    return text


def extract_pdf_provider_override(endo_text: str) -> str:
    """Prefer the endoscopist in private-clinic PDF layouts.

    Some PDF reports list an anaesthetist first in "Providers:" and the
    endoscopist second or in the footer signature. This keeps the Excel-derived
    v11 provider logic untouched while improving PDF-only report handling.
    """
    text = normalize_report_text(endo_text)

    # Footer signature pattern, e.g. "Dr. Justin Baker" followed by a signed
    # name/date block. This is usually the report author/endoscopist.
    footer_matches = re.findall(
        r"^\s*(?:Dr|Doctor)\.?\s+([A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+){1,3})\s*$",
        text,
        flags=re.I | re.M,
    )
    if footer_matches:
        return clean_pdf_provider_name(footer_matches[-1])

    providers = first_regex(text, r"^\s*Providers?\s*:\s*([^\n]+)")
    if providers:
        parts = [clean_pdf_provider_name(part) for part in providers.split(",")]
        parts = [
            part for part in parts
            if part and not re.search(r"\b(?:anaesthesiologist|anesthesiologist)\b", part, re.I)
        ]
        if parts:
            return parts[-1]

    return ""


def extract_pdf_endoscopy_findings(endo_text: str):
    """Extract findings from PDF reports while preserving bullet boundaries."""
    text = normalize_report_text(endo_text)
    m = re.search(
        r"Findings?\s*:\s*(.*?)(?=\n?\s*(?:Impression|Recommendation|Scope Withdrawal Time|Images)\s*:|$)",
        text,
        re.I | re.S,
    )
    if not m:
        return extract_endoscopy_findings(text)

    findings_block = m.group(1).strip()
    bullet_blocks = [
        block.strip()
        for block in re.split(r"(?:^|\n)\s*[-•]\s+", findings_block)
        if len(block.strip()) > 15
    ]
    if len(bullet_blocks) <= 1:
        return extract_endoscopy_findings(text)

    rows = []
    for block in bullet_blocks:
        # Run the stable v11 extractor per bullet so neighbouring PDF bullet
        # findings do not donate count/size/site details to each other.
        rows.extend(extract_endoscopy_findings(f"Findings:\n- {block}\nImpression:"))

    return rows or extract_endoscopy_findings(text)


def build_rows_from_report(
    endoscopy_report_text: str,
    pathology_report_text: str,
    metadata: dict,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Run the v11 extractor on one PDF-derived report."""
    endo = normalize_report_text(endoscopy_report_text)
    path = normalize_report_text(pathology_report_text)

    procedure_id = metadata["procedure_id"]
    registration_id = metadata.get("registration_id", "")
    procedure_finding_id = metadata.get("procedure_finding_id", "")
    procedure_date = metadata.get("procedure_date", "")
    procedure_type = metadata.get("procedure_type", "")
    procedure_location = metadata.get("procedure_location", "")

    no_endoscopy_report = is_missing_report_text(endo)
    proc = extract_procedure(endo, procedure_id)
    proc["procedure_id"] = procedure_id
    proc["procedure_finding_id"] = procedure_finding_id
    proc["registration_id"] = registration_id
    proc["procedure_date"] = procedure_date
    proc["procedure_type"] = procedure_type
    proc["procedure_location"] = procedure_location
    proc["pathology_report_text"] = path
    proc["endoscopy_report_text"] = endo
    proc["other_findings"] = "No report" if no_endoscopy_report else extract_other_findings(endo)
    apply_procedure_type_overrides(proc, procedure_type)
    provider_override = extract_pdf_provider_override(endo)
    if provider_override:
        proc["procedure_provider"] = provider_override

    findings = extract_pdf_endoscopy_findings(endo) if not no_endoscopy_report else []
    specimens = extract_pathology_specimens(path) if path else []
    finding_rows = merge_findings(findings, specimens, procedure_id)

    for row in finding_rows:
        row["registration_id"] = registration_id
        row["procedure_id"] = procedure_id
        row["procedure_finding_id"] = procedure_finding_id
        row["procedure_date"] = procedure_date
        row["procedure_type"] = procedure_type
        row["procedure_location"] = procedure_location
        row["pathology_report_text"] = path
        row["endoscopy_report_text"] = endo
        row["other_findings"] = proc.get("other_findings", "None")
        annotate_finding_row_flags(row, endo, path)

    indication_rows = build_indication_rows(
        endo,
        registration_id=registration_id,
        procedure_id=procedure_id,
        procedure_finding_id=procedure_finding_id,
        procedure_date=procedure_date,
        procedure_type=procedure_type,
        procedure_location=procedure_location,
    )

    return [proc], finding_rows, indication_rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="v1 PDF endoscopy/pathology report extraction wrapper for pipeline_v11_spacy_fixed.py"
    )
    parser.add_argument("--endoscopy-pdf", default="Test pdf.pdf", help="Endoscopy report PDF path")
    parser.add_argument("--pathology-pdf", default="", help="Optional pathology report PDF path")
    parser.add_argument("--endoscopy-text-file", default="", help="Optional pre-OCR endoscopy text file")
    parser.add_argument("--pathology-text-file", default="", help="Optional pre-OCR pathology text file")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output Excel workbook path")
    parser.add_argument("--procedure-id", default="", help="Optional procedure_id override")
    parser.add_argument("--force-ocr", action="store_true", help="Force OCR even when embedded PDF text exists")
    parser.add_argument("--tesseract", default="", help="Optional explicit path to tesseract")
    parser.add_argument("--pdftoppm", default="", help="Optional explicit path to pdftoppm")
    parser.add_argument("--ocr-dpi", type=int, default=300, help="OCR render DPI")
    parser.add_argument("--ocr-lang", default="eng", help="Tesseract OCR language")
    parser.add_argument("--save-extracted-text", default="", help="Optional path to save extracted PDF text")
    args = parser.parse_args()

    endoscopy_pdf = Path(args.endoscopy_pdf).expanduser().resolve()

    try:
        endoscopy_text = read_text_file(args.endoscopy_text_file)
        endoscopy_method = "text_file"
        if not endoscopy_text:
            endoscopy_text, endoscopy_method = extract_pdf_text(
                str(endoscopy_pdf),
                force_ocr=args.force_ocr,
                tesseract_path=args.tesseract or None,
                pdftoppm_path=args.pdftoppm or None,
                ocr_dpi=args.ocr_dpi,
                ocr_lang=args.ocr_lang,
            )

        pathology_text = read_text_file(args.pathology_text_file)
        pathology_method = "none"
        if args.pathology_pdf and not pathology_text:
            pathology_text, pathology_method = extract_pdf_text(
                args.pathology_pdf,
                force_ocr=args.force_ocr,
                tesseract_path=args.tesseract or None,
                pdftoppm_path=args.pdftoppm or None,
                ocr_dpi=args.ocr_dpi,
                ocr_lang=args.ocr_lang,
            )
        elif pathology_text:
            pathology_method = "text_file"
    except PdfTextExtractionError as exc:
        print(f"PDF text extraction failed: {exc}", file=sys.stderr)
        print(
            "For scanned PDFs on macOS, install OCR with: brew install tesseract poppler",
            file=sys.stderr,
        )
        sys.exit(2)

    if args.save_extracted_text:
        combined = (
            f"--- ENDOSCOPY TEXT ({endoscopy_method}) ---\n{endoscopy_text}\n\n"
            f"--- PATHOLOGY TEXT ({pathology_method}) ---\n{pathology_text}\n"
        )
        Path(args.save_extracted_text).expanduser().write_text(combined, encoding="utf-8")

    metadata = extract_pdf_metadata(endoscopy_text, endoscopy_pdf, args.procedure_id)
    proc_rows, finding_rows, indication_rows = build_rows_from_report(
        endoscopy_text,
        pathology_text,
        metadata,
    )
    build_excel_multi(proc_rows, finding_rows, args.output, indication_rows)

    print(f"Endoscopy text method: {endoscopy_method}")
    print(f"Pathology text method : {pathology_method}")
    print(f"Procedure rows        : {len(proc_rows)}")
    print(f"Finding rows          : {len(finding_rows)}")
    print(f"Indication rows       : {len(indication_rows)}")
    print(f"Output                : {args.output}")


if __name__ == "__main__":
    main()
