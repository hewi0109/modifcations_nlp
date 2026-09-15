import re
from datetime import date
from spacy_engine import SPACY_ENGINE
from vocab import INTERVENTION_VOCAB, POOR_PREP_QUALITY_VALUES, INTUBATION_DISTANCE_VOCAB, BOWEL_PREP_GIVEN_VOCAB, BOWEL_PREP_QUALITY_VOCAB
from textClean import normalise, is_missing_report_text

def extract_procedure_finding_terms(note: str) -> list[str]:
    """Capture patient-level non-polyp findings for Procedure_finding_2..5."""
    # Supervisor feedback 26.06.26: these belong on the Procedure sheet, not
    # repeated on finding rows. Supervisor feedback 03.07.26 renames
    # diverticula/diverticulosis to "Diverticulosis" and adds Melanosis coli.
    text = str(note or "")
    candidates = []
    patterns = [
        (r'\bdivert(?:icul|cul|c)\w*\b', "Diverticulosis"),
        (r'\bha?emorrh\w*\b', "Haemorrhoids"),
        (r'\bmelanosis\s+coli\b', "Melanosis coli"),
    ]
    for pat, label in patterns:
        m = re.search(pat, text, re.I)
        if m:
            candidates.append((m.start(), label))
    candidates.sort(key=lambda item: item[0])
    terms = []
    for _, label in candidates:
        if label not in terms:
            terms.append(label)
    return terms[:4]


def extract_other_findings(note: str) -> str:
    """Backward-compatible text form of the Procedure_finding_2..5 values."""
    terms = extract_procedure_finding_terms(note)
    return "; ".join(terms) if terms else "None"


def apply_procedure_finding_terms(proc: dict, note: str, no_endoscopy_report: bool = False) -> dict:
    """Populate Procedure_finding_2..5 from non-polyp findings."""
    if no_endoscopy_report:
        values = ["No Endoscopy report", "", "", ""]
    else:
        terms = extract_procedure_finding_terms(note)
        values = (terms + ["None", "", "", ""])[:4] if not terms else (terms + ["", "", "", ""])[:4]
    for idx, value in enumerate(values, start=2):
        proc[f"procedure_finding_{idx}"] = value
    proc["other_findings"] = "; ".join(v for v in values if v and v != "None") or values[0] or "None"
    return proc




# ─────────────────────────────────────────────────────────────────
# SIZE EXTRACTION
# ─────────────────────────────────────────────────────────────────
def extract_size(text: str) -> str:
    # SIZE CAN BE WRITTEN 3 DIMENSIONALLY, 2 DIMENSIONALLY, AS A RANGE OR AS
    # A SINGLE MEASUREMENT, mm can also appear on either the first, second or last number
    # Range: "4 to 6 mm" or "4-6mm"

    # Exclude distance/location phrasing -- these describe position, not
    # size. Covers "from the anal verge", "FAV" shorthand, and
    # "proximally"/"distally"/"proximal to"/"distal to" markers. The
    # optional plural "s" lives INSIDE the lookahead, not as an optional
    # part of the main unit match -- putting it on the main match let the
    # regex engine backtrack past it to dodge the exclusion entirely.
    distance_re = (
        r'(?:from\s+(?:the\s+)?(?:anal\s+verge|anus|ileocaecal\s+valve|dentate\s+line)'
        r'|FAV\b'
        r'|(?:proximally|distally)\b'
        r'|proximal\s+to|distal\s+to)'
    )

    m = re.search(
        rf'(\d+(?:\.\d+)?)\s*(?:to|-)\s*(\d+(?:\.\d+)?)\s*(mm|cm)(?!s?\s*{distance_re})',
        text, re.I
    )
    if m:
        lo, hi, unit = float(m.group(1)), float(m.group(2)), m.group(3).lower()
        if unit == "cm":
            lo, hi = lo * 10, hi * 10
        return f"{int(lo)}-{int(hi)}mm"

    # Single: "7 mm"
    m = re.search(rf'(\d+(?:\.\d+)?)\s*(mm|cm)(?!s?\s*{distance_re})', text, re.I)
    if m:
        val, unit = float(m.group(1)), m.group(2).lower()
        if unit == "cm":
            val *= 10
        return f"{int(val)}mm"

    return "Not documented"


# ─────────────────────────────────────────────────────────────────
# INTERVENTION EXTRACTION
# ─────────────────────────────────────────────────────────────────
INTERVENTION_RAW = [
    (re.compile(r'EMR.*piecemeal|piecemeal.*EMR', re.I), "EMR - piecemeal"),
    (re.compile(r'EMR.*en.?bloc|en.?bloc.*EMR', re.I), "EMR - en bloc"),
    (re.compile(r'\bESD\b', re.I), "ESD"),
    (re.compile(r'piecemeal.{0,30}\bsnare\b|\bsnare.{0,30}piecemeal', re.I), "Piecemeal snare"),
    (re.compile(r'cold\s+snare.{0,100}(?:hot\s+snare|heat).{0,40}completion', re.I), "Cold snare"),
    (re.compile(r'hot\s+snare', re.I), "Hot snare"),
    (re.compile(r'cold\s+snare', re.I), "Cold snare"),
    (re.compile(r'surgical\s+excision|surgery', re.I), "Surgical excision"),
    (re.compile(r'biopsy\s+only|biopsied\s+only', re.I), "Biopsy only"),
    (re.compile(r'not\s+excised|not\s+removed|left\s+in\s+situ', re.I), "Not excised"),
    (re.compile(r'\bsnare\b', re.I), "Cold snare"),   # fallback
    (re.compile(r'\bbiops(?:y|ied)\b', re.I), "Biopsy only"),
    (re.compile(r'cold\s+forceps', re.I), "Cold forceps"),
    (re.compile(r'hot\s+forceps', re.I), "Hot forceps"),

]

