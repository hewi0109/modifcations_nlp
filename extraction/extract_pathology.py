from dataclasses import dataclass
from textClean import clean_clinical_text, normalise, fuzzy_phrase_present
import re
from patterns.diagnosis import COLITIS_FAMILY_PATTERNS, DIAG_PATTERNS, DYSPLASIA_PATTERNS, is_bare_normal_statement
from spacy_engine import SPACY_ENGINE
from vocab import DIAGNOSIS_GROUP_VOCAB, DYSPLASIA_VOCAB, FINDING_SITE_VOCAB, UPPER_GI_SITES
from patterns.sites import extract_site_raw, SITE_RAW_PATTERNS, dedupe_sites_preferring_specific
from extraction.extract_findings import NEGATION_RE


# ─────────────────────────────────────────────────────────────────
# STAGE 3: PATHOLOGY EXTRACTOR
# Extracts per-specimen: site, diagnosis_group, dysplasia, fragment_count
# ─────────────────────────────────────────────────────────────────
@dataclass
class PathologySpecimen:
    site_raw: str
    site_norm: str
    diagnoses: list[tuple[str, str, int]]  # (diagnosis_group, dysplasia, fragment_count)
    fragment_count_total: int               # total fragments received for this site
    fragment_count_certain: bool            # False if "multiple" / uncountable
    raw_text: str
    specimen_type: str = "Polyp"            # "Polyp" or "Biopsy"


def is_biopsy_specimen_text(text: str) -> bool:
    """True when pathology labels the specimen as biopsy/Bx without a polyp label."""
    t = clean_clinical_text(text)
    has_biopsy = bool(re.search(r'\b(?:biopsy|biopsies|bx)\b', t, re.I))
    has_polyp_label = bool(re.search(r'\bpolyps?\b|\bpolypectomy\b|\bEMR\b', t, re.I))
    return has_biopsy and not has_polyp_label




def colitis_family_diagnosis(text: str) -> str:
    """Match colitis-family diagnoses with sentence-level negation awareness.

    Bug fix: these patterns previously lived in DIAG_PATTERNS and were matched
    with a blind pat.search(), which doesn't check for negation. Text like
    "no features to suggest underlying Crohn's disease" matched just as
    readily as an actual positive mention. This checks each candidate match's
    containing sentence against the existing NEGATION_RE (already used
    elsewhere in the file for endoscopy findings) and only accepts the match
    if that sentence isn't negated.
    """
    sentences = re.split(r'(?<=[.!?])\s+', text)
    for pat, label in COLITIS_FAMILY_PATTERNS:
        for sentence in sentences:
            if pat.search(sentence) and not NEGATION_RE.search(sentence):
                return label
    return ""


def extract_diagnosis(text: str) -> str:
    t = clean_clinical_text(text)

    colitis_label = colitis_family_diagnosis(t)
    if colitis_label:
        return colitis_label

    if is_bare_normal_statement(t):
        return "Normal tissue"

    # First use exact/regex rules. These remain the safest when the phrase is clear.
    for pat, label in DIAG_PATTERNS:
        if pat.search(t):
            return label

    for ent in SPACY_ENGINE.entities(t, "DIAGNOSIS"):
        diag_norm, score = normalise(ent, DIAGNOSIS_GROUP_VOCAB, threshold=65)
        if score >= 0.65 and not diag_norm.startswith("[UNMATCHED"):
            return diag_norm

    # Fuzzy fallback for common pathology diagnoses and spelling variation.
    # Ordered from more specific to less specific so broad terms do not win too early.
    fuzzy_map = [
        ("tubulovillous adenoma", "Tubulovillous adenoma", 88),
        ("tubular and villous adenoma", "Tubulovillous adenoma", 88),
        ("villous adenoma", "Villous adenoma", 90),
        ("tubular adenoma", "Tubular adenoma", 88),
        ("sessile serrated lesion", "Sessile Serrated Polyp", 88),
        ("sessile serrated adenoma", "Sessile Serrated Polyp", 88),
        ("sessile serrated polyp", "Sessile Serrated Polyp", 88),
        ("traditional serrated adenoma", "Traditional Serrated Adenoma", 88),
        ("hyperplastic polyp", "Hyperplastic polyp", 88),
        ("inflammatory polyp", "Inflammatory polyp", 88),
        ("juvenile polyp", "Juvenile polyp", 88),
        ("pseudo polyp", "Pseudo-polyp", 88),
        ("pseudopolyp", "Pseudo-polyp", 88),
        ("adenocarcinoma", "Adenocarcinoma", 88),
        ("squamous cell carcinoma", "Squamous cell carcinoma", 88),
        ("granulation tissue", "Granulation tissue", 88),
        ("neuroendocrine", "Neuroendocrine tumour", 88),
        ("leiomyoma", "Leiomyoma", 88),
        ("lipoma", "Lipoma", 88),
        ("normal mucosa", "Normal tissue", 90),
        ("unremarkable mucosa", "Normal tissue", 88),
        ("normal tissue", "Normal tissue", 90),
    ]

    for phrase, label, threshold in fuzzy_map:
        if fuzzy_phrase_present(t, phrase, threshold):
            return label

    return "NLP unable to classify pathology clearly"

