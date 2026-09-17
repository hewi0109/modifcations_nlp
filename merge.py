import re
import uuid
from textClean import is_missing_report_text, normalise, is_upper_endoscopy_type, _match_text
from vocab import DIAGNOSIS_GROUP_VOCAB, DYSPLASIA_VOCAB, DIAGNOSIS_SEVERITY, DYSPLASIA_SEVERITY
from difflib import SequenceMatcher
from extraction.extract_findings import EndoscopyFinding
from extraction.extract_pathology import PathologySpecimen, wait_for_supplementary
from spacy_engine import fuzz


# ─────────────────────────────────────────────────────────────────
# STAGE 4: MERGE ENGINE
# Endoscopy drives row count. Match by site. Worst diagnosis wins.
# ─────────────────────────────────────────────────────────────────
def site_match_score(endo_site: str, path_site: str) -> int:
    rectal_sites = {"Rectum", "Upper rectum", "Mid rectum", "Distal rectum"}

    if endo_site in rectal_sites and path_site in rectal_sites:
        return 92

    # Rectosigmoid junction is anatomically adjacent to rectum, but not the
    # same site -- treat as a plausible but lower-confidence match rather
    # than either a full rectal match (92) or a hard non-match (0).
    if endo_site == "Rectosigmoid junction" and path_site in rectal_sites:
        return 80
    if path_site == "Rectosigmoid junction" and endo_site in rectal_sites:
        return 80

    if (endo_site in rectal_sites) != (path_site in rectal_sites):
        return 0
    if endo_site == "Appendiceal orifice" and path_site in {"Caecum", "Ascending colon"}:
        return 75
    if fuzz:
        return fuzz.token_sort_ratio(endo_site.lower(), path_site.lower())
    return int(SequenceMatcher(None, endo_site.lower(), path_site.lower()).ratio() * 100)

def worst_diagnosis(diagnoses: list[tuple[str, str, int]]) -> tuple[str, str]:
    """Pick worst diagnosis + dysplasia by severity ranking."""
    if not diagnoses:
        return "Not documented", "Not documented"
    best = max(diagnoses, key=lambda d: (
        DIAGNOSIS_SEVERITY.get(d[0], 0),
        DYSPLASIA_SEVERITY.get(d[1], 0)
    ))
    return best[0], best[1]

def pathology_implies_polyp(diag_norm: str, raw_text: str) -> bool:
    polyp_diags = {
        "Admixed polyp",
        "Hyperplastic polyp",
        "Inflammatory polyp",
        "Juvenile polyp",
        "Possible Sessile Serrated Polyp",
        "Pseudo-polyp",
        "Sessile Serrated Polyp",
        "Traditional Serrated Adenoma",
        "Tubular adenoma",
        "Tubulovillous adenoma",
        "NLP unable to classify pathology clearly",
        "Unspecified polyp - polyp not excised",
        "Unspecified polyp - not excised (likely HP)",
        "Unspecified polyp - polyp not retrieved",
        "Villous adenoma",
    }

    if diag_norm in polyp_diags:
        return True

    if re.search(r'\bpolyp\b|\bpolyps\b|\badenoma\b|\badenomas\b|\bserrated\b', raw_text, re.I):
        return True

    return False


NON_DYSPLASTIC_DIAGNOSES = {
    "Hyperplastic polyp",
    "Inflammatory polyp",
    "Juvenile polyp",
    "Pseudo-polyp",
    "Normal tissue",
    "Granulation tissue",
    "Ileitis",
    "Lipoma",
    "Leiomyoma",
    "Melanosis coli",
}


def postprocess_pathology_result(diag_group: str, dysplasia: str) -> tuple[str, str]:
    """Apply supervisor feedback for dysplasia values that are structural, not missing."""
    if diag_group == "Unspecified polyp - pathology not clear":
        diag_group = "NLP unable to classify pathology clearly"
    if diag_group in NON_DYSPLASTIC_DIAGNOSES:
        return diag_group, "N/A"
    return diag_group, dysplasia


