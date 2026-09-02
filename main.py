import uuid
import re
from textClean import is_missing_report_text, isolate_relevant_endoscopy_text, _match_text
from extract_procedure import (apply_procedure_finding_terms, extract_procedure, resolve_procedure_date,
                               impression_has_no_specimens_collected)
from extract_findings import extract_endoscopy_findings
from extract_pathology import extract_pathology_specimens
from merge import (merge_findings, apply_no_pathology_report_to_finding,
                   apply_no_endoscopy_report_to_finding, build_indication_rows,
                   apply_upper_gi_overrides_to_finding, apply_procedure_type_overrides,
                   is_upper_endoscopy_type, annotate_finding_row_flags, urgent_flag_for_row,
                   site_match_score, build_upper_gi_placeholder_finding)
import pandas as pd
from spacy_engine import SPACY_ENGINE
from excel_out import build_excel_multi
import argparse
from typing import Optional


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────
def run(endo_note: str, path_note: str, output_path: str):
    CMBSMain = f"PROC-{str(uuid.uuid4())[:8].upper()}"
    print(f"\n{'='*60}")
    print(f"  Procedure ID: {CMBSMain}")
    print(f"{'='*60}")

    nlp_endo_note = isolate_relevant_endoscopy_text(endo_note, "")
    proc = extract_procedure(nlp_endo_note, CMBSMain)
    proc["procedure_id"] = CMBSMain
    proc["procedure_finding_id"] = ""
    proc["registration_id"] = ""
    proc["procedure_date"] = ""
    proc["procedure_type"] = ""
    proc["procedure_location"] = ""
    proc["pathology_report_text"] = path_note
    proc["endoscopy_report_text"] = endo_note
    no_endoscopy_report = is_missing_report_text(endo_note)
    apply_procedure_finding_terms(proc, nlp_endo_note, no_endoscopy_report)
    print(f"\n[PROCEDURE]")
    for k, v in proc.items():
        if k != "CMBSMain":
            print(f"  {k}: {v}")

    endo_findings = extract_endoscopy_findings(nlp_endo_note)
    print(f"\n[ENDOSCOPY FINDINGS] — {len(endo_findings)} finding groups")
    for ef in endo_findings:
        print(f"  {ef.count}x {ef.morphology} @ {ef.site} | {ef.size} | {ef.intervention} | conf:{ef.confidence:.0%}")

    path_specimens = extract_pathology_specimens(path_note)
    print(f"\n[PATHOLOGY SPECIMENS] — {len(path_specimens)} specimens")
    for ps in path_specimens:
        print(f"  {ps.site_norm}: {ps.diagnoses}")

    finding_rows = merge_findings(endo_findings, path_specimens, CMBSMain)
    for fr in finding_rows:
        fr["procedure_id"] = CMBSMain
        fr["procedure_finding_id"] = ""
        fr["registration_id"] = ""
        fr["procedure_date"] = ""
        fr["procedure_type"] = ""
        fr["procedure_location"] = ""
        fr["pathology_report_text"] = path_note
        fr["endoscopy_report_text"] = endo_note
        if not str(path_note or "").strip():
            apply_no_pathology_report_to_finding(fr)
        annotate_finding_row_flags(fr, nlp_endo_note, path_note)
        if no_endoscopy_report:
            apply_no_endoscopy_report_to_finding(fr)
    print(f"\n[MERGED] — {len(finding_rows)} final rows")
    needs_curation = sum(1 for r in finding_rows if r["manual_curation"] == "Yes")
    print(f"  Needs manual curation: {needs_curation}")

    if needs_curation:
        proc["manual_curation"] = f"Yes — {needs_curation} finding(s) flagged"
    # Supervisor feedback 03.07.26: if Impression explicitly says no specimens
    # were collected, show that value at the end of procedure extraction.
    if impression_has_no_specimens_collected(nlp_endo_note):
        proc["manual_curation"] = "No specimens collected"
    proc["urgent_flag"] = "Yes" if any(r.get("urgent_flag") == "Yes" for r in finding_rows) else urgent_flag_for_row({"size_mm": "", "diagnosis_group": ""}, nlp_endo_note, path_note)
    proc["extraction_discrepancy_flag"] = "Yes" if any(r.get("extraction_discrepancy_flag") == "Yes" for r in finding_rows) else "No"

    indication_rows = build_indication_rows(
        endo_note,
        registration_id="",
        procedure_id=CMBSMain,
        procedure_finding_id="",
        procedure_date="",
        procedure_type="",
        procedure_location="",
    )

    build_excel_multi([proc], finding_rows, output_path, indication_rows)
    print(f"\n{'='*60}")
    print(f"  Procedure rows : 1")
    print(f"  Finding rows   : {len(finding_rows)}")
    print(f"  Manual curation: {needs_curation} flagged")
    print(f"{'='*60}\n")

