# PDF report text extraction

Reads colonoscopy report PDFs and writes the raw report text, one row per report, for
loading into the backend table.

No extraction or classification happens here — the script produces text only. Everything
downstream (findings, pathology, merge, Excel output) is unchanged and unaffected.

## How it works

1. Tries the PDF's embedded text layer first, using `pypdf`. Most reports arrive this way
   and are read in a fraction of a second.
2. If there is too little text, or no `Findings:` header, falls back to OCR — pages are
   rasterised to 300 DPI PNG images by Poppler's `pdftoppm`, then read by Tesseract.
3. Writes one CSV row per report.

The header check in step 1 matters. Some scanned PDFs carry a small digital header stamped
on top of the image; a character count alone would accept that header as a full report and
produce a near-empty row with no error.

Both OCR tools run entirely locally. No report text leaves the machine at any point.

## Prerequisites

    pip install pypdf

Tesseract and Poppler are only needed for scanned PDFs. Reports with a usable text layer
never invoke them. In the current sample batch, four of seven PDFs are native and three
are scans, so both are required for a full run.

- **Tesseract OCR** — https://github.com/UB-Mannheim/tesseract/wiki
- **Poppler** (`pdftoppm`) — https://github.com/oschwartz10612/poppler-windows/releases

Neither is a pip package. Install both, then add the **folders containing the
executables** to your user PATH (not system — no admin needed):

    <tesseract folder>
    <poppler folder>\Library\bin

PATH entries are directories, not paths to the .exe files.

Windows only gives the new PATH to processes started after the change, so **fully restart
PyCharm** — closing the terminal tab is not enough. Confirm what Python sees:

    python -c "import shutil; print(shutil.which('tesseract'), shutil.which('pdftoppm'))"

Two paths printed means it is working. Two `None` means PATH has not propagated — restart
again, or pass the paths explicitly with `--tesseract` and `--pdftoppm`, which bypasses
PATH entirely.

The Poppler PATH entry contains the version number, so it breaks on upgrade.

## Running

A folder of PDFs:

    cd C:\Users\tallen09\PycharmProjects\modifcations_nlp
    python pdf_text.py "data\input\pdf"

A single PDF:

    python pdf_text.py "data\input\pdf\208903600200.pdf"

With explicit OCR paths, when PATH is not set up:

    python pdf_text.py "data\input\pdf" --tesseract "<path>\tesseract.exe" --pdftoppm "<path>\Library\bin\pdftoppm.exe"

### Flags

| Flag | Purpose |
|---|---|
| *(positional)* | A PDF file, or a folder of PDFs |
| `--output` | CSV path, default `data/output/report_text.csv` |
| `--tesseract` / `--pdftoppm` | Explicit paths, when PATH is not set up |
| `--ocr-dpi` | Render DPI, default 300. Below ~200 accuracy drops sharply |
| `--ocr-psm` | Tesseract page segmentation: `6` (default) single-column, `3` two-column |

### Output

CSV rather than xlsx, because Excel caps a cell at 32,767 characters and a multi-page
OCR'd report can exceed that. Written as `utf-8-sig` so it opens correctly on a
double-click.

| Column | Contents |
|---|---|
| `source_file` | PDF filename |
| `extraction_method` | `embedded_text`, `ocr_tesseract`, or `FAILED` |
| `char_count` | Length of the extracted text |
| `endoscopy_report_text` | The report text |

The column name matches the main pipeline's input convention, so this CSV can also be fed
to `main.py` if that is ever wanted.

A report that cannot be read still gets a row, with `FAILED` and empty text, so nothing
disappears silently between the folder and the table. The reason is printed to stderr.

### Checking the output

`char_count` is the quickest sanity check — a report returning a few hundred characters
when the others return thousands usually means only a header was captured.

Then read one full `endoscopy_report_text` cell per batch. Two things go wrong even with a
clean native text layer:

- **Reading order** — a two-column layout can come out interleaved, because `extract_text`
  follows the PDF's internal ordering rather than the visual layout.
- **Missing sections** — text held in form fields or annotations is not returned.

For OCR'd reports, also check that sizes (`5 mm`, not `S mm`) and Paris classifications
(`IIa`, not `lla`) survived.

## Notes

The PDFs seen so far are sometimes bundles — one file containing the colonoscopy report
plus referrals, outpatient slips, request forms and photo pages, in a different order each
time. Since this script produces raw text rather than extracted fields, all of that text
ends up in the row. If the backend table needs the report section only, page filtering
would have to be added.

Some bundles appear to contain more than one procedure. One sample holds both an endoscopy
report and a separate colonoscopy report. The script produces one row per file regardless.