def manual_curation_decision(
    diag_group: str,
    dysplasia: str,
    path_matched: bool,
    mixed: bool,
    count_mismatch: bool,
    current_reason: str,
) -> tuple[str, str]:
    reasons = []
    if current_reason and current_reason != "N/A":
        reasons.append(current_reason)
    if mixed and not any("Mixed pathology" in r for r in reasons):
        reasons.append("Mixed pathology at site")
    if diag_group in {"Unspecified polyp - pathology not clear", "NLP unable to classify pathology clearly"}:
        reasons.append("NLP unable to classify pathology clearly")
    if not path_matched:
        reasons.append("No pathology match")
    manual = count_mismatch or mixed or (not path_matched) or diag_group in {
        "Unspecified polyp - pathology not clear",
        "NLP unable to classify pathology clearly",
    }
    return ("Yes" if manual else "No"), (" | ".join(dict.fromkeys(reasons)) if reasons else "N/A")


URGENT_DIAGNOSES = {
    "Adenocarcinoma",
    "Squamous cell carcinoma",
    "Neuroendocrine tumour",
}


def max_size_mm(size_text: str) -> float:
    """Return the largest numeric size in a normalised size string."""
    if not size_text or str(size_text).lower() in {"not documented", "nan"}:
        return 0.0
    values = [float(v) for v in re.findall(r'\d+(?:\.\d+)?', str(size_text))]
    return max(values) if values else 0.0


def urgent_flag_for_row(row: dict, endoscopy_text: str = "", pathology_text: str = "") -> str:
    """Flag findings that may need urgent clinical review.

    Supervisor feedback 22.05.26: urgent if polyp size is >20mm, diagnosis is
    adenocarcinoma/squamous cell carcinoma/neuroendocrine tumour, or either
    report mentions recurrence/recurring using a recurr* stem.
    """
    if max_size_mm(row.get("size_mm", "")) > 20:
        return "Yes"
    if row.get("diagnosis_group") in URGENT_DIAGNOSES:
        return "Yes"
    combined_text = f"{endoscopy_text or ''} {pathology_text or ''}"
    if re.search(r'\brecurr\w*', combined_text, re.I):
        return "Yes"
    return "No"


def extraction_discrepancy_flag_for_row(row: dict) -> str:
    """Flag likely endoscopy/pathology extraction discrepancies for review."""
    reason = str(row.get("curation_reason", ""))
    if row.get("count_mismatch") == "Yes":
        return "Yes"
    if row.get("path_site_matched") == "No":
        return "Yes"
    if re.search(r'pathology-only|no pathology match|count mismatch|more fragments|mixed pathology|report has more findings', reason, re.I):
        return "Yes"
    return "No"


def annotate_finding_row_flags(row: dict, endoscopy_text: str = "", pathology_text: str = "") -> dict:
    row["urgent_flag"] = urgent_flag_for_row(row, endoscopy_text, pathology_text)
    row["extraction_discrepancy_flag"] = extraction_discrepancy_flag_for_row(row)
    return row


def apply_report_count_discrepancy_reason(
    rows: list[dict],
    endo_findings: list["EndoscopyFinding"],
    path_specimens: list["PathologySpecimen"],
) -> list[dict]:
    """Append supervisor-requested wording when report finding counts differ."""
    # Supervisor feedback 03.07.26: make cross-report count discrepancies easy
    # for nurses to scan, using explicit "endoscopy/path report has more
    # findings" wording alongside the existing detailed curation reasons.
    endo_count = len([ef for ef in endo_findings if not ef.is_negated])
    path_count = len([ps for ps in path_specimens if ps.site_norm != "Unknown"])
    if endo_count == path_count:
        return rows
    reason = (
        "The endoscopy report has more findings"
        if endo_count > path_count
        else "The path report has more findings"
    )
    for row in rows:
        existing = str(row.get("curation_reason", "") or "")
        parts = [] if existing in {"", "N/A", "nan"} else [existing]
        if reason not in parts:
            parts.append(reason)
        row["curation_reason"] = " | ".join(parts)
        row["manual_curation"] = "Yes"
        row["extraction_discrepancy_flag"] = "Yes"
    return rows