def _row_level_source_id_note() -> str:
    return (
        "procedure_finding_id is aggregated in the Procedure sheet, but matched "
        "row-by-row in the Findings sheet after NLP extraction."
    )

def _canonicalise_batch_columns(df):
    """Accept the old workbook schema plus the new Muktar.xlsx sheet schema."""
    rename_map = {
        "ClientGUID": "registration_id",
        "CMBSMain": "procedure_id",
        "startDate": "procedure_date",
        "Hospital": "procedure_location",
        "colonoscopy": "endoscopy_report_text",
        "pathology": "pathology_report_text",
        "procedureType": "procedure_type",
        "ProcedureType": "procedure_type",
        "Procedure_Type": "procedure_type",
        "ProcedureFindingID": "procedure_finding_id",
        "procedureFindingID": "procedure_finding_id",
        "procedure_finding_ID": "procedure_finding_id",
        "procedure_findings_id": "procedure_finding_id",
    }
    normalised_aliases = {
        "registrationid": "registration_id",
        "clientguid": "registration_id",
        "procedureid": "procedure_id",
        "cmbsmain": "procedure_id",
        "proceduredate": "procedure_date",
        "startdate": "procedure_date",
        "proceduretype": "procedure_type",
        "procedurelocation": "procedure_location",
        "hospital": "procedure_location",
        "endoscopyreporttext": "endoscopy_report_text",
        "colonocopyreport": "endoscopy_report_text",
        "colonoscopyreport": "endoscopy_report_text",
        "pathologyreporttext": "pathology_report_text",
        "pathresult": "pathology_report_text",
        "procedurefindingid": "procedure_finding_id",
        "procedurefindingsid": "procedure_finding_id",
        "colonoscopy": "endoscopy_report_text",
        "pathology": "pathology_report_text",
        "pathologyreport": "pathology_report_text"
    }
    renamed = {}
    for c in df.columns:
        c_str = str(c).strip()
        key = re.sub(r'[^a-z0-9]', '', c_str.lower())
        renamed[c] = rename_map.get(c_str, normalised_aliases.get(key, c_str))
    df = df.rename(columns=renamed)
    return df


def _clean_identifier(value) -> str:
    """Keep Excel numeric IDs stable as strings without trailing .0."""

    if pd.isna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _collapse_ids(values) -> str:
    ids = []
    for value in values:
        cleaned = _clean_identifier(value)
        if cleaned and cleaned not in ids:
            ids.append(cleaned)
    return "; ".join(ids)