def extract_dysplasia(text: str) -> str:
    t = clean_clinical_text(text)

    # First use exact/regex rules.
    for pat, label in DYSPLASIA_PATTERNS:
        if pat.search(t):
            return label

    for ent in SPACY_ENGINE.entities(t, "DYSPLASIA"):
        dys_norm, score = normalise(ent, DYSPLASIA_VOCAB, threshold=65)
        if score >= 0.65 and not dys_norm.startswith("[UNMATCHED"):
            return dys_norm

    # Fuzzy fallback for dysplasia phrases and common spelling/formatting variation.
    fuzzy_map = [
        ("intramucosal carcinoma", "Intramucosal carcinoma", 88),
        ("adenocarcinoma high grade", "Adenocarcinoma  high grade", 88),
        ("adenocarcinoma low grade", "Adenocarcinoma  low grade", 88),
        ("focal high grade dysplasia", "Focal high-grade dysplasia", 88),
        ("high grade dysplasia", "High Grade dysplasia", 88),
        ("low grade dysplasia", "Low Grade dysplasia", 88),
        ("no dysplasia", "No dysplasia", 90),
        ("without dysplasia", "No dysplasia", 90),
        ("unable to assess", "Unable to assess", 88),
    ]

    for phrase, label, threshold in fuzzy_map:
        if fuzzy_phrase_present(t, phrase, threshold):
            return label

    return "Not documented"


def extract_diagnosis_groups(text: str) -> list[str]:
    """Return all distinct diagnosis groups explicitly present in a specimen."""
    t = clean_clinical_text(text)
    labels = []
    colitis_label = colitis_family_diagnosis(t)
    if colitis_label:
        labels.append(colitis_label)
    if is_bare_normal_statement(t):
        labels.append("Normal tissue")
    for pat, label in DIAG_PATTERNS:
        if pat.search(t) and label not in labels:
            labels.append(label)
    if not labels:
        single = extract_diagnosis(t)
        if single:
            labels.append(single)
    return labels

