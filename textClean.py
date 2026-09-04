from spacy_engine import fuzz, process
import re
from difflib import SequenceMatcher

# ─────────────────────────────────────────────────────────────────
# NORMALISATION ENGINE
# ─────────────────────────────────────────────────────────────────
def normalise(raw: str, vocab: list, threshold: int = 60) -> tuple[str, float]:
    """Fuzzy match raw string to closest vocab item. Returns (match, score)."""
    if not raw or raw.strip() in ("", "Not documented", "N/A"):
        return "Not documented", 0.0
    if process and fuzz:
        result = process.extractOne(raw, vocab, scorer=fuzz.token_sort_ratio)
        if result and result[1] >= threshold:
            return result[0], result[1] / 100.0
    else:
        raw_tokens = " ".join(sorted(raw.lower().split()))
        best_item, best_score = None, 0.0
        for item in vocab:
            item_tokens = " ".join(sorted(item.lower().split()))
            score = SequenceMatcher(None, raw_tokens, item_tokens).ratio()
            if score > best_score:
                best_item, best_score = item, score
        if best_item and best_score * 100 >= threshold:
            return best_item, best_score
    return f"[UNMATCHED: {raw}]", 0.3


def clean_clinical_text(text: str) -> str:
    """Normalise common report artefacts and spacing before NLP matching."""
    if text is None:
        return ""
    text = str(text)
    text = re.sub(r'_?x000D_|_?x000A_', ' ', text, flags=re.I)
    text = re.sub(r'\r|\n|\t', ' ', text)
    text = re.sub(r'[\u2010-\u2015]', '-', text)  # normalise dash variants
    text = re.sub(r'[\u2018\u2019\u02BC]', "'", text)  # normalise curly/smart apostrophes
    text = re.sub(r'[\u201C\u201D]', '"', text)  # normalise curly quotation marks
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def is_missing_report_text(text) -> bool:
    """True when the source workbook has no usable endoscopy report text."""
    if text is None:
        return True
    value = str(text).strip()
    return value == "" or value.lower() in {"nan", "none", "null", "no report"}

def is_upper_endoscopy_type(procedure_type: str) -> bool:
    """True for rows that are upper-GI procedures rather than colorectal exams."""
    text = str(procedure_type or "")
    return bool(re.search(r'\b(?:upper\s+endoscopy|upper\s+gi|gastroscopy|egd|oesophago|esophago)\b', text, re.I)) and not bool(
        re.search(r'\b(?:colonoscopy|colon|sigmoid|sigmoidoscopy)\b', text, re.I)
    )


def isolate_relevant_endoscopy_text(note: str, procedure_type: str = "") -> str:
    """Return only the section of a combined report matching this procedure's
    type (colorectal or upper GI), so NLP extraction never sees the other
    procedure's text.

    Supervisor feedback 26.06.26: some source reports contain both Upper GI
    Endoscopy and Colonoscopy/Flexible Sigmoidoscopy sections. We preserve
    the original report in Excel, but all NLP should run only on the
    section matching this row's own procedure type -- previously, upper-GI
    rows received the entire unfiltered combined report, letting fields
    like Difficulty be silently populated from the colonoscopy section.

    The report is split into chunks at each detected section heading (e.g.
    "----- Upper GI Endoscopy Report -----"). Any text before the first
    heading has no heading of its own, so its kind is inferred from its own
    "Procedure: ..." line instead. Chunks matching the target procedure
    type are kept; everything else is dropped.
    """
    if is_missing_report_text(note):
        return ""

    text = str(note)
    text = re.sub(r'_?x000D_|_?x000A_|\r', '\n', text, flags=re.I)

    heading_re = re.compile(
        r'(?im)^\s*(?:[-=]+\s*)?(?P<title>'
        r'(?:Upper\s+GI\s+Endoscopy|Gastroscopy|Oesophago.*?duodenoscopy|Esophago.*?duodenoscopy|EGD|'
        r'Colonoscopy|Flexible\s+Sigmoid(?:o)?scopy|Flexible\s+Colonoscopy)'
        r'(?:\s+Report|\s+Procedure\s+Report)?)\s*:?\s*(?:[-=]+)?\s*$'
    )
    headings = [(m.start(), m.end(), m.group("title")) for m in heading_re.finditer(text)]

    if not headings:
        # Nothing to isolate against -- return the whole note rather than
        # guessing which parts belong to which procedure type.
        return str(note)

    target_kind = "upper" if is_upper_endoscopy_type(procedure_type) else "colorectal"

    def chunk_kind(chunk_text: str, heading_title: str | None) -> str:
        if heading_title is not None:
            return "colorectal" if re.search(r'\b(?:colon|sigmoid)\b', heading_title, re.I) else "upper"
        if re.search(r'\bProcedure\s*:?\s*(?:Colonoscopy|Flexible\s+Sigmoid(?:o)?scopy|Flexible\s+Colonoscopy)\b', chunk_text, re.I):
            return "colorectal"
        if re.search(r'\bProcedure\s*:?\s*(?:Upper\s+GI\s+Endoscopy|Gastroscopy|Oesophago|Esophago|EGD)\b', chunk_text, re.I):
            return "upper"
        return "unknown"

    chunks = []
    first_start = headings[0][0]
    prefix_text = text[:first_start].strip()
    if prefix_text:
        chunks.append((chunk_kind(prefix_text, None), prefix_text))
    for idx, (start, end, title) in enumerate(headings):
        section_end = headings[idx + 1][0] if idx + 1 < len(headings) else len(text)
        section_text = text[start:section_end].strip()
        chunks.append((chunk_kind(section_text, title), section_text))

    matched = [chunk for kind, chunk in chunks if kind == target_kind]
    if matched:
        return "\n".join(matched).strip()

    # Nothing confidently matched the target kind. If the whole report is a
    # single unheaded/unclassified chunk, return it as-is rather than
    # dropping everything -- this preserves the original "no headings"
    # fallback behaviour for single-section reports with unusual headers.
    if len(chunks) == 1:
        return chunks[0][1]

    return str(note)

def fuzzy_phrase_present(text: str, phrase: str, threshold: int = 90) -> bool:
    """Return True if a phrase is approximately present in the text."""
    if not text or not phrase:
        return False
    if fuzz:
        return fuzz.partial_ratio(phrase.lower(), text.lower()) >= threshold
    phrase_l = phrase.lower()
    text_l = text.lower()
    if phrase_l in text_l:
        return True
    window = max(len(phrase_l), 12)
    best = 0.0
    for i in range(0, max(len(text_l) - window + 1, 1), max(1, window // 4)):
        best = max(best, SequenceMatcher(None, phrase_l, text_l[i:i + window]).ratio())
    return best * 100 >= threshold

def _match_text(value) -> str:
    """Normalise a finding attribute for source-to-extraction matching."""
    if value is None:
        return ""
    value = str(value).strip()
    if not value or value.lower() in {"nan", "none", "not documented", "not reported"}:
        return ""
    return re.sub(r'\s+', ' ', value).lower()