def build_source_finding_lookup(finding_df):
    """Create procedure_id -> source finding records for row-level ID matching."""
    records_by_proc = {}
    for _, row in finding_df.iterrows():
        procedure_id = _clean_identifier(row.get("procedure_id", ""))
        finding_id = _clean_identifier(row.get("procedure_finding_id", ""))
        if not procedure_id or not finding_id:
            continue
        record = {
            "procedure_finding_id": finding_id,
            "site": row.get("site", ""),
            "finding_morphology": row.get("finding_morphology", ""),
            "number_categorical": row.get("finding_number_categorical", ""),
            "size_mm": row.get("finding_size_mm", ""),
            "finding_intervention_performed": row.get("finding_intervention_performed", ""),
            "finding_intervention_completeness": row.get("finding_intervention_completeness", ""),
            "type": row.get("type", ""),
            "diagnosis_group": row.get("diagnosis_group", ""),
            "dysplasia": row.get("dysplasia", ""),
        }
        records_by_proc.setdefault(procedure_id, []).append(record)
    return records_by_proc


def source_finding_match_score(extracted_row: dict, source_row: dict) -> int:
    """Score a source finding against one extracted output row."""
    score = 0
    src_site = _match_text(source_row.get("site"))
    out_site = _match_text(extracted_row.get("site"))
    if src_site and out_site:
        score += int(site_match_score(extracted_row.get("site", ""), source_row.get("site", "")) * 0.40)

    comparisons = [
        ("size_mm", "size_mm", 18),
        ("diagnosis_group", "diagnosis_group", 18),
        ("dysplasia", "dysplasia", 10),
        ("finding_intervention_performed", "finding_intervention_performed", 10),
        ("finding_intervention_completeness", "finding_intervention_completeness", 6),
        ("type", "type", 6),
        ("finding_morphology", "finding_morphology", 5),
    ]
    for out_key, src_key, weight in comparisons:
        out_val = _match_text(extracted_row.get(out_key))
        src_val = _match_text(source_row.get(src_key))
        if out_val and src_val and out_val == src_val:
            score += weight

    out_count = _clean_identifier(extracted_row.get("number_categorical", ""))
    src_count = _clean_identifier(source_row.get("number_categorical", ""))
    if out_count and src_count and out_count == src_count:
        score += 5

    return score


def match_procedure_finding_id(extracted_row: dict, source_findings: list[dict]) -> str:
    """Assign the most likely source procedure_finding_id after extraction.

    This is intentionally done after NLP extraction because one procedure may
    have zero, one, or many source finding rows. A pre-extraction join can only
    attach all IDs for the procedure; this function tries to attach the one best
    matching source ID to each extracted finding row.
    """
    if not source_findings:
        return ""
    if len(source_findings) == 1:
        return source_findings[0]["procedure_finding_id"]

    best_row, best_score = None, -1
    for source_row in source_findings:
        score = source_finding_match_score(extracted_row, source_row)
        if score > best_score:
            best_row, best_score = source_row, score

    return best_row["procedure_finding_id"] if best_row and best_score >= 30 else ""


def assign_procedure_finding_ids(extracted_rows: list[dict], source_findings: list[dict]) -> None:
    """Assign source procedure_finding_id values one-to-one within a procedure.

    QA feedback 04.06.26: assigning each NLP row independently can reuse the
    same source procedure_finding_id several times. This procedure-level pass
    scores all possible pairs, then consumes each source ID after the best
    confident match so duplicated IDs are not created by the matcher.
    """
    for row in extracted_rows:
        row["procedure_finding_id"] = ""

    if not extracted_rows or not source_findings:
        return

    if len(extracted_rows) == 1 and len(source_findings) == 1:
        extracted_rows[0]["procedure_finding_id"] = source_findings[0]["procedure_finding_id"]
        return

    candidates = []
    for out_idx, extracted_row in enumerate(extracted_rows):
        for src_idx, source_row in enumerate(source_findings):
            score = source_finding_match_score(extracted_row, source_row)
            if score >= 30:
                candidates.append((score, out_idx, src_idx))

    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    used_output_rows = set()
    used_source_rows = set()

    for score, out_idx, src_idx in candidates:
        if out_idx in used_output_rows or src_idx in used_source_rows:
            continue
        extracted_rows[out_idx]["procedure_finding_id"] = source_findings[src_idx]["procedure_finding_id"]
        used_output_rows.add(out_idx)
        used_source_rows.add(src_idx)