def extract_intervention_raw(text: str) -> str:
    if re.search(r'polypectomy\s+was\s+not\s+attempted|no\s+(?:biopsies|specimens).*collected|no\s+biopsies\s+or\s+other\s+specimens', text, re.I):
        return "Not excised"
    if re.search(r'\bdestroyed\b|diathermy|ablated|argon\s+plasma', text, re.I) and not re.search(r'\bsnare\b|\bexcis(?:ed|ion)\b|\bremoved?\b', text, re.I):
        return "Not excised"
    for pat, label in INTERVENTION_RAW:
        if pat.search(text):
            return label
    for ent in SPACY_ENGINE.entities(text, "INTERVENTION"):
        interv_norm, score = normalise(ent, INTERVENTION_VOCAB, threshold=60)
        if score >= 0.60 and not interv_norm.startswith("[UNMATCHED"):
            return interv_norm
    return "Not documented"


# ─────────────────────────────────────────────────────────────────
# COMPLETENESS EXTRACTION
# ─────────────────────────────────────────────────────────────────
def extract_completeness_raw(text: str) -> str:
    t = text.lower()
    if re.search(r'polypectomy\s+was\s+not\s+attempted|no\s+(?:biopsies|specimens).*collected|no\s+biopsies\s+or\s+other\s+specimens', t):
        return "Not retrieved"
    if re.search(r'not retrieved\b', t):
        return "Not retrieved"
    if re.search(r'resection and retrieval were complete|complete resection|retrieved.*complete', t):
        return "Complete"
    if re.search(r'resection was complete\b', t):
        return "Complete"
    if re.search(r'\bincomplete\b|\bpartial\b', t):
        return "Incomplete"
    if re.search(r'not reported\b', t):
        return "Not reported"
    if re.search(r'not clear\b|unclear\b', t):
        return "Not clear"
    return "Not documented"


# ─────────────────────────────────────────────────────────────────
# STAGE 1: ENDOSCOPY PROCEDURE EXTRACTOR
# ─────────────────────────────────────────────────────────────────
def _clean_provider_candidate(text: str) -> str:
    text = re.sub(r'_?x000D_|_?x000A_|\r|\n', ' ', str(text), flags=re.I)
    # Supervisor feedback 08.06.26 follow-up: some Consultant header lines
    # place role/demographic fields on the same line as the provider name.
    text = re.split(
        r'\b(?:Date of Birth|DOB|Patient Name|Gender|Age|MRN|Medicare|Room|Note Status|Admit Type|MBS Code)\b\s*:?',
        text,
        maxsplit=1,
        flags=re.I
    )[0]
    text = re.sub(
        r'\b(?:Gender|Indications?|Referring Physician|Requesting Physician|Providers?|'
        r'Medications?|Procedure|Findings?|Complications?|Scope Withdrawal Time)\b.*$',
        '',
        text,
        flags=re.I
    )
    text = re.sub(r'\s+', ' ', text).strip(" :-\t")
    if not text or text.lower() in {"not documented", "none", "nan"}:
        return ""
    text = re.sub(
        r'\s*,\s*(?:Consultant|Colorectal Consultant|Colorectal Surgeon|General Surgeon|'
        r'Gastroenterologist|Fellow|MD|FRACS|MBBS|BMed|PhD|FACG|FRACP)\b.*$',
        '',
        text,
        flags=re.I
    )
    text = re.sub(r'\s+,?\s*$', '', text).strip()

    name_match = re.search(
        r'\b(?:Dr|Doctor|Prof|Professor|A/Prof|Mr|Ms|Mrs|Miss)\.?\s+'
        r'[A-Z][A-Za-z\'.-]+(?:\s+[A-Z][A-Za-z\'.-]+){0,3}',
        text
    )
    if name_match:
        return name_match.group(0).strip()

    name_match = re.search(
        r'\b[A-Z][A-Za-z\'.-]+(?:\s+[A-Z][A-Za-z\'.-]+){1,3}\b',
        text
    )
    if name_match:
        return name_match.group(0).strip()

    return text


def extract_procedure_provider(note: str) -> str:
    """Extract the endoscopist/provider name reported in the procedure note.

    Supervisor feedback 08.06.26: provider names can appear as Consultant,
    Providers, or repeated near the report footer. Prefer labelled provider
    fields, then fall back to repeated doctor-name patterns in the report.
    """
    if is_missing_report_text(note):
        return "No Endoscopy report"

    text = re.sub(r'_?x000D_|_?x000A_|\r', '\n', str(note), flags=re.I)
    candidates = []

    for label in ("Consultant", "Providers?", "Endoscopist", "Proceduralist"):
        for m in re.finditer(rf'^\s*{label}\s*:\s*(.+)$', text, re.I | re.M):
            cleaned = _clean_provider_candidate(m.group(1))
            if cleaned:
                candidates.append(cleaned)

    # Footer fallback: repeated title-name patterns often identify the same
    # provider where the labelled fields are absent or malformed.
    for m in re.finditer(
        r'\b(?:Dr|Doctor|Prof|Professor|A/Prof|Mr|Ms|Mrs|Miss)\.?\s+'
        r'[A-Z][A-Za-z\'.-]+(?:\s+[A-Z][A-Za-z\'.-]+){1,3}',
        text
    ):
        candidates.append(_clean_provider_candidate(m.group(0)))

    if not candidates:
        return "Not documented"

    counts = {}
    display = {}
    for candidate in candidates:
        key = re.sub(r'\W+', '', candidate).lower()
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
        display.setdefault(key, candidate)

    if not counts:
        return "Not documented"
    best_key = sorted(counts, key=lambda k: (-counts[k], candidates.index(display[k])))[0]
    return display[best_key]


def _clean_report_field(text: str) -> str:
    text = re.sub(r'_?x000D_|_?x000A_|\r|\n', ' ', str(text or ""), flags=re.I)
    text = re.split(
        r'_{5,}|Number of Addenda|Note Initiated On|CC Letter to|A joint service of the Departments',
        text,
        maxsplit=1,
        flags=re.I
    )[0]
    text = re.sub(r'\s+', ' ', text).strip(" :-\t")
    return text if text else "Not documented"


