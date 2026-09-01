# Modifications to the NLP #

Modifications include;
  - Capture difficulty of procedure properly
  - Correctly identify incomplete procedure reasons
  - Change regex to pick up more key terms
  - Look for words like mass or tumour in the report


  # Testing the /extract API #

This endpoint runs the NLP pipeline over an uploaded colonoscopy/pathology workbook and returns extracted procedures, findings, and indications as JSON (or as an Excel workbook via format=csv).

## 1. Start the server ##
bash
python -m uvicorn pipeline_v13_spacy:app --reload

You should see:

Uvicorn running on http://127.0.0.1:8000

If uvicorn isn't recognised, install it first:

bash
pip install fastapi uvicorn python-multipart
## 2. Confirm it's running ##

Go to http://127.0.0.1:8000/docs in a browser. This is auto-generated from the actual routes — if /extract is listed there, the server is up and the endpoint exists.

Visiting http://127.0.0.1:8000/extract directly in a browser will always show "Not Found." A browser address bar only sends GET requests, and /extract only accepts POST. Use /docs, curl, or Postman instead — never test this endpoint by pasting the URL into a browser tab.

## 3. Test it ##
Option A — /docs UI (best for small files)
Go to http://127.0.0.1:8000/docs
Click POST /extract → Try it out
Upload a .xlsx file
Optionally set sheet_name, findings_sheet, year, format
Click Execute

Only use this for small test files (roughly low hundreds of rows). Large responses (thousands of rows) can crash the browser tab trying to render the JSON — use curl instead for anything bigger.

Option B — curl (works for any size)

JSON response, written to a file instead of the terminal:

bash
curl -X POST "http://127.0.0.1:8000/extract?format=json" \
  -F "file=@/path/to/your/file.xlsx" \
  -o response.json

CSV/Excel workbook response:

bash
curl -X POST "http://127.0.0.1:8000/extract?format=csv" \
  -F "file=@/path/to/your/file.xlsx" \
  -o output.xlsx

Inspect the JSON without opening a browser:

bash
python -m json.tool response.json | head -50
Option C — Python (for calling it from the dashboard or another script)
python
import requests

with open("your_file.xlsx", "rb") as f:
    response = requests.post(
        "http://127.0.0.1:8000/extract",
        files={"file": f},
        params={"format": "json"},  # or "csv"
    )

data = response.json()
proc_rows = data["procedures"]
finding_rows = data["findings"]
indication_rows = data["indications"]
## 4. Query parameters ##
Parameter	Default	Notes
format	json	json or csv
sheet_name	None	Leave blank to auto-process every sheet in the workbook
findings_sheet	None	Leave blank if the file doesn't have a separate findings sheet
year	2024	Only relevant when findings_sheet is set

Leaving sheet_name/findings_sheet blank is usually correct — it lets the pipeline auto-detect valid sheets rather than requiring an exact match to a hardcoded name.


