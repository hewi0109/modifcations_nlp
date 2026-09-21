# PDF file integration

Reads private-clinic / external-facility reports supplied as PDFs and produces the same
Procedure, Findings and Indications output as the Excel pipeline.

Based on Muktar's `pdf_report_processing_v01.py`, retargeted from the single-file v11
script to the current modular architecture.

## How it works

1. Tries the PDF's embedded text layer first (`pypdf`).
2. If there is too little text, or the `Findings:` / `Impression:` headers are missing,
   falls back to OCR — pages are rendered to PNG with `pdftoppm`, then read by Tesseract.
3. Endoscopy and pathology always arrive in the same PDF, so the extracted text is passed
   to both extractors, then through `merge` and `excel_out` as normal.
4. The extracted text is always written alongside the workbook as `<output>.source.txt`.
   With OCR in the chain this is the only record of what the pipeline actually read.

The header check in step 2 matters. Several of these PDFs are scans with a small digital
header stamped on top; counting characters alone would accept that header as a full report
and silently produce a row of "Not documented" with no error.

## Prerequisites

`pip install pypdf` 

Two system binaries, neither a pip package:

- **Tesseract** — https://github.com/UB-Mannheim/tesseract/wiki
- **Poppler** — https://github.com/oschwartz10612/poppler-windows/releases

Install both, then add the **folders containing the executables** to your user PATH
(not system — no admin needed):

    <tesseract folder>
    <poppler folder>\Library\bin

PATH entries are directories, not paths to the .exe files.

Windows only gives the new PATH to processes started after the change, so **fully restart
PyCharm** — closing the terminal tab is not enough. Then confirm what Python sees:

    python -c "import shutil; print(shutil.which('tesseract'), shutil.which('pdftoppm'))"

Two paths printed means it is working. Two `None` means PATH has not propagated — either
restart again, or pass `--tesseract` and `--pdftoppm` with the full paths to the
executables, which bypasses PATH entirely.

Note the Poppler PATH entry contains the version number, so it breaks on upgrade.

## Running

The script lives in `pdf/`, so run it as a module from the project root. Running
`python pdf/pdf_ingest.py` directly fails with `ModuleNotFoundError: No module named
'textClean'`, because that puts `pdf/` on the import path instead of the project root.

    cd C:\Users\tallen09\PycharmProjects\modifcations_nlp ## add in your own path, this is mine
    python -m pdf.pdf_ingest --endoscopy-pdf "data\input\pdf\{file name}.pdf"

Every PDF in a folder (PowerShell):

    Get-ChildItem "data\input\pdf\*.pdf" | ForEach-Object {
        python -m pdf.pdf_ingest --endoscopy-pdf $_.FullName --output "data\output\$($_.BaseName).xlsx"
    }
note that this group method prints an individual excel file for EACH pdf, this needs fixing 

### Flags

| Flag | Purpose |
|---|---|
| `--endoscopy-pdf` | The report PDF (required, one file per run) |
| `--output` | Output workbook path |
| `--endoscopy-text-file` | Re-run extraction on already-saved text, skipping OCR |
| `--save-extracted-text` | Write the text somewhere other than beside the workbook |
| `--force-ocr` | Skip the embedded-text check and OCR regardless |
| `--ocr-psm` | Tesseract page segmentation: `6` (default) single-column, `3` two-column |
| `--ocr-dpi` | Render DPI, default 300 |
| `--tesseract` / `--pdftoppm` | Explicit paths, when PATH is not set up |
| `--procedure-id` | Override the generated procedure_id |

### Checking the output

Read `<output>.source.txt` before trusting the workbook:

- `Findings:` and `Impression:` survived OCR
- sizes are intact — `5 mm`, not `S mm`
- Paris classifications are intact — `IIa`, not `lla`
- on pathology pages, sentences read straight through rather than columns being
  interleaved (if interleaved, rerun with `--ocr-psm 3`)

A corrupted section header makes the whole section invisible to the extractor, and the
result is "Not documented" rather than an error, so this check is not optional.

`--endoscopy-text-file` lets you re-run extraction against a saved text file without
paying for OCR again, which is useful while iterating.

## Current limitations

**No page routing.** The PDFs received so far are bundles — one file containing the
endoscopy report, pathology, referrals, outpatient slips, request forms and photo pages,
in a different order each time. Every page is concatenated into one string and handed to
both extractors, so a referral mentioning a diagnosis can be parsed as a pathology
specimen, and a phantom specimen can steal the site pairing from a real one in
`merge_findings`.

**Bundles may contain more than one procedure.** One sample holds both an endoscopy report
and a separate colonoscopy report. The script assumes one procedure per PDF and issues a
single `procedure_id`.

**Reports span pages.** Any future page classifier has to join contiguous runs rather than
label pages independently — a `Findings:` header on one page may have its content on the
next.

**One report per run.** No batch mode inside the script; use the PowerShell loop above.

**Location is hardcoded** to Tennyson Centre Day Hospital in `extract_pdf_metadata`. Any
other clinic returns "Not documented".

**`procedure_id` falls back to a filename hash** when the MRN and procedure date cannot be
found. That keeps rows apart but cannot be matched back to a patient — check the column
for `PDF-<hash>` values after a run.

**PDF findings use a different code path.** `extract_pdf_endoscopy_findings` re-runs the
extractor per bullet so neighbouring bullets do not donate size and count details to each
other. Row counts from the PDF route are therefore not directly comparable with the Excel
route.

## Next step

Page classification: OCR each page, label it (endoscopy / pathology / discard), join
contiguous runs, and route each to the right extractor. Pages that cannot be classified
should flag the report for review rather than being dropped silently.