def extract_labelled_field(note: str, label: str, stop_labels: list[str]) -> str:
    """Extract a single labelled report field while tolerating wrapped text."""
    text = re.sub(r'_?x000D_|_?x000A_|\r', '\n', str(note or ""), flags=re.I)
    stops = "|".join(re.escape(s) for s in stop_labels)
    m = re.search(
        rf'\b{re.escape(label)}\s*:\s*(.*?)(?=\n?\s*(?:{stops})\s*:|\Z)',
        text,
        re.I | re.S
    )
    if not m:
        return "Not documented"
    return _clean_report_field(m.group(1))


def impression_has_no_specimens_collected(note: str) -> bool:
    """True when the Impression section explicitly says no specimens collected."""
    # Supervisor feedback 03.07.26: preserve the usual manual-curation logic
    # unless the Impression section directly documents "No specimens collected".
    text = re.sub(r'_?x000D_|_?x000A_|\r', '\n', str(note or ""), flags=re.I)
    m = re.search(
        r'\bImpression\s*:\s*(.*?)(?=\n?\s*(?:Recommendation|Complications|Procedure|Findings|Scope Withdrawal Time|Images)\b\s*:|\Z)',
        text,
        re.I | re.S,
    )
    if not m:
        return False
    impression = re.sub(r'\s+', ' ', m.group(1))
    return bool(re.search(r'\bNo\s+specimens?\s+collected\b', impression, re.I))


def extract_procedure_room(note: str) -> str:
    m = re.search(
        r'\bRoom\s*:\s*(.*?)(?=\s+\b(?:Age|Date of Birth|DOB|Gender|Note Status|MBS Code|Medicare|Procedure Date|MRN)\b\s*:|\n|$)',
        str(note or ""),
        re.I | re.S
    )
    return _clean_report_field(m.group(1)) if m else "Not documented"

# -----------------------------------------------------------------
# REPORT DATE EXTRACTION
# -----------------------------------------------------------------
PROCEDURE_DATE_RE = re.compile(
    r'\bProcedure\s+Date\s*:?\s*(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{4})\b',
    re.I
)

def extract_report_date(note: str) -> str:
    """Extract 'Procedure Date: D/M/YYYY' from the report header

    Returns IDO YYYY-MM-DD so the column sort correctly into excel and
    compares cleanly afainst the source procedure_date"""
    if is_missing_report_text(note):
        return "No Endoscopy report"

    text = re.sub(r'_?x000D_|_?x000A_|\r', '\n', str(note), flags=re.I)
    m= PROCEDURE_DATE_RE.search(text)
    if not m:
        return "Not documented"

    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try: return date(year, month, day).isoformat()
    except ValueError:
        return "Not documented"

def resolve_procedure_date(report_date: str, source_date) -> str:
    """Use the date printed in the report; fall back to the source column"""
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(report_date or "")):
        return report_date
    source=str(source_date or "").strip()
    return source if source else (report_date or "Not Documented")



def extract_medication(note: str) -> str:
    text_for_prefix = re.sub(r'_?x000D_|_?x000A_|\r|\n', ' ', str(note or ""), flags=re.I)
    text_for_prefix = re.sub(r'\s+', ' ', text_for_prefix)
    if re.search(r'\bMedications\s*:\s*(?:Comorbidities|Patient Profile)\b', text_for_prefix, re.I):
        # Supervisor feedback 03.07.26: if the "Medications" field is only a
        # patient-profile/comorbidity handoff, it is not a medication value.
        return "N/A"

    value = extract_labelled_field(
        note,
        "Medications",
        [
            "Patient Profile",
            "Comorbidities",
            "Procedure",
            "Findings",
            "Complications",
            "Recommendation",
            "Impression",
            "Images",
        ]
    )
    # Supervisor feedback 03.07.26: Medication sometimes swallowed Patient
    # Profile/Comorbidities text when those subheadings followed immediately
    # after "Medications:". Keep only the actual medication value.
    value = re.split(r'\b(?:Patient Profile|Comorbidities)\b\s*:?', value, maxsplit=1, flags=re.I)[0]
    value = _clean_report_field(value)
    if re.match(r'^\s*Comorbidities\b', value, re.I):
        return "N/A"
    return value


def extract_complications_reported(note: str) -> str:
    """Return the complications field exactly as reported, not an inferred label."""
    value = extract_labelled_field(
        note,
        "Complications",
        ["Procedure", "Findings", "Impression", "Recommendation", "Scope Withdrawal Time", "Images"]
    )
    # Supervisor feedback 03.07.26: keep the complication statement but remove
    # trailing full stops and footer/provider text accidentally captured after
    # the field, such as "Dr ..." or "Supervisor Override".
    value = re.split(r'\bSupervisor Override\b', value, maxsplit=1, flags=re.I)[0]
    value = re.sub(r'\.\s*(?:Dr|Mr|Ms|Mrs|Prof|Professor)\b.*$', '', value, flags=re.I)
    value = re.sub(r'\s+', ' ', value).strip(" .;:-")
    return value if value else "Not documented"