def extract_microscopic_specimens(path_note: str) -> list[PathologySpecimen]:
    """Parse numbered microscopic pathology lines when they are clearer.

    Supervisor feedback 01.06.26: some reports have grouped, unordered diagnosis
    lines (e.g. "1 & 2..." and "3 & 4...") but a clean microscopic section with
    each specimen described line-by-line. In those cases, use microscopic rows as
    the verified specimen source and do not also count the diagnosis rows.

    Bug fix: reports can contain more than one "Microscopic" header — a
    grossing/specimen list (measurements, cassette codes, no diagnosis) followed
    by the real diagnostic narrative, sometimes followed by an addendum/re-verified
    continuation. Parsing only the first header pulled in the grossing list as if
    it were diagnostic text and produced duplicate/garbage specimens. Every
    "Microscopic"-headed block is now parsed, keyed by specimen number, with later
    blocks overwriting earlier ones for the same number — narrative text always
    follows the grossing list and addenda always follow the narrative, in these
    reports, so last-write-wins resolves to the correct diagnostic text per specimen.
    """
    path_note = clean_clinical_text(path_note)

    header_matches = list(re.finditer(r'Microscopic[:\s]*', path_note, re.I))
    if not header_matches:
        return []

    stop_pat = re.compile(r'\b(?:Comment|Macroscopic|Supplementary|Addendum|Pathologist|Verified)\b', re.I)

    items_by_number: dict[int, str] = {}
    type_text_by_number: dict[int, str] = {}
    for idx, hm in enumerate(header_matches):
        start = hm.end()
        next_header_start = header_matches[idx + 1].start() if idx + 1 < len(header_matches) else len(path_note)
        block = path_note[start:next_header_start]
        sm = stop_pat.search(block)
        if sm:
            block = block[:sm.start()]
        block = block.strip()
        if not block:
            continue

        block = re.sub(r'(?<!\n)\s*(\d+)\.\s+(?=["\'A-Z])', r'\n\1. ', block)
        num_matches = list(re.finditer(r'(?:^|\n)\s*(\d+)\.\s*', block))
        for n_idx, nm in enumerate(num_matches):
            number = int(nm.group(1))
            s = nm.end()
            e = num_matches[n_idx + 1].start() if n_idx + 1 < len(num_matches) else len(block)
            text = block[s:e].strip()
            if len(text) > 25:
                items_by_number[number] = text  # later block wins over earlier
                # Track every block's text for this number too — the grossing
                # block usually carries the "bx"/"polyp" wording that decides
                # specimen_type, and that wording is often absent from the
                # narrative block that wins for diagnosis/site purposes.
                type_text_by_number[number] = type_text_by_number.get(number, "") + " " + text

    items = [items_by_number[k] for k in sorted(items_by_number)]

    specimens = []
    for number in sorted(items_by_number):
        item = items_by_number[number]
        type_text = type_text_by_number[number]
        site_raw = extract_site_raw(item) or "Unknown"
        if site_raw == "Unknown":
            continue
        if site_raw == "Ileum":
            site_raw = "Terminal Ileum"
        site_norm, _ = normalise(site_raw, FINDING_SITE_VOCAB, threshold=65)
        if site_norm in UPPER_GI_SITES or site_raw in UPPER_GI_SITES:
            continue

        diag_groups = extract_diagnosis_groups(item)
        dys = extract_dysplasia(item)
        diagnoses = [(diag, dys, 1) for diag in diag_groups if diag]
        if not diagnoses:
            continue

        specimens.append(PathologySpecimen(
            site_raw=site_raw,
            site_norm=site_norm,
            diagnoses=diagnoses,
            fragment_count_total=1,
            fragment_count_certain=True,
            raw_text=item,
            specimen_type="Biopsy" if is_biopsy_specimen_text(type_text) else "Polyp",
        ))

    return specimens


def extract_microscopic_items_by_number(path_note: str) -> dict[int, str]:
    """Return numbered microscopic paragraphs keyed by specimen number."""
    # Supervisor feedback 03.07.26: dysplasia may be absent from Diagnosis but
    # present in the corresponding numbered Microscopic paragraph.
    path_note = clean_clinical_text(path_note)
    m = re.search(r'Microscopic[:\s]*(.*?)(?:Pathologist|Comment|Macroscopic|Supplementary|Addendum|Verified|$)', path_note, re.I | re.S)
    if not m:
        return {}
    micro_text = m.group(1).strip()
    matches = list(re.finditer(r'(?:^|\s)(\d+)\.\s+', micro_text))
    items = {}
    for idx, match in enumerate(matches):
        number = int(match.group(1))
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(micro_text)
        item = micro_text[start:end].strip()
        if item:
            items[number] = item
    return items


def should_use_microscopic_specimens(path_note: str, diag_items: list[str], micro_specs: list[PathologySpecimen]) -> bool:
    """Use microscopic rows only when they clearly resolve grouped diagnosis rows."""
    if len(micro_specs) < 2:
        return False
    path_note = clean_clinical_text(path_note)
    diag_match = re.search(r'Diagnosis[:\s]*(.*?)(?:Macroscopic|Microscopic|Comment|$)', path_note, re.I | re.S)
    diag_text = diag_match.group(1) if diag_match else path_note
    grouped_diagnosis = bool(re.search(r'\b\d+\s*&\s*\d+\b|\b\d+\s*(?:and|&)\s*\d+\.', diag_text, re.I))
    fewer_diag_items_than_micro = len(diag_items) < len(micro_specs)
    multi_site_diag_line = any(
        len(dedupe_sites_preferring_specific([label for pat, label in SITE_RAW_PATTERNS if pat.search(item)])) >= 2
        for item in diag_items
    )
    return grouped_diagnosis or fewer_diag_items_than_micro or multi_site_diag_line