def prepare_muktar_2024_dataframe(
    df_path: str,
    procedures_sheet: str = "vw_scoop_procedures",
    findings_sheet: str = "vw_scoop_procedure_findings",
    year: int = 2024,
    return_source_findings: bool = False,
):
    """Build the v7 analysis dataframe from Muktar.xlsx.

    The procedure sheet is filtered to the requested year and reduced to the
    seven source columns requested by the user. procedure_finding_id is then
    joined from vw_scoop_procedure_findings for traceability in the final output.
    Multiple source finding IDs for the same procedure are kept as a semicolon-
    separated list to avoid running the same endoscopy/pathology report repeatedly.
    """
    proc_df = pd.read_excel(df_path, sheet_name=procedures_sheet)
    finding_df = pd.read_excel(df_path, sheet_name=findings_sheet)

    proc_df.columns = proc_df.columns.astype(str).str.strip()
    finding_df.columns = finding_df.columns.astype(str).str.strip()
    proc_df = _canonicalise_batch_columns(proc_df)
    finding_df = _canonicalise_batch_columns(finding_df)

    required_proc_cols = [
        "registration_id",
        "procedure_id",
        "procedure_date",
        "procedure_type",
        "procedure_location",
        "endoscopy_report_text",
        "pathology_report_text",
    ]
    missing_proc = [c for c in required_proc_cols if c not in proc_df.columns]
    if missing_proc:
        raise ValueError(f"Sheet {procedures_sheet}: missing required columns {missing_proc}")

    missing_find = [c for c in ["procedure_id", "procedure_finding_id"] if c not in finding_df.columns]
    if missing_find:
        raise ValueError(f"Sheet {findings_sheet}: missing required columns {missing_find}")

    proc_df["procedure_id"] = proc_df["procedure_id"].map(_clean_identifier)
    finding_df["procedure_id"] = finding_df["procedure_id"].map(_clean_identifier)

    procedure_dates = pd.to_datetime(proc_df["procedure_date"], errors="coerce")
    year_mask = procedure_dates.dt.year.eq(year)
    if year_mask.isna().all() or not year_mask.any():
        year_mask = proc_df["procedure_date"].astype(str).str.contains(str(year), na=False)
    proc_df = proc_df.loc[year_mask, required_proc_cols].copy()

    finding_ids = (
        finding_df
        .dropna(subset=["procedure_id"])
        .groupby("procedure_id", as_index=False)["procedure_finding_id"]
        .agg(_collapse_ids)
    )

    prepared = proc_df.merge(finding_ids, on="procedure_id", how="left")
    prepared["procedure_finding_id"] = prepared["procedure_finding_id"].fillna("")
    prepared = prepared[
        [
            "registration_id",
            "procedure_id",
            "procedure_date",
            "procedure_type",
            "procedure_location",
            "endoscopy_report_text",
            "pathology_report_text",
            "procedure_finding_id",
        ]
    ]
    if return_source_findings:
        source_lookup = build_source_finding_lookup(
            finding_df[finding_df["procedure_id"].isin(set(prepared["procedure_id"]))]
        )
        return prepared, source_lookup
    return prepared