def extract_indication_block(endo_note: str) -> str:
    """Extract the raw Indication(s) block from an endoscopy report."""
    if is_missing_report_text(endo_note):
        return ""
    text = str(endo_note)
    text = re.sub(r'_?x000D_|_?x000A_|\r', '\n', text, flags=re.I)
    # Stop at common endoscopy report section headings.
    m = re.search(
        r'\bIndications?\s*:\s*(.*?)(?=\n?\s*(?:'
        r'Referring\s+Physician|Requesting\s+Physician|Consultant|Providers?|'
        r'Medications?|Procedure|Findings?|Complications?|Recommendation|Impression'
        r')\b\s*:?\s*|$)',
        text,
        re.I | re.S
    )
    return m.group(1).strip() if m else ""


def _clean_indication_entry(text: str) -> str:
    text = re.sub(r'_?x000D_|_?x000A_|\r|\n', ' ', str(text), flags=re.I)
    text = re.sub(r'\s+', ' ', text).strip(" '\"\t-")
    return text


def split_indication_entries(block: str) -> list[str]:
    """Split an indication block into conservative, top-level entries.

    Bullet/indented lines are kept with the preceding indication because many
    reports use them as supporting details rather than separate indications.
    We split only on strong new-entry cues, such as repeated surveillance or
    follow-up prefixes inside the same Indications block.
    """
    if not block:
        return []

    raw_lines = [re.sub(r'\s+', ' ', line).strip() for line in block.splitlines()]
    raw_lines = [line for line in raw_lines if line]

    grouped = []
    current = ""
    for line in raw_lines:
        is_bullet = bool(re.match(r'^[-•*]\s+', line))
        line = re.sub(r'^[-•*]\s+', '', line).strip()
        if not current:
            current = line
        elif is_bullet:
            current = f"{current} - {line}"
        else:
            # Most non-bullet line breaks in these reports are word wrapping.
            current = f"{current} {line}"
    if current:
        grouped.append(current)

    strong_starters = (
        r'Above average risk colon cancer surveillance|Colon cancer surveillance|'
        r'Follow-up|Personal history|Positive faecal immunochemical test|'
        r'Iron deficiency anaemia|Haematochezia|Rectal bleeding|Abnormal CT|'
        r'For EMR|For excision|Referral for|Suspected|Preoperative assessment|'
        r'Indeterminate colitis|PSC\b|UC\b'
    )

    entries = []
    for text in grouped:
        # Example feedback: one line may contain two surveillance indications:
        # "Colon cancer surveillance: ..., Colon cancer surveillance: ..."
        parts = re.split(rf'\s*,\s*(?=(?:{strong_starters})\b)', text, flags=re.I)
        for part in parts:
            cleaned = _clean_indication_entry(part)
            if cleaned and cleaned.lower() not in {"not documented", "none", "nan"}:
                entries.append(cleaned)

    deduped = []
    for entry in entries:
        if entry.lower() not in {x.lower() for x in deduped}:
            deduped.append(entry)
    return deduped


def extract_indications(endo_note: str) -> list[str]:
    """Return one row-worthy indication entry per extracted indication."""
    return split_indication_entries(extract_indication_block(endo_note))


def indication_nbcsp_value(indication: str) -> str:
    """Return the indication text only when it is a true NBCSP indication.

    Supervisor feedback 08.06.26: the review column should repeat the source
    indication text, not a Yes/No flag, while excluding explicit non-NBCSP FITs.
    """
    text = str(indication or "").strip()
    if not text:
        return ""
    if re.search(r'\bnon\s*[- ]?\s*NBCSP\b', text, re.I):
        return ""
    if re.search(r'\bNBCSP\b|National Bowel Cancer Screening Program', text, re.I):
        return text
    return ""