def extract_instrument_name(note: str) -> str:
    """Extract colonoscope/gastroscope model and code from the procedure text."""
    text = re.sub(r'_?x000D_|_?x000A_|\r|\n', ' ', str(note or ""), flags=re.I)
    text = re.sub(r'\s+', ' ', text)
    candidates = []

    def clean_instrument_candidate(raw: str) -> str:
        value = _clean_report_field(raw)
        value = re.sub(r'\bTeam Time Out\b.*$', '', value, flags=re.I).strip(" :-")
        # Supervisor feedback 03.07.26: some header Instrument Name fields are
        # followed by "Attending MD: ..."; stop before provider names.
        value = re.sub(r'\b(?:Attending\s+MD|Procedure|Referring Physician|Requesting Physician|Indications|Providers|Medications)\b.*$', '', value, flags=re.I).strip(" :-")
        value = re.sub(r'\s*,\s*', ', ', value)
        value = re.sub(r'\s*-\s*', '-', value)
        value = re.sub(r'\s+', ' ', value).strip(" :-")
        return value if value else "Not documented"

    def add_candidate(raw: str, start: int, source_rank: int = 0) -> None:
        display = clean_instrument_candidate(raw)
        if display == "Not documented":
            return
        is_pediatric = bool(re.search(r'\b(?:paed|pediatric|paediatric|PCF)\b', display, re.I))
        candidates.append((source_rank, is_pediatric, start, display))

    # Supervisor feedback 03.07.26: many missed instruments are reported in
    # the header next to Medicare number rather than in the procedure sentence.
    for m in re.finditer(
        r'\bInstrument\s+Name\s*:\s*(.{2,140}?)(?=\s+(?:Attending\s+MD|Team Time Out|Procedure\s*:|Referring Physician|Requesting Physician|Indications\s*:|Providers\s*:|Medications\s*:|_{5,}|$))',
        text,
        re.I,
    ):
        add_candidate(m.group(1), m.start(), source_rank=0)

    pattern = re.compile(
        r'\b(?:The\s+)?(?:Colonoscope|Gastroscope|Endoscope)\s*'
        r'(?P<paed>\((?:Paed|Pediatric|Paediatric)\))?\s*[-:]?\s*'
        r'(?P<model>[A-Z]{2,4}-[A-Z0-9]+)\s*[:,;]?\s*(?P<code>\d{5,8})',
        re.I
    )
    for m in pattern.finditer(text):
        paed = _clean_report_field(m.group("paed") or "")
        model = m.group("model").upper()
        code = m.group("code")
        display = f"{model}: {code}"
        if m.group("paed"):
            display = f"{paed} {display}"
        add_candidate(display, m.start(), source_rank=1)

    # Supervisor feedback 03.07.26: also accept plain/internal scope IDs such
    # as "The Colonoscope C4026 was introduced" and "IC762K041", and header
    # variants such as "Colonoscope PCF H190 DL2500606".
    for m in re.finditer(
        r'\b(?:The\s+)?(?P<scope>Colonoscope|Gastroscope|Endoscope)\s+'
        r'(?P<id>(?:[A-Z]{1,3}\d{3,4}[A-Z]?\d{0,4}|IC\d{3}K\d{3}|'
        r'(?:PCF|CF|GIF)\s*-?\s*[A-Z0-9]{3,8}(?:\s+[A-Z]{1,3}\d{5,8})?))'
        r'\s+(?:was\s+)?(?:introduced|advanced)\b',
        text,
        re.I,
    ):
        add_candidate(f"{m.group('scope')} {m.group('id')}", m.start(), source_rank=1)

    if not candidates:
        return "Not documented"

    # Supervisor feedback 19.06.26: prefer a paediatric scope when both adult
    # and paediatric instruments are mentioned during a difficult procedure.
    candidates.sort(key=lambda item: (item[0], not item[1], item[2]))
    return candidates[0][3]


def _clean_reason_phrase(text: str) -> str:
    text = re.sub(r'\s+', ' ', str(text or "")).strip()
    text = re.sub(r'([a-z])(?=(?:The quality|Successful completion|The patient tolerated|BBPS|Bowel preparation|Findings)\b)', r'\1 ', text)
    text = re.sub(r'\s*(?:The quality|Successful completion|The patient tolerated|BBPS|Bowel preparation|Findings):?.*$', '', text, flags=re.I)
    return text.strip(" .;")


def extract_difficulty_and_reason(note_clean: str) -> tuple[str, str]:
    """Extract procedure difficulty and the documented 'due to...' reason."""
    # Supervisor feedback 26.06.26: "somewhat difficult" and "performed with
    # difficulty due to..." are true difficulty statements; if no difficulty is
    # documented, the reason column should be NA rather than Not documented.
    if re.search(r'\bwithout difficulty\b|\bwith ease\b|\bperformed (?:easily|smoothly)\b', note_clean, re.I):
        return "No", "NA"

    MOD = r'(?:some|mild|moderate|significant|considerable|substantial|extreme|great)'
    ADV = r'(?:somewhat|mildly|moderately|very|unusually|extremely|significantly)'

    patterns = [
        rf'\bthe\s+colonoscopy\s+was\s+({ADV}?\s*(?:technically\s+)?(?:difficult|complex)\b(?:\s+and\s+complex)?)(?:\s+(due to [^\.]+))?',
        rf'\bthe\s+colonoscopy\s+was\s+performed\s+with\s+({MOD}?\s*difficulty)\b(?:\s+(due to [^\.]+))?',
        rf'\bperformed\s+with\s+({MOD}?\s*difficulty)\b(?:\s+(due to [^\.]+))?',
        rf'\b(?:technically\s+)?((?:difficult|complex)\b(?:\s+and\s+complex)?)(?:\s+(due to [^\.]+))?',
    ]
    for pat in patterns:
        m = re.search(pat, note_clean, re.I)
        if not m:
            continue
        difficulty = _clean_reason_phrase(m.group(1)).lower()
        groups = m.groups()
        reason = _clean_reason_phrase(groups[1]) if len(groups) > 1 and groups[1] else "Not documented"
        if difficulty:
            return "Yes", reason

    return "Not documented", "Not documented"