def run_batch_data(
    df_path: str,
    sheet_name: Optional[str] = "vw_scoop_procedures",
    findings_sheet: Optional[str] = "vw_scoop_procedure_findings",
    year: int = 2024,
):
    """Run pipeline on a batch Excel file using real source columns."""

    source_finding_lookup = {}
    if findings_sheet:
        df, source_finding_lookup = prepare_muktar_2024_dataframe(
            df_path,
            procedures_sheet=sheet_name or "vw_scoop_procedures",
            findings_sheet=findings_sheet,
            year=year,
            return_source_findings=True,
        )
        sheets = {f"{sheet_name} ({year})": df}
    elif sheet_name:
        sheets = {sheet_name: pd.read_excel(df_path, sheet_name=sheet_name)}
    else:
        sheets = pd.read_excel(df_path, sheet_name=None)

    required_cols = [
        "registration_id",
        "procedure_id",
        "procedure_date",
        "procedure_type",
        "procedure_location",
        "endoscopy_report_text",
        "pathology_report_text",
        "procedure_finding_id",
    ]

    all_proc_rows, all_finding_rows, all_indication_rows = [], [], []

    print(f"spaCy engine: {'enabled' if SPACY_ENGINE.available else 'not installed - regex/fuzzy fallback active'}")

    for source_sheet, df in sheets.items():
        df.columns = df.columns.str.strip()
        df = _canonicalise_batch_columns(df)
        for optional_col in ["procedure_type", "procedure_finding_id", "procedure_id", "procedure_date", "procedure_location"]:
            if optional_col not in df.columns:
                df[optional_col] = ""
        if (df["procedure_id"] == "").all():
            df["procedure_id"] = [
                f"PROC-{_clean_identifier(reg)} - {i}" if _clean_identifier(reg) else f"PROC-ROW-{i}"
                for i, reg in enumerate(df["registration_id"], start = 1)
            ]
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            print(f"Sheet {source_sheet}: skipped - missing required columns {missing}")
            continue

        print(f"\n[SHEET {source_sheet}] {len(df)} source rows")

        for i, row in df.iterrows():
            registration_id = "" if pd.isna(row["registration_id"]) else str(row["registration_id"]).strip()
            procedure_id = _clean_identifier(row["procedure_id"])
            procedure_finding_id = "" if pd.isna(row["procedure_finding_id"]) else str(row["procedure_finding_id"]).strip()
            procedure_date = "" if pd.isna(row["procedure_date"]) else str(row["procedure_date"]).strip()
            procedure_type = "" if pd.isna(row["procedure_type"]) else str(row["procedure_type"]).strip()
            procedure_location = "" if pd.isna(row["procedure_location"]) else str(row["procedure_location"]).strip()
            endo = "" if pd.isna(row["endoscopy_report_text"]) else str(row["endoscopy_report_text"])
            path = "" if pd.isna(row["pathology_report_text"]) else str(row["pathology_report_text"])
            no_endoscopy_report = is_missing_report_text(endo)
            upper_gi_only = is_upper_endoscopy_type(procedure_type)
            nlp_endo = isolate_relevant_endoscopy_text(endo, procedure_type)

            if not procedure_id:
                print(f"Sheet {source_sheet} row {i + 2}: skipped - missing procedure_id")
                continue

            proc = extract_procedure(nlp_endo, procedure_id)
            proc["procedure_id"] = procedure_id
            proc["procedure_finding_id"] = procedure_finding_id
            proc["registration_id"] = registration_id
            proc["procedure_date"] = resolve_procedure_date(proc.get("report_date", ""), procedure_date)
            proc["procedure_type"] = procedure_type
            proc["procedure_location"] = procedure_location
            proc["pathology_report_text"] = path
            proc["endoscopy_report_text"] = endo
            apply_procedure_finding_terms(proc, nlp_endo, no_endoscopy_report)
            apply_procedure_type_overrides(proc, procedure_type)

            if upper_gi_only:
                # Supervisor feedback 26.06.26: upper-GI-only rows are not
                # colorectal extraction targets, so do not let gastric/duodenal
                # text generate misleading polyp rows.
                finding_rows = [build_upper_gi_placeholder_finding(procedure_id)]
            else:
                findings = extract_endoscopy_findings(nlp_endo) if not no_endoscopy_report else []
                specs = extract_pathology_specimens(path) if path.strip() else []
                finding_rows = merge_findings(findings, specs, procedure_id)
            source_findings = source_finding_lookup.get(procedure_id, [])
            assign_procedure_finding_ids(finding_rows, source_findings)

            for fr in finding_rows:
                fr["procedure_id"] = procedure_id
                fr["registration_id"] = registration_id
                fr["procedure_date"] = procedure_date
                fr["procedure_type"] = procedure_type
                fr["procedure_location"] = procedure_location
                fr["pathology_report_text"] = path
                fr["endoscopy_report_text"] = endo
                if not path.strip():
                    apply_no_pathology_report_to_finding(fr)
                if upper_gi_only:
                    apply_upper_gi_overrides_to_finding(fr)
                annotate_finding_row_flags(fr, nlp_endo, path)
                if no_endoscopy_report:
                    apply_no_endoscopy_report_to_finding(fr)

            needs_curation = sum(1 for r in finding_rows if r["manual_curation"] == "Yes")
            if needs_curation:
                proc["manual_curation"] = f"Yes — {needs_curation} finding(s) flagged"
            # Supervisor feedback 03.07.26: keep the Impression-level "No
            # specimens collected" indicator even after finding curation counts.
            if impression_has_no_specimens_collected(nlp_endo):
                proc["manual_curation"] = "No specimens collected"
            proc["urgent_flag"] = "Yes" if any(r.get("urgent_flag") == "Yes" for r in finding_rows) else urgent_flag_for_row({"size_mm": "", "diagnosis_group": ""}, nlp_endo, path)
            proc["extraction_discrepancy_flag"] = "Yes" if any(r.get("extraction_discrepancy_flag") == "Yes" for r in finding_rows) else "No"

            indication_rows = build_indication_rows(
                endo,
                registration_id=registration_id,
                procedure_id=procedure_id,
                procedure_finding_id=procedure_finding_id,
                procedure_date=procedure_date,
                procedure_type=procedure_type,
                procedure_location=procedure_location,
            )

            all_proc_rows.append(proc)
            all_finding_rows.extend(finding_rows)
            all_indication_rows.extend(indication_rows)

            print(
                f"Sheet={source_sheet}, registration_id={registration_id}, procedure_id={procedure_id}, "
                f"procedure_finding_id={procedure_finding_id}: "
                f"{len(finding_rows)} finding rows, {needs_curation} flagged"
            )

    print(f"\nTotal: {len(all_proc_rows)} procedures, {len(all_finding_rows)} findings, {len(all_indication_rows)} indications")

    return all_proc_rows, all_finding_rows, all_indication_rows