def build_indication_rows(
    endo_note: str,
    registration_id: str,
    procedure_id: str,
    procedure_finding_id: str,
    procedure_date: str,
    procedure_type: str,
    procedure_location: str,
) -> list[dict]:
    rows = []
    for indication in extract_indications(endo_note):
        rows.append({
            "indication_id": f"I-{str(uuid.uuid4())[:6].upper()}",
            "registration_id": registration_id,
            "procedure_id": procedure_id,
            "procedure_finding_id": procedure_finding_id,
            "procedure_date": procedure_date,
            "procedure_type": procedure_type,
            "procedure_location": procedure_location,
            "indication": indication,
            "indication_nbcsp": indication_nbcsp_value(indication),
            "endoscopy_report_text": endo_note,
        })
    return rows


def is_flexible_procedure_type(procedure_type: str) -> bool:
    t = _match_text(procedure_type)
    return bool(re.search(r'\bflex(?:ible)?\b.*\b(sig|sigmoidoscopy|sigmoidscopy|colon(?:oscopy)?)\b|flexible\s+sigmoid(?:o)?scopy|flexible\s+colonoscopy', t, re.I))


def is_anastomosis_procedure_type(procedure_type: str) -> bool:
    return bool(re.search(r'\banastom', str(procedure_type or ""), re.I))


def apply_procedure_type_overrides(proc: dict, procedure_type: str) -> dict:
    """Apply procedure-type rules requested in supervisor feedback."""
    if is_upper_endoscopy_type(procedure_type):
        # Supervisor feedback 26.06.26: upper-GI-only rows should not look like
        # failed colonoscopies; direct nurse review to the correct procedure type.
        upper_msg = "Upper GI Procedure (Nurse Check)"
        for key in [
            "procedure_completeness",
            "intubation_distance",
            "bowel_prep_given",
            "bowel_prep_quality",
            "bbps_right",
            "bbps_transverse",
            "bbps_left",
            "total_bbps_score",
            "withdrawal_time",
        ]:
            proc[key] = upper_msg
        proc["reason_incomplete"] = upper_msg
        return proc
    if is_flexible_procedure_type(procedure_type):
        proc["procedure_completeness"] = "N/A"
        proc["reason_incomplete"] = "N/A - flexible sigmoidoscopy/colonoscopy"
    if is_anastomosis_procedure_type(procedure_type) and proc.get("intubation_distance") in ("Not Reported", "Not reported", "Not documented"):
        proc["intubation_distance"] = "Nurse check (unspecified distance)"
    return proc


def apply_upper_gi_overrides_to_finding(row: dict) -> dict:
    """Mark finding NLP fields for upper-GI-only procedures."""
    upper_msg = "Upper GI Procedure (Nurse Check)"
    # Supervisor feedback 26.06.26: the finding/pathology NLP columns should
    # clearly indicate that this is not a colorectal extraction target.
    for key in [
        "finding_morphology",
        "number_categorical",
        "size_mm",
        "finding_intervention_performed",
        "finding_intervention_completeness",
        "type",
        "diagnosis_group",
        "dysplasia",
    ]:
        row[key] = upper_msg
    row["manual_curation"] = "Yes"
    row["curation_reason"] = upper_msg
    row["extraction_discrepancy_flag"] = "Yes"
    return row


def build_upper_gi_placeholder_finding(CMBSMain: str) -> dict:
    row = {
        "finding_id": f"F-{str(uuid.uuid4())[:6].upper()}",
        "CMBSMain": CMBSMain,
        "site": "Not applicable",
        "finding_morphology": "Not documented",
        "number_categorical": "Not documented",
        "size_mm": "Not documented",
        "finding_intervention_performed": "Not documented",
        "finding_intervention_completeness": "Not documented",
        "type": "Not documented",
        "diagnosis_group": "Not documented",
        "dysplasia": "Not documented",
        "path_site_matched": "No",
        "path_match_score": "0%",
        "mixed_pathology": "No",
        "count_mismatch": "No",
        "negated": "No",
        "hedged": "No",
        "hedge_type": "N/A",
        "manual_curation": "Yes",
        "curation_reason": "Upper GI Procedure (Nurse Check)",
        "urgent_flag": "No",
        "extraction_discrepancy_flag": "Yes",
    }
    return apply_upper_gi_overrides_to_finding(row)