def extract_incomplete_reason(note_clean: str, difficulty_reason: str, prep_quality: str = "") -> str:
    """Prefer the documented reason for incompletion over the reached extent."""
    # Supervisor feedback 26.06.26: intubation extent is captured separately,
    # so Reason_incomplete should explain why completion failed.
    reasons = []
    if difficulty_reason and difficulty_reason not in {"Not documented", "NA"}:
        reasons.append(re.sub(r'^\s*due to\s+', '', difficulty_reason, flags=re.I).strip())

    if re.search(r'\bthis\s+(?:is|was)\s+the\s+intended\s+extent\b', note_clean, re.I):
        reasons.append("Intended extent")

    if str(prep_quality).strip().lower() in POOR_PREP_QUALITY_VALUES:
        reasons.append("Poor Bowel Prep")

    specific_patterns = [
        r'\blumen\s+was\s+not\s+able\s+to\s+be\s+insufflated.*?(?=\.|\bThe quality\b|\bBowel preparation\b|\bFindings\b|$)',
        r'\bnot\s+able\s+to\s+be\s+insufflated.*?(?=\.|\bThe quality\b|\bBowel preparation\b|\bFindings\b|$)',
        r'\bcould\s+not\s+(?:be\s+)?(?:advanced|passed|completed).*?(?=\.|\bThe quality\b|\bBowel preparation\b|\bFindings\b|$)',
        r'\bunable\s+to\s+(?:advance|pass|complete|intubate).*?(?=\.|\bThe quality\b|\bBowel preparation\b|\bFindings\b|$)',
        r'\baborted\s+due\s+to\s+.*?(?=\.|\)|\bThe quality\b|\bBowel preparation\b|\bFindings\b|$)',
        r'\btolerated\s+the\s+procedure\s+poorly\s+due\s+to\s+.*?(?=\.|\)|\bThe quality\b|\bBowel preparation\b|\bFindings\b|$)',

    ]
    for pat in specific_patterns:
        m = re.search(pat, note_clean, re.I)
        if m:
            matched = re.sub (
                r'^\s*(?:aborted\s+due\s+to|tolerated\s+the\s+procedure\s+poorly\s+due\s+to)\s+',
                '', m.group(0), flags=re.I
            )
            reasons.append(_clean_reason_phrase(m.group(0)))

    deduped = []
    for reason in reasons:
        reason = reason.strip(" .;")
        reason_l = reason.lower()
        if reason and not any(reason_l in r.lower() or r.lower() in reason_l for r in deduped):
            deduped.append(reason)
    return "; ".join(deduped) if deduped else ""

def strip_indications_section(text: str) -> str:
    """Remove the Indications field before bowel-prep-quality inference, so
    history/context text (e.g. 'inadequate bowel prep at last colonoscopy')
    isn't misread as describing the current procedure's prep.

    note_clean is already flattened (newlines collapsed to spaces) by the
    time this runs, so the removal can't rely on finding a literal '\\n' to
    know where Indications ends -- confirmed this exact issue: the original
    version fell through to end-of-string and deleted everything after
    Indications, including the Findings section. It now stops at the next
    recognised field label instead."""
    return re.sub(
        r'Indications:.*?(?=\b(?:Providers|Medications|Procedure|Findings|Impression|'
        r'Recommendation|Complications|Scope Withdrawal)\s*:|\Z)',
        ' ', text, flags=re.I | re.S
    )