def run_batch(
    df_path: str,
    output_path: str,
    sheet_name: Optional[str] = "vw_scoop_procedures",
    findings_sheet: Optional[str] = "vw_scoop_procedure_findings",
    year: int = 2024,
):
    """CLI entry point: run the pipeline and write the Excel workbook."""
    all_proc_rows, all_finding_rows, all_indication_rows = run_batch_data(
        df_path, sheet_name, findings_sheet, year
    )
    build_excel_multi(all_proc_rows, all_finding_rows, output_path, all_indication_rows)
    return all_proc_rows, all_finding_rows, all_indication_rows


if __name__ == "__main__":
    # Supervisor feedback 03.07.26: this file is the v12 delivery, so the CLI
    # help/default output should not create a workbook labelled as v11.
    parser = argparse.ArgumentParser(description="v12 spaCy-enhanced colonoscopy/pathology polyp and indication extraction pipeline")
    parser.add_argument(
        "--input",
        default="data/SALHN colonoscopies and pathology 2020-2025.xlsx",
        help="Input workbook path, defaulting to the input dataset"
    )
    parser.add_argument(
        "--output",
        default="data/refactoredOutput.xlsx",
        help="Output workbook path"
    )
    parser.add_argument("--sheet", default="Sheet1", help="Procedure sheet to process")
    parser.add_argument("--findings-sheet", default=None, help="Sheet containing procedure_finding_id")
    parser.add_argument("--year", type=int, default=2024, help="Procedure year to process")
    args = parser.parse_args()
    run_batch(args.input, args.output, args.sheet, args.findings_sheet, args.year)