def _extract_diagnosis_text(path_note: str) -> str:
    """Extract the Diagnosis section, preferring the LAST occurrence when a
    report has multiple (e.g. an interim Diagnosis followed by a
    Supplementary Report's own revised Diagnosis section). Mirrors the
    same last-write-wins principle already used for multiple Microscopic
    headers -- supplementary/addendum sections always come after the
    original in these reports."""
    matches = list(re.finditer(r'Diagnosis[:\s]*(.*?)(?:Macroscopic|Microscopic|Comment|$)', path_note, re.I | re.S))
    if not matches:
        return path_note
    return matches[-1].group(1).strip()

def extract_pathology_specimens(path_note: str) -> list[PathologySpecimen]:
    path_note = clean_clinical_text(path_note)
    # Focus on Diagnosis section
    diag_text = _extract_diagnosis_text(path_note)

    # Handle both newline-separated AND inline numbered lists
    # e.g. "Diagnosis 1. Caecal polyp... 2. Sigmoid polyp..."
    # First normalise: insert newline before each numbered item
    diag_text = re.sub(r'(?<!\n)\s*(\d+)\.\s+(?=["\'A-Z])', r'\n\1. ', diag_text)

    # Split on numbered items
    items = re.split(r'\n\s*\d+\.\s*', diag_text)
    items = [i.strip() for i in items if len(i.strip()) > 10]
    # Filter divider lines and non-clinical content
    items = [i for i in items if not re.match(r'^[-=\s]+$', i) and
             re.search(r'[a-zA-Z]{4,}', i)]

    micro_specs = extract_microscopic_specimens(path_note)
    if should_use_microscopic_specimens(path_note, items, micro_specs):
        return micro_specs
    micro_items_by_number = extract_microscopic_items_by_number(path_note)

    specimens = []
    for item_number, item in enumerate(items, start=1):
        site_raw = extract_site_raw(item) or "Unknown"
        specimen_type = "Biopsy" if is_biopsy_specimen_text(item) else "Polyp"

        # Remap "Ileum" → "Terminal Ileum" in colonoscopy context
        if site_raw == "Ileum":
            site_raw = "Terminal Ileum"

        site_norm, _ = normalise(site_raw, FINDING_SITE_VOCAB, threshold=65)

        # Skip upper GI specimens — not relevant for colonoscopy findings
        if site_norm in UPPER_GI_SITES or site_raw in UPPER_GI_SITES:
            continue

        # Look for mixed diagnoses in same specimen
        multi = re.findall(
            r'(\d+)\s+fragment[s]?\s+showing\s+features\s+of\s+(.+?)(?=\s+and\s+\d+\s+fragment|\.|$)',
            item, re.I
        )
        if multi:
            diagnoses = []
            for frag_count, frag_desc in multi:
                diag = extract_diagnosis(frag_desc)
                dys  = extract_dysplasia(frag_desc + " " + item)
                diagnoses.append((diag, dys, int(frag_count)))
        elif re.search(r'\b\d+\s+(?:tubular\s+)?adenomas?\b.*\b\d+\s+fragment', item, re.I):
            # Supervisor feedback 26.06.26: keep mixed same-site pathology
            # separate, e.g. "2 tubular adenomas with LGD and 1 fragment with
            # no diagnostic abnormality" should not become one merged result.
            diagnoses = []
            m_ad = re.search(r'\b(\d+)\s+((?:tubular\s+)?adenomas?.*?)(?=\s+and\s+\d+\s+fragment|$)', item, re.I)
            if m_ad:
                diagnoses.append((extract_diagnosis(m_ad.group(2)), extract_dysplasia(m_ad.group(2) + " " + item), int(m_ad.group(1))))
            m_norm = re.search(r'\band\s+(\d+)\s+fragment[s]?\s+with\s+(no\s+diagnostic\s+abnormality|normal\s+colonic\s+mucosa|no\s+significant\s+histological\s+abnormality)', item, re.I)
            if m_norm:
                diagnoses.append(("Normal tissue", "N/A", int(m_norm.group(1))))
            if not diagnoses:
                diag_groups = extract_diagnosis_groups(item)
                dys = extract_dysplasia(item)
                diagnoses = [(diag, dys, 1) for diag in diag_groups]
        else:
            diag_groups = extract_diagnosis_groups(item)
            dys  = extract_dysplasia(item)

            # If no dysplasia found in diagnosis line, search the full microscopic section
            if dys == "Not documented":
                numbered_micro = micro_items_by_number.get(item_number, "")
                if numbered_micro:
                    dys = extract_dysplasia(numbered_micro)

                micro_section = re.search(
                    r'Microscopic[:\s]*(.*?)(?:Pathologist|Comment|Macroscopic|Supplementary|Addendum|Verified|$)',
                    path_note, re.I | re.S
                )
                if dys == "Not documented" and micro_section:
                    micro_text = re.sub(r'\s+', ' ', micro_section.group(1))
                    # Try site-specific subsection first
                    site_key = site_raw[:6] if site_raw != "Unknown" else ""
                    if site_key:
                        site_micro = re.search(
                            rf'{re.escape(site_key)}.{{0,300}}',
                            micro_text, re.I
                        )
                        if site_micro:
                            dys = extract_dysplasia(site_micro.group(0))
                    # Fallback: full microscopic text (only if single specimen)
                    if dys == "Not documented" and len(items) == 1:
                        dys = extract_dysplasia(micro_text[:600])

            if len(diag_groups) > 1:
                diagnoses = [(diag, dys, 1) for diag in diag_groups]
            else:
                diagnoses = [(diag_groups[0], dys, 1)]

        # ── Fragment count from macroscopic ─────────────────────────
        frag_count_total = 0
        frag_count_certain = True

        # "polyp x5" or "polyps x5" in diagnosis line
        m_x = re.search(r'x\s*(\d+)', item, re.I)
        if m_x:
            frag_count_total = int(m_x.group(1))

        # "x M AE" = multiple fragments → uncertain
        if re.search(r'x\s*M\s*AE', item, re.I):
            frag_count_certain = False
            frag_count_total = frag_count_total or 99

        # "Multiple ... fragments" → uncertain
        if re.search(r'\bmultiple\s+(?:tan\s+|pale\s+)?(?:polypoid\s+)?(?:tissue\s+)?fragment', item, re.I):
            frag_count_certain = False
            frag_count_total = frag_count_total or 99

        # Explicit fragment count: "Nine ... fragments", "Seven ... fragments", "1A x 9 AE"
        if frag_count_total == 0:
            # "1A x 9 AE" style
            m_ae = re.search(r'\d+[A-Z]\s+x\s+(\d+)\s+AE', item)
            if m_ae:
                frag_count_total = int(m_ae.group(1))

        if frag_count_total == 0:
            # Word numbers before "fragments"
            num_words = {"one":1,"two":2,"three":3,"four":4,"five":5,
                         "six":6,"seven":7,"eight":8,"nine":9,"ten":10}
            for word, val in num_words.items():
                if re.search(rf'\b{word}\b.*fragment', item, re.I):
                    frag_count_total = val
                    break

        if frag_count_total == 0:
            frag_mentions = re.findall(r'(\d+)\s+fragment', item, re.I)
            if frag_mentions:
                frag_count_total = sum(int(f) for f in frag_mentions)

        # Fallback: sum of diagnosis fragment counts
        if frag_count_total == 0:
            frag_count_total = sum(fc for _, _, fc in diagnoses)

        specimens.append(PathologySpecimen(
            site_raw=site_raw,
            site_norm=site_norm,
            diagnoses=diagnoses,
            fragment_count_total=frag_count_total,
            fragment_count_certain=frag_count_certain,
            raw_text=item,
            specimen_type=specimen_type,
        ))

    return specimens

def wait_for_supplementary(raw_text: str) -> str | None:
    interim = False
    resolved = False

    if re.search(r'INTERIM REPORT', raw_text, re.I):
        interim = True

    if re.search(r'Supplementary Report', raw_text, re.I):
        resolved = True

    if interim and not resolved:
        return "Only interim pathology present"
    else:
        return None