def extract_procedure(note: str, CMBSMain: str) -> dict:
    if is_missing_report_text(note):
        # Supervisor feedback 01.06.26: make absent endoscopy reports explicit
        # from the first extraction fields onward so nurse review can triage them.
        no_report = "No Endoscopy report"
        return {
            "CMBSMain": CMBSMain,
            "procedure_completeness": no_report,
            "reason_incomplete": "No endoscopy report",
            "intubation_distance": no_report,
            "bowel_prep_given": no_report,
            "bowel_prep_quality": no_report,
            "bbps_right": no_report,
            "bbps_transverse": no_report,
            "bbps_left": no_report,
            "total_bbps_score": no_report,
            "withdrawal_time": no_report,
            "manual_curation": "Yes - No endoscopy report",
            "procedure_room": no_report,
            "procedure_provider": no_report,
            "instrument_name": no_report,
            "medication": no_report,
            "tolerated_procedure": no_report,
            "difficulty": no_report,
            "difficulty_reason": "No endoscopy report",
            "complications": no_report,
            "other_findings": no_report,
        }

    note_clean = re.sub(r'_?x000D_', ' ', note, flags=re.I)
    note_clean = re.sub(r'\s+', ' ', note_clean).strip()
    procedure_provider = extract_procedure_provider(note)
    procedure_room = extract_procedure_room(note)
    report_date = extract_report_date(note)
    medication = extract_medication(note)
    instrument_name = extract_instrument_name(note)

         # ── Intubation distance ──────────────────────────────────────
    intubation_raw = "Not Reported"

    # Remove phrases that describe intended target only, not achieved extent
    note_extent = note_clean

    note_extent = re.sub(
        r'\bwith the intention of (?:advancing|reaching|intubating|passing) to the [a-z \-]+\b',
        ' ',
        note_extent,
        flags=re.I
    )
    note_extent = re.sub(
        r'\bintended to (?:advance|reach|intubate|pass) to the [a-z \-]+\b',
        ' ',
        note_extent,
        flags=re.I
    )
    note_extent = re.sub(
        r'\bplanned to (?:advance|reach|intubate|pass) to the [a-z \-]+\b',
        ' ',
        note_extent,
        flags=re.I
    )
    note_extent = re.sub(
        r'\battempted to (?:advance|reach|intubate|pass) to the [a-z \-]+\b',
        ' ',
        note_extent,
        flags=re.I
    )

    # Look for actual confirmed extent reached, from furthest / most specific downward
    extent_patterns = [
        # Terminal ileum / ileum
        # Supervisor feedback 08.06.26: keep true "advanced to terminal ileum"
        # ahead of anastomosis wording elsewhere in the same report, and treat
        # plain "advanced to the ileum" as terminal ileum for this field.
        (r'\b(?:advanced|passed|reached|intubated|inserted|insered).{0,50}\bterminal ileum\b', "Terminal Ileum"),
        (r'\bextent reached(?: was|:)? (?:the )?terminal ileum\b', "Terminal Ileum"),
        (r'\bcould only be advanced to the terminal ileum\b', "Terminal Ileum"),
        (r'\b(?:advanced|passed|reached|intubated|inserted|insered).{0,50}\b\d+(?:\.\d+)?\s*cm\s+(?:in(?:to|side)?|within)\s+(?:the\s+)?ileum\b', "Terminal Ileum"),
        (r'\b(?:advanced|passed|reached|intubated|inserted|insered).{0,50}\b(?:in(?:to|side)?|within)\s+(?:the\s+)?ileum\b', "Terminal Ileum"),
        (r'\b(?:advanced|passed|reached|intubated|inserted|insered).{0,50}\b(?:the\s+)?ileum\b', "Terminal Ileum"),

        # Anastomosis & Surgical Stoma
        (r'\b(?:advanced|passed|reached|intubated|inserted).{0,50}\b(?:ileocolonic|ileo-colonic|colonic)?\s*anastomosis\b', "Anastomosis"),
        (r'\bextent reached(?: was|:)? (?:the )?(?:ileocolonic|ileo-colonic|colonic)?\s*anastomosis\b', "Anastomosis"),
        (r'\bcould only be advanced to the (?:ileocolonic|ileo-colonic|colonic)?\s*anastomosis\b', "Anastomosis"),
        (r'\b(?:advanced|passed|reached|intubated|inserted|insered).{0,50}\bsurgical stoma\b', "Surgical Stoma"),

        # Caecum
        (r'\b(?:advanced|passed|reached|intubated|inserted).{0,50}\bcaecum\b', "Caecum"),
        (r'\bextent reached(?: was|:)? (?:the )?caecum\b', "Caecum"),
        (r'\bcould only be advanced to the caecum\b', "Caecum"),
        (r'\bappendiceal orifice\b.{0,80}\bileocaecal valve\b|\bappendiceal orifice\b.{0,80}\bileocecal valve\b', "Caecum"),
        (r'\b(?:advanced|passed|reached|intubated|inserted|insered).{0,50}\btripartite caecal folds\b', "Caecum"),

	(r'\b(?:advanced|passed|reached|intubated|inserted).{0,50}\bcaecum\b', "Caecum"),
        (r'\b(?:advanced|passed|reached|intubated|inserted).{0,50}\bileocaecal valve\b', "Caecum"),
        (r'\b(?:advanced|passed|reached|intubated|inserted).{0,50}\bileocecal valve\b', "Caecum"),
        (r'\bextent reached(?: was|:)? (?:the )?caecum\b', "Caecum"),
        (r'\bcould only be advanced to the caecum\b', "Caecum"),
        (r'\bappendiceal orifice\b.{0,80}\bileocaecal valve\b|\bappendiceal orifice\b.{0,80}\bileocecal valve\b', "Caecum"),

        # Flexures and colon segments
        (r'\b(?:advanced|passed|reached|intubated|inserted).{0,50}\bhepatic flexure\b', "Hepatic Flexure"),
        (r'\bextent reached(?: was|:)? (?:the )?hepatic flexure\b', "Hepatic Flexure"),
        (r'\bcould only be advanced to the hepatic flexure\b', "Hepatic Flexure"),

        (r'\b(?:advanced|passed|reached|intubated|inserted).{0,50}\bsplenic flexure\b', "Splenic Flexure"),
        (r'\b(?:advanced|passed|reached|intubated|inserted|insered).{0,50}\bsplee?lnic flexure\b', "Splenic Flexure"),
        (r'\bextent reached(?: was|:)? (?:the )?splenic flexure\b', "Splenic Flexure"),
        (r'\bcould only be advanced to the splenic flexure\b', "Splenic Flexure"),

        (r'\b(?:advanced|passed|reached|intubated|inserted).{0,50}\bascending colon\b', "Ascending Colon"),
        (r'\bextent reached(?: was|:)? (?:the )?ascending colon\b', "Ascending Colon"),
        (r'\bcould only be advanced to the ascending colon\b', "Ascending Colon"),

        (r'\b(?:advanced|passed|reached|intubated|inserted).{0,50}\btransverse colon\b', "Transverse Colon"),
        (r'\bextent reached(?: was|:)? (?:the )?transverse colon\b', "Transverse Colon"),
        (r'\bcould only be advanced to the transverse colon\b', "Transverse Colon"),

        (r'\b(?:advanced|passed|reached|intubated|inserted).{0,50}\bdescending colon\b', "Descending Colon"),
        (r'\bextent reached(?: was|:)? (?:the )?descending colon\b', "Descending Colon"),
        (r'\bcould only be advanced to the descending colon\b', "Descending Colon"),
        # Supervisor feedback 08.06.26: manual QA maps 70-90 cm from anal
        # verge to descending colon in the sampled procedure records.
        (r'\b(?:advanced|passed|reached|intubated|inserted|insered).{0,50}\b(?:7[0-9]|8[0-9]|90)\s*cm\s+from\s+(?:the\s+)?anal verge\b', "Descending Colon"),

        # QA feedback 04.06.26: preserve rectosigmoid as its own intubation extent.
        # Newlines are already normalised above, so "advanced to the" on one line
        # and "rectosigmoid junction" on the next is still matched here.
        (r'\b(?:advanced|passed|reached|intubated|inserted).{0,70}\brecto[-\s]?(?:sigmoid|sgmoid)(?:\s+(?:junction|remnant))?\b', "Rectosigmoid Junction"),
        (r'\bextent reached(?: was|:)? (?:the )?recto[-\s]?(?:sigmoid|sgmoid)(?:\s+(?:junction|remnant))?\b', "Rectosigmoid Junction"),
        (r'\bcould only be advanced to the recto[-\s]?(?:sigmoid|sgmoid)(?:\s+(?:junction|remnant))?\b', "Rectosigmoid Junction"),

        (r'\b(?:advanced|passed|reached|intubated|inserted).{0,50}\bsigmoid colon\b', "Sigmoid Colon"),
        (r'\bextent reached(?: was|:)? (?:the )?sigmoid colon\b', "Sigmoid Colon"),
        (r'\bcould only be advanced to the sigmoid colon\b', "Sigmoid Colon"),
        (r'\b(?:advanced|passed|reached|intubated|inserted|insered).{0,50}\b(?:[2-5][0-9]|60)\s*cm\s+from\s+(?:the\s+)?anal verge\b', "Sigmoid Colon"),
        (r'\b(?:advanced|passed|reached|intubated|inserted|insered).{0,50}\bileoanal pouch\b', "Sigmoid Colon"),

        (r'\b(?:advanced|passed|reached|intubated|inserted|insered).{0,50}\brectum\b', "Rectum"),
        (r'\bextent reached(?: was|:)? (?:the )?rectum\b', "Rectum"),
        (r'\bcould only be advanced to the rectum\b', "Rectum"),
        (r'\b(?:advanced|passed|reached|intubated|inserted|insered).{0,50}\banus\b', "Rectum"),
    ]

    for pat, label in extent_patterns:
        if re.search(pat, note_extent, re.I):
            intubation_raw = label
            break

    intubation, _ = normalise(intubation_raw, INTUBATION_DISTANCE_VOCAB)

    # ── Bowel prep quality ───────────────────────────────────────
    # Only use explicit quality wording. DO NOT infer from BBPS.
    prep_quality_raw = "Not reported"
    note_clean_no_indications = strip_indications_section(note_clean)

    explicit_quality_patterns = [
        r'\bbowel prep(?:aration)? (?:was|is)\s+(excellent|good|adequate|fair|poor|inadequate|unsatisfactory)\b',
        r'\bquality of the bowel prep(?:aration)? (?:was|is)\s+(excellent|good|adequate|fair|poor|inadequate|unsatisfactory)\b',
        r'\bbowel prep(?:aration)? quality[:\s]+(excellent|good|adequate|fair|poor|inadequate|unsatisfactory)\b',
        r'\bprep quality[:\s]+(excellent|good|adequate|fair|poor|inadequate|unsatisfactory)\b',
    ]

    for pat in explicit_quality_patterns:
        m = re.search(pat, note_clean_no_indications, re.I)
        if m:
            prep_quality_raw = m.group(1).capitalize()
            break

     # "Borderline" bowel prep is treated as Fair -- it's not one of the
     # standard quality words itself, but reports use it to mean the same
     # thing as a fair/marginal prep.
    if prep_quality_raw.lower() == "not reported" and re.search(r'\bborderline bowel prep(?:aration)?\b',
        note_clean_no_indications, re.I):
        prep_quality_raw = "Fair"

    # ── Bowel prep given ─────────────────────────────────────────
    prep_given = "Not documented"
    m = re.search(
        r'\bbowel preparation used was\s+(.+?)(?=\.\s+[A-Z]|\.\s*$)',
        note_clean, re.I
    )
    if m:
        raw_prep = m.group(1).strip().rstrip('.')
        # Supervisor feedback (1467304): do not let low-threshold fuzzy
        # matching turn "2 Picolax and 1L Colonlytely" into "3L Colonlytely".
        prep_given, prep_score = normalise(raw_prep, BOWEL_PREP_GIVEN_VOCAB, threshold=82)
        picolax_combo = re.search(r'\b(?:picolax|picoprep)\b', raw_prep, re.I) and re.search(r'\bcolonlytely\b', raw_prep, re.I)
        if picolax_combo:
            m_pic = re.search(r'\b(\d+)\s*(?:x\s*)?(?:picolax|picoprep)\b|(?:picolax|picoprep)\s*(?:x\s*)?(\d+)\b', raw_prep, re.I)
            m_col = re.search(r'\b(\d+)\s*l\s*colonlytely\b|colonlytely\s*x\s*(\d+)\b', raw_prep, re.I)
            if m_pic and m_col:
                prep_given = f"Picoprep x{m_pic.group(1) or m_pic.group(2)}, Colonlytely x{m_col.group(1) or m_col.group(2)}"
                prep_given, _ = normalise(prep_given, BOWEL_PREP_GIVEN_VOCAB, threshold=82)

      # ── BBPS scores ──────────────────────────────────────────────
    bbps_r = bbps_t = bbps_l = total_bbps = "Not reported"

    # First isolate a likely BBPS block
    bbps_block = ""
    m = re.search(
        r'(?:BBPS|Boston Bowel Preparation Scale|bowel preparation was evaluated using the BBPS).{0,700}',
        note_clean,
        re.I
    )
    if m:
        bbps_block = m.group(0)
    else:
        m = re.search(r'(?:bowel preparation|prep).{0,700}', note_clean, re.I)
        if m:
            bbps_block = m.group(0)

    if bbps_block:
        # Standard named fields
        m = re.search(r'Right\s*Colon\s*=?\s*([0-3])\b', bbps_block, re.I)
        if m:
            bbps_r = m.group(1)

        m = re.search(r'Transverse\s*Colon\s*=?\s*([0-3])\b', bbps_block, re.I)
        if m:
            bbps_t = m.group(1)

        m = re.search(r'Left\s*Colon\s*=?\s*([0-3])\b', bbps_block, re.I)
        if m:
            bbps_l = m.group(1)

        # Short-form initials: R/T/L
        if bbps_r == "Not reported":
            m = re.search(r'\bR\s*=\s*([0-3])\b', bbps_block, re.I)
            if m:
                bbps_r = m.group(1)

        if bbps_t == "Not reported":
            m = re.search(r'\bT\s*=\s*([0-3])\b', bbps_block, re.I)
            if m:
                bbps_t = m.group(1)

        if bbps_l == "Not reported":
            m = re.search(r'\bL\s*=\s*([0-3])\b', bbps_block, re.I)
            if m:
                bbps_l = m.group(1)

        # Compact triplet format: BBPS 3/3/3 or BBPS score 3,3,3
        if "Not reported" in [bbps_r, bbps_t, bbps_l]:
            m = re.search(
                r'(?:BBPS|BPSS)(?:\s+score)?(?:\s+of)?[:\s]*([0-3])\s*[/,]\s*([0-3])\s*[/,]\s*([0-3])',
                bbps_block,
                re.I
            )
            if m:
                bbps_r, bbps_t, bbps_l = m.group(1), m.group(2), m.group(3)

        # Supervisor feedback 08.06.26: BBPS = 3 (1+1+1), where the
        # parenthesised values are right, transverse, and left respectively.
        if "Not reported" in [bbps_r, bbps_t, bbps_l]:
            m = re.search(
                r'(?:BBPS|BPSS)\s*=\s*([0-9]{1,2})\s*\(\s*([0-3])\s*\+\s*([0-3])\s*\+\s*([0-3])\s*\)',
                bbps_block,
                re.I
            )
            if m:
                total_bbps = m.group(1)
                bbps_r, bbps_t, bbps_l = m.group(2), m.group(3), m.group(4)

        # Total score
        m = re.search(
            r'total\s+BBPS\s+score\s+(?:equals?|was|is)\s*([0-9]{1,2})\b|'
            r'BBPS\s+total\s+score\s*(?:equals?|was|is)\s*([0-9]{1,2})\b|'
            r'BBPS\s+score\s+(?:equals?|was|is)\s*([0-9]{1,2})\b',
            bbps_block,
            re.I
        )
        if m:
            total_bbps = m.group(1) or m.group(2) or m.group(3)

    # Fallback: calculate total if all three segment scores are present
    if total_bbps == "Not reported" and all(x != "Not reported" for x in [bbps_r, bbps_t, bbps_l]):
        total_bbps = str(int(bbps_r) + int(bbps_t) + int(bbps_l))

    # Supervisor feedback 08.06.26: infer conservative prep quality only when
    # explicit quality wording was absent but the report clearly describes poor
    # preparation/visualisation or a well visualised colon.
    if prep_quality_raw.lower() == "not reported":
        if (
            re.search(r'\b(?:bowel prep(?:aration)?\s+was\s+)?not good\b', note_clean_no_indications, re.I)
            or re.search(r'\binadequate bowel prep(?:aration)?\b|\bpoor endoscopic visuali[sz]ation\b', note_clean_no_indications, re.I)
            or total_bbps == "0"
        ):
            prep_quality_raw = "Poor"
        elif re.search(r'\bentire colon was well visuali[sz]ed\b', note_clean_no_indications, re.I):
            prep_quality_raw = "Good"

    prep_quality, _ = normalise(prep_quality_raw, BOWEL_PREP_QUALITY_VOCAB)

    # ── Withdrawal time ──────────────────────────────────────────
    withdrawal_time = "Not documented"
    m = re.search(
        r'\b[Ss]cope\s+[Ww]ithdrawal\s+[Tt]ime[:\s]+(?:(\d+)\s+hours?\s+)?(\d+)\s+minutes?\s+(\d+)\s+seconds?\b',
        note_clean, re.I
    )
    if m:
        hrs = int(m.group(1) or 0)
        mins = int(m.group(2))
        secs = int(m.group(3))
        total_mins = hrs * 60 + mins
        withdrawal_time = f"{total_mins}:{secs:02d}"
    else:
        m = re.search(r'\b[Ss]cope\s+[Ww]ithdrawal\s+[Tt]ime[:\s]+(\d+)\s+[Mm]inutes?\b', note_clean, re.I)
        if m:
            withdrawal_time = f"{m.group(1)}:00"

    # ── Difficulty ───────────────────────────────────────────────
    difficulty, difficulty_reason = extract_difficulty_and_reason(note_clean)

    # ── Tolerated ────────────────────────────────────────────────

    if re.search(
            r"\b(?:patient\s+)?tolerated\s+the\s+procedure\s+"
            r"(?:poorly|with\s+(?:some\s+apparent\s+)?discomfort|with\s+difficulty)\b",
            note_clean,
            re.I,
    ):
        tolerated = "Poorly tolerated"

    elif re.search(
            r"\b(?:patient\s+)?tolerated\s+the\s+procedure\s+"
            r"(?:fairly\s+well|reasonably\s+well|adequately|fairly)\s*[,;:]?\s+but\b",
            note_clean,
            re.I,
    ):
        tolerated = "Poorly tolerated"

    elif re.search(
            r"\b(?:patient\s+)?tolerated\s+the\s+procedure"
            r"(?:\s+(?:well|fairly\s+well|reasonably\s+well|adequately|fairly))?\b",
            note_clean,
            re.I,
    ):
        tolerated = "Tolerated"

    else:
        tolerated = "Not documented"


    # ── Complications ────────────────────────────────────────────
    # Supervisor feedback 19.06.26: use the reported Complications field
    # verbatim; do not collapse perforation/bleeding to generic labels.
    complications = extract_complications_reported(note)

     # ── Procedure completeness ───────────────────────────────────
    if intubation in ["Caecum", "Terminal Ileum", "Anastomosis", "Surgical Stoma"]:
        completeness = "Complete"
        reason_incomplete = "N/A"
    elif intubation == "Not Reported":
        completeness = "Not documented"
        reason_incomplete = "Not documented"
    else:
        completeness = "Incomplete"
        reason_incomplete = extract_incomplete_reason(note_clean, difficulty_reason, prep_quality) or f"Manual Curation Needed"

    manual_curation = "No"

    return {
        "CMBSMain": CMBSMain,
        "procedure_completeness": completeness,
        "reason_incomplete": reason_incomplete,
        "intubation_distance": intubation,
        "bowel_prep_given": prep_given,
        "bowel_prep_quality": prep_quality,
        "bbps_right": bbps_r,
        "bbps_transverse": bbps_t,
        "bbps_left": bbps_l,
        "total_bbps_score": total_bbps,
        "withdrawal_time": withdrawal_time,
        "manual_curation": manual_curation,
        "procedure_room": procedure_room,
        "report_date": report_date,
        "procedure_provider": procedure_provider,
        "instrument_name": instrument_name,
        "medication": medication,
        "tolerated_procedure": tolerated,
        "difficulty": difficulty,
        "difficulty_reason": difficulty_reason,
        "complications": complications,
        "other_findings": extract_other_findings(note_clean),
    }
