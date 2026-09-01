from pipeline_v13_spacy import run_batch_data, build_excel_multi

import tempfile, os
from fastapi import FastAPI, UploadFile, Query
from fastapi.responses import StreamingResponse
import pandas as pd

app = FastAPI()

@app.post("/extract")
async def extract(
    file: UploadFile,
    format: str = Query("json", enum=["json", "csv"]),
    sheet_name: str = Query(None),
    findings_sheet: str = Query(None),
    year: int = Query(2024),
):
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    proc_rows, finding_rows, indication_rows = run_batch_data(
        tmp_path, sheet_name, findings_sheet, year
    )
    os.remove(tmp_path)
    ...

    if format == "csv":
        # only build workbook if csv specified
        output_path = tmp_path.replace(".xlsx", "_output.xlsx")
        build_excel_multi(proc_rows, finding_rows, output_path, indication_rows)
        return StreamingResponse(open(output_path, "rb"), media_type="application/octet-stream")

    return {
        "procedures": proc_rows,
        "findings": finding_rows,
        "indications": indication_rows,
    }