def apply_no_endoscopy_report_to_finding(row: dict) -> dict:
    """Make pathology-only rows explicit when no endoscopy report was available."""
    for key in [
        "finding_morphology",
        "number_categorical",
        "size_mm",
        "finding_intervention_performed",
        "finding_intervention_completeness",
        "type",
    ]:
        row[key] = "No Endoscopy report"
    row["manual_curation"] = "Yes"
    row["curation_reason"] = "No endoscopy report"
    row["extraction_discrepancy_flag"] = "Yes"
    return row


def apply_no_pathology_report_to_finding(row: dict) -> dict:
    """Make diagnosis/dysplasia explicit when pathology text is absent."""
    row["diagnosis_group"] = "No Pathology Report"
    row["dysplasia"] = "No Pathology Report"
    if row.get("curation_reason") in ("", "N/A", None):
        row["curation_reason"] = "No pathology report"
    return row


def _append_row(
    rows,
    ef,
    CMBSMain,
    count,
    diag_group,
    dysplasia,
    path_matched,
    best_specimen,
    best_score,
    mixed,
    count_mismatch,
    curation_reason,
    size_override=None,
    supplementary_flag=None,
):
    size = size_override if size_override else ef.size
    diag_group, dysplasia = postprocess_pathology_result(diag_group, dysplasia)

    if path_matched and supplementary_flag:
        curation_reason = (
            f"{curation_reason} | {supplementary_flag}"
            if curation_reason and curation_reason not in ("N/A", "")
            else supplementary_flag
        )

    manual_curation, curation_reason = manual_curation_decision(
        diag_group=diag_group,
        dysplasia=dysplasia,
        path_matched=path_matched,
        mixed=mixed,
        count_mismatch=count_mismatch,
        current_reason=curation_reason,
    )

    if path_matched and supplementary_flag:
        manual_curation = "Yes"

    is_biopsy = ef.finding_type == "Biopsy" or (best_specimen is not None and best_specimen.specimen_type == "Biopsy")
    number_categorical = None if is_biopsy else count
    intervention_performed = "Biopsy only" if is_biopsy else ef.intervention

    # A successful pathology match is direct proof this specimen was
    # retrieved -- overrides a "Not retrieved" completeness value that was
    # only ever a block-level guess (e.g. from multi-site text where the
    # retrieval caveat actually applied to a different site).
    completeness = ef.completeness
    if path_matched and completeness == "Not retrieved":
        completeness = "Complete"

    # A finding cannot logically be both "Not excised" and "Complete" --
    # this combination is almost always caused by two distinct findings
    # merging into one text block during extraction. Checked against the
    # FINAL values above (post biopsy/retrieval overrides), since those are
    # what actually gets shown to the reviewer.
    if intervention_performed == "Not excised" and completeness == "Complete":
        conflict_reason = "Intervention/completeness mismatch — 'Not excised' with 'Complete' outcome, verify against source text"
        curation_reason = (
            f"{curation_reason} | {conflict_reason}"
            if curation_reason and curation_reason not in ("N/A", "")
            else conflict_reason
        )
        manual_curation = "Yes"

    rows.append({
        "finding_id": f"F-{str(uuid.uuid4())[:6].upper()}",
        "CMBSMain": CMBSMain,
        "site": ef.site,
        "finding_morphology": ef.morphology,
        "number_categorical": number_categorical,
        "size_mm": size,
        "finding_intervention_performed": intervention_performed,
        "finding_intervention_completeness": completeness,
        "type": ef.finding_type,
        "diagnosis_group": diag_group,
        "dysplasia": dysplasia,
        "path_site_matched": best_specimen.site_norm if best_specimen else "No",
        "path_match_score": f"{best_score}%" if best_specimen else "0%",
        "mixed_pathology": "Yes" if mixed else "No",
        "count_mismatch": "Yes" if count_mismatch else "No",
        "negated": "No",
        "hedged": "No",
        "hedge_type": "N/A",
        "manual_curation": manual_curation,
        "curation_reason": curation_reason,
        "urgent_flag": "No",
        "extraction_discrepancy_flag": "No",
    })

def merge_findings(
    endo_findings: list[EndoscopyFinding],
    path_specimens: list[PathologySpecimen],
    CMBSMain: str,
    pathology_report_text: str = "",
) -> list[dict]:

    """
    Row generation rules:
    - Endoscopy drives row count for polyp findings
    - When endoscopy has no findings but pathology has specimens → generate pathology-only rows
    - Same site + same diagnosis → 1 row
    - Different diagnoses at same site → split rows
    """

    supplementary_flag = wait_for_supplementary(pathology_report_text)

    # If no endoscopy findings but pathology has specimens → pathology-only rows
    if not endo_findings and path_specimens:
        rows = []
        for ps in path_specimens:
            if ps.site_norm == "Unknown":
                continue
            diag, dys = ps.diagnoses[0][0], ps.diagnoses[0][1]
            diag_norm, _ = normalise(diag, DIAGNOSIS_GROUP_VOCAB, threshold=65)
            dys_norm, _ = normalise(dys, DYSPLASIA_VOCAB, threshold=65)
            diag_norm, dys_norm = postprocess_pathology_result(diag_norm, dys_norm)

            if diag_norm in ("Normal tissue", "Not documented") and ps.specimen_type != "Biopsy":
                continue

            is_polyp_case = ps.specimen_type != "Biopsy" and pathology_implies_polyp(diag_norm, ps.raw_text)

            # Better count for pathology-only cases
            inferred_count = ps.fragment_count_total if ps.fragment_count_total and ps.fragment_count_total < 99 else 1
            if inferred_count < 1:
                inferred_count = 1
            if not is_polyp_case:
                inferred_count = None

            base_reason = (
                "Pathology-only polyp finding — no endoscopy finding extracted"
                if is_polyp_case
                else "Biopsy-only procedure — no endoscopy polyp findings"
            )

            rows.append({
                "finding_id":                        f"F-{str(uuid.uuid4())[:6].upper()}",
                "CMBSMain":                      CMBSMain,
                "site":                              ps.site_norm,
                "finding_morphology":                "Not documented",
                "number_categorical":                inferred_count,
                "size_mm":                           "Not documented",
                "finding_intervention_performed":    "Not documented" if is_polyp_case else "Biopsy only",
                "finding_intervention_completeness": "Not reported" if is_polyp_case else "Not applicable",
                "type":                              "Polyp" if is_polyp_case else "Biopsy",
                "diagnosis_group":                   diag_norm,
                "dysplasia":                         dys_norm,
                "path_site_matched":                 ps.site_norm,
                "path_match_score":                  "100%",
                "mixed_pathology":                   "No",
                "count_mismatch":                    "No",
                "negated":                           "No",
                "hedged":                            "No",
                "hedge_type":                        "N/A",
                "manual_curation":                   "Yes",
                "curation_reason": f"{base_reason} | {supplementary_flag}" if supplementary_flag else base_reason,
                "urgent_flag":                       "No",
                "extraction_discrepancy_flag":        "Yes",
            })
        # Supervisor feedback 03.07.26: explicitly flag when only the pathology
        # report contributes findings so manual review can see the source count
        # imbalance.
        return apply_report_count_discrepancy_reason(rows, endo_findings, path_specimens)

    rows = []
    matched_specimen_ids = set()

    for ef in endo_findings:
        if ef.is_left_in_situ:
            # Not excised — no tissue was sent to pathology, so this finding
            # should never consume/claim a pathology specimen.
            _append_row(
                rows, ef, CMBSMain,
                count=ef.count,
                diag_group="Unspecified polyp - polyp not retrieved",
                dysplasia="N/A",
                path_matched=False,
                best_specimen=None,
                best_score=0,
                mixed=False,
                count_mismatch=False,
                curation_reason="Not excised — left in situ, no pathology expected"
            )
            continue

        best_specimen = None
        best_score = 0
        # Supervisor feedback 26.06.26: when pathology has separate same-site
        # specimens, avoid reusing the first specimen for every matching
        # endoscopy row. Fall back to all specimens only when nothing unused
        # matches.
        candidate_specimens = [ps for ps in path_specimens if id(ps) not in matched_specimen_ids] or path_specimens
        for ps in candidate_specimens:
            score = site_match_score(ef.site, ps.site_norm)
            # Supervisor feedback: when multiple specimens tie on site score,
            # prefer the one whose specimen_type matches the endoscopy finding
            # (a Polyp finding should match a Polyp specimen over a same-site
            # Biopsy specimen, not just whichever appears first in the list).
            if score > best_score or (
                score == best_score and score > 0
                and best_specimen is not None
                and ps.specimen_type == ef.finding_type
                and best_specimen.specimen_type != ef.finding_type
            ):
                best_score = score
                best_specimen = ps

        path_matched = best_specimen is not None and best_score >= 60

        if not path_matched:
            if ef.completeness == "Not retrieved":
                endo_diag = "Unspecified polyp - polyp not retrieved"
                endo_dys = "N/A"
            else:
            # Supervisor feedback (1497976): if the endoscopist explicitly
            # calls unretrieved polyps hyperplastic, preserve that as the best
            # available diagnosis instead of "Not documented".
                endo_diag = "Hyperplastic polyp" if re.search(r'\bhyperplastic\b', ef.raw_text, re.I) else "Not documented"
                endo_dys = "N/A" if endo_diag == "Hyperplastic polyp" else "Not documented"
            _append_row(
                rows, ef, CMBSMain,
                count=ef.count,
                diag_group=endo_diag,
                dysplasia=endo_dys,
                path_matched=False,
                best_specimen=None,
                best_score=0,
                mixed=False,
                count_mismatch=False,
                curation_reason="No pathology match"
            )
            continue

        matched_specimen_ids.add(id(best_specimen))
        diagnoses = best_specimen.diagnoses

        count_mismatch = False
        count_mismatch_reason = "N/A"

        if ef.finding_type == "Polyp" and best_specimen.fragment_count_total > 0:
            frag_total = best_specimen.fragment_count_total
            if not best_specimen.fragment_count_certain:
                count_mismatch = True
                count_mismatch_reason = (
                    f"Pathology reports 'multiple fragments' — unable to verify against endoscopy count of {ef.count}"
                )
            elif frag_total < ef.count:
                count_mismatch = True
                count_mismatch_reason = (
                    f"Count mismatch — endoscopy: {ef.count} polyp(s), pathology: {frag_total} fragment(s). "
                    f"Possible incomplete retrieval."
                )
            elif frag_total > ef.count and ef.count > 1:
                count_mismatch = True
                count_mismatch_reason = (
                    f"More fragments ({frag_total}) than polyps ({ef.count}) — possible labelling error."
                )

        if len(diagnoses) == 1:
            diag, dys, _ = diagnoses[0]
            curation_reason = count_mismatch_reason if count_mismatch else "N/A"
            _append_row(
                rows, ef, CMBSMain,
                count=ef.count,
                diag_group=diag,
                dysplasia=dys,
                path_matched=True,
                best_specimen=best_specimen,
                best_score=best_score,
                mixed=False,
                count_mismatch=count_mismatch,
                curation_reason=curation_reason
            )
        else:
            sorted_diags = sorted(
                diagnoses,
                key=lambda d: (
                    DIAGNOSIS_SEVERITY.get(d[0], 0),
                    DYSPLASIA_SEVERITY.get(d[1], 0)
                ),
                reverse=True
            )

            total_frags = sum(d[2] for d in sorted_diags)
            remaining_polyps = ef.count

            for i, (diag, dys, frag_count) in enumerate(sorted_diags):
                if remaining_polyps <= 0:
                    break

                if i == len(sorted_diags) - 1:
                    group_count = remaining_polyps
                else:
                    group_count = max(1, round(ef.count * frag_count / total_frags))
                    group_count = min(group_count, remaining_polyps)

                reasons = []
                if count_mismatch:
                    reasons.append(count_mismatch_reason)
                reasons.append("Mixed pathology at site — counts distributed proportionally")
                curation_reason = " | ".join(reasons)

                _append_row(
                    rows, ef, CMBSMain,
                    count=group_count,
                    diag_group=diag,
                    dysplasia=dys,
                    path_matched=True,
                    best_specimen=best_specimen,
                    best_score=best_score,
                    mixed=True,
                    count_mismatch=count_mismatch,
                    curation_reason=curation_reason,
                    size_override=ef.size
                )
                remaining_polyps -= group_count

    # Supervisor feedback (1516218 and similar): when endoscopy has findings
    # but pathology contains an additional unmatched polyp specimen, keep a
    # pathology-only row so manual verification can see the discrepancy.
    for ps in path_specimens:
        if id(ps) in matched_specimen_ids:
            continue
        if ps.site_norm == "Unknown":
            continue
        if ps.specimen_type == "Biopsy" and ps.site_norm == "Unknown":
            continue
        diag, dys = worst_diagnosis(ps.diagnoses)
        diag_norm, _ = normalise(diag, DIAGNOSIS_GROUP_VOCAB, threshold=65)
        dys_norm, _ = normalise(dys, DYSPLASIA_VOCAB, threshold=65)
        diag_norm, dys_norm = postprocess_pathology_result(diag_norm, dys_norm)
        if diag_norm in ("Normal tissue", "Not documented") and ps.specimen_type != "Biopsy":
            continue
        if ps.specimen_type != "Biopsy" and not pathology_implies_polyp(diag_norm, ps.raw_text):
            continue
        is_polyp_case = ps.specimen_type != "Biopsy"

        base_reason = (
            "Pathology-only polyp finding — no matching endoscopy row"
            if is_polyp_case
            else "Biopsy-only pathology specimen — no matching endoscopy row"
        )

        rows.append({
            "finding_id": f"F-{str(uuid.uuid4())[:6].upper()}",
            "CMBSMain": CMBSMain,
            "site": ps.site_norm,
            "finding_morphology": "Not documented",
            "number_categorical": ((ps.fragment_count_total if 0 < ps.fragment_count_total < 99 else 1)
            if is_polyp_case else None),
            "size_mm": "Not documented",
            "finding_intervention_performed": "Not documented" if is_polyp_case else "Biopsy only",
            "finding_intervention_completeness": "Not reported" if is_polyp_case else "Not applicable",
            "type": "Polyp" if is_polyp_case else "Biopsy",
            "diagnosis_group": diag_norm,
            "dysplasia": dys_norm,
            "path_site_matched": ps.site_norm,
            "path_match_score": "100%",
            "mixed_pathology": "No",
            "count_mismatch": "No",
            "negated": "No",
            "hedged": "No",
            "hedge_type": "N/A",
            "manual_curation": "Yes",
            "curation_reason": f"{base_reason} | {supplementary_flag}" if supplementary_flag else base_reason,
            "urgent_flag": "No",
            "extraction_discrepancy_flag": "Yes",
        })

    # Supervisor feedback 03.07.26: add explicit endoscopy/path report count
    # imbalance wording after all matched and unmatched rows have been built.
    return apply_report_count_discrepancy_reason(rows, endo_findings, path_specimens)