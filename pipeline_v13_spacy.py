"""
Clinical NLP Pipeline v12
========================
- Endoscopy extractor (procedure + findings)
- Pathology extractor
- Merge engine (endoscopy drives row count, worst-diagnosis-wins)
- Full dropdown normalisation
- Two linked Excel sheets: Procedure + Findings (with pathology merged)
"""

import re
import uuid
import argparse
from difflib import SequenceMatcher
from dataclasses import dataclass, field
from typing import Optional
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from datetime import date

try:
    from rapidfuzz import fuzz, process
except ImportError:
    fuzz = None
    process = None

try:
    import spacy
    from spacy.matcher import Matcher, PhraseMatcher
    from spacy.pipeline import EntityRuler
except ImportError:
    spacy = None
    Matcher = PhraseMatcher = EntityRuler = None

# ─────────────────────────────────────────────────────────────────
# TEST NOTES
# ─────────────────────────────────────────────────────────────────
ENDOSCOPY_NOTE = """
Procedure: Pre-Anaesthesia Assessment: The risks and benefits of the procedure and the sedation 
options and risks were discussed with the patient. All questions were answered and informed consent 
was obtained. Patient identification and proposed procedure were verified prior to the procedure by 
the physician and the nurse. The procedure was verified in the pre-procedure area in the procedure room.
Anaesthetic Class: Anaesthetic class 2.
After obtaining informed consent, the was introduced through the anus and advanced to the caecum, 
identified by appendiceal orifice and ileocaecal valve. The colonoscopy was performed with moderate 
difficulty due to restricted mobility of the colon. The patient tolerated the procedure well. 
The quality of the bowel preparation was evaluated using the BBPS (Boston Bowel Preparation Scale) 
with scores of: Right Colon = 2, Transverse Colon = 2 and Left Colon = 2. The total BBPS score equals 6.
The bowel preparation used was 4l colonlytely. The ileocaecal valve, the appendiceal orifice and 
the rectum were photographed. Scope withdrawal time was measured from caecum to rectum.

Findings:
- A 7 mm polyp was found in the lower rectum. The polyp was Paris classification Is (protruding, sessile). 
The polyp was removed with a cold snare. Resection and retrieval were complete. To prevent bleeding 
post-intervention, one haemostatic clip was successfully placed. Bleeding had stopped at the end of the procedure.
- Two Paris classification Is (protruding, sessile) polyps were found in the sigmoid colon. 
The polyps were 4 to 5 mm in size. These polyps were removed with a cold snare. Resection and retrieval were complete.
- Five Paris classification Is (protruding, sessile) polyps were found in the transverse colon. 
The polyps were 4 to 6 mm in size. These polyps were removed with a cold snare. Resection and retrieval were complete.
- Eight Paris classification Is (protruding, sessile) polyps were found in the ascending colon. 
The polyps were 4 to 8 mm in size. These polyps were removed with a cold snare. Resection and retrieval were complete.
- A diffuse area of severe melanosis was found in the entire colon.
- The retroflexed view of the distal rectum and anal verge was normal and showed no anal or rectal abnormalities.

Scope Withdrawal Time: 20 Minutes
"""

PATHOLOGY_NOTE = """
Diagnosis:
1. Ascending colon polyps, biopsy: tubular adenomas with low-grade dysplasia.
2. Transverse colon polyp x5, biopsy: tubular adenomas with low-grade dysplasia.
3. Sigmoid polyp, biopsy: tubular adenoma with low-grade dysplasia.
4. Rectal polyp, biopsy: 1 fragment showing features of tubular adenoma with low-grade dysplasia 
and 2 fragments showing features of hyperplastic polyps.

Microscopic:
1. The specimen has been examined at 3 levels and consists of 5 fragments of large bowel type mucosa, 
all of which show focal low grade dysplasia in keeping with tubular adenomas. No high-grade dysplasia 
or invasive lesion is seen.
2. Sections consist of multiple fragments of large bowel type mucosa, 4 of which show low grade dysplasia 
in keeping with origin from tubular adenoma. No high-grade dysplasia or invasive lesion is seen.
3. Sections consist of a tubular adenoma showing low grade dysplasia. No high-grade dysplasia or 
invasive lesion is seen. A well defined stalk of normal mucosa is included.
4. Sections consist of fragments of large bowel type mucosa showing features of melanosis coli. 
1 fragment shows features of a tubular adenoma with low-grade dysplasia. The remaining 2 fragments 
show features of hyperplastic polyps. No high-grade dysplasia or invasive lesion is seen.
"""

# ─────────────────────────────────────────────────────────────────
# DROPDOWN VOCABULARIES
# ─────────────────────────────────────────────────────────────────
INTUBATION_DISTANCE_VOCAB = [
    "Anastomosis", "Ascending Colon", "Caecum", "Descending Colon",
    "Hepatic Flexure", "Not Reported", "Rectosigmoid Junction", "Rectum", "Sigmoid Colon",
    "Splenic Flexure", "Terminal Ileum", "Transverse Colon", "Surgical Stoma"
]

BOWEL_PREP_QUALITY_VOCAB = [
    "Adequate", "Excellent", "Fair", "Good", "Inadequate",
    "Not reported", "Poor", "Unsatisfactory", "Barely satisfactory",
    "Barely satistfactory", "Very good", "Mildly poor"
]

POOR_PREP_QUALITY_VALUES = [
    "poor", "inadequate", "unsatisfactory", "barely satisfactory",
    "mildly poor",  "Barely satistfactory"
]

BOWEL_PREP_GIVEN_VOCAB = [
    "3L Colonlytely", "4L Colonlytely", "4L Colonlytely + Aperients",
    "5L Colonlytely", "5L Colonlytely + Aperients", "Moviprep",
    "Picoprep x2, Colonlytely x1", "Picoprep x2, Colonlytely x2",
    "Picoprep x3", "Plenvu", "Fleet enema x2", "Fleet enema x1", "Nil"
]

BBPS_VOCAB = ["N/A", "0", "1", "2", "3", "Not reported"]

FINDING_SITE_VOCAB = [
    "Anal canal", "Anastomosis", "Appendiceal orifice", "Ascending colon",
    "Distal ascending colon", "Mid ascending colon", "Proximal ascending colon",
    "Caecum", "Colon - multiple polyps", "Descending colon",
    "Distal descending colon", "Mid descending colon", "Proximal descending colon",
    "Duodenum", "Duodenal",
    "Gastric", "Hepatic flexure of colon", "Ileum", "Ileocaecal valve",
    "Ileocecal valve", "Ileo-caecal valve", "ICV",
    "Jejunum", "Left colon", "Oesophagus", "Pouch", "Random colon biopsies",
    "Rectosigmoid junction", "Rectum", "Upper rectum", "Mid rectum", "Distal rectum",
    "Right colon", "Sigmoid colon",
    "Distal sigmoid colon", "Mid sigmoid colon", "Proximal sigmoid colon",
    "Small bowel biopsy", "Splenic flexure of colon", "Terminal Ileum",
    "Transverse colon", "Distal transverse colon", "Mid transverse colon",
    "Proximal transverse colon", "Unknown", "Other"
]

FINDING_MORPHOLOGY_VOCAB = [
    "sessile", "flat", "pedunculated", "semi-pedunculated", "semi-sessile",
    "sessile serrated", "multi-lobulated", "laterally spreading", "multiple (sessile)", "mass",
    "type Ip", "type Is", "type IIa", "type IIb", "type IIc", "type III",
    "type IIc+IIa", "type IIa+IIc", "type IIc+Is", "type Is+IIc", "type Ib+IIa",
    "type IIa+Ib", "type Is+IIa", "type IIa+Is", "type IIa+c"
]

INTERVENTION_VOCAB = [
    "Piecemeal snare", "Cold snare", "Hot snare", "EMR - piecemeal",
    "EMR - en bloc", "ESD", "Biopsy only", "Surgical excision", "Not excised"
]

INTERVENTION_COMPLETENESS_VOCAB = [
    "Complete", "Incomplete", "Not clear", "Not reported", "Not retrieved"
]

DIAGNOSIS_GROUP_VOCAB = [
    "Admixed polyp", "Neuroendocrine tumour", "Granulation tissue",
    "Hyperplastic polyp", "Inflammatory polyp", "Juvenile polyp",
    "Leiomyoma", "Lipoma", "Melanosis coli", "Normal tissue", "Possible Sessile Serrated Polyp",
    "Pseudo-polyp", "Sessile Serrated Polyp", "Squamous cell carcinoma",
    "Traditional Serrated Adenoma", "Tubular adenoma", "Tubulovillous adenoma",
    "Ulcer", "NLP unable to classify pathology clearly",
    "Unspecified polyp - polyp not excised",
    "Unspecified polyp - not excised (likely HP)",
    "Unspecified polyp - polyp not retrieved", "Villous adenoma",
    "Condyloma acuminata", "Colitis", "Crohn's colitis", "Quiescent colitis",
    "Ulcerative colitis", "Pouchitis", "Proctitis", "Ileitis",
    "Indeterminate colitis", "Adenocarcinoma"
]

DYSPLASIA_VOCAB = [
    "N/A",
    "No dysplasia", "Low Grade dysplasia", "Focal high-grade dysplasia",
    "Focal High Grade Dysplasia",
    "High Grade dysplasia", "Unable to assess",
    "Mildly active", "Moderately active", "Severely active",
    "Mildly active - with dysplasia", "Moderately active - with dysplasia",
    "Severely active - with dysplasia",
    "Adenocarcinoma  low grade", "Adenocarcinoma  high grade",
    "Intramucosal carcinoma"
]

# Upper GI sites to exclude from pathology (not relevant for colonoscopy findings)
UPPER_GI_SITES = {
    "Oesophagus", "Gastric", "Duodenum", "Small bowel biopsy", "Ileum"
}
# Note: Terminal Ileum is KEPT — relevant for IBD surveillance in colonoscopy
DIAGNOSIS_SEVERITY = {
    "Adenocarcinoma": 10,
    "Squamous cell carcinoma": 10,
    "Intramucosal carcinoma": 9,
    "Tubulovillous adenoma": 8,
    "Villous adenoma": 8,
    "Traditional Serrated Adenoma": 7,
    "Tubular adenoma": 7,
    "Sessile Serrated Polyp": 6,
    "Possible Sessile Serrated Polyp": 5,
    "Admixed polyp": 5,
    "Neuroendocrine tumour": 5,
    "Hyperplastic polyp": 3,
    "Inflammatory polyp": 3,
    "Pseudo-polyp": 2,
    "NLP unable to classify pathology clearly": 2,
    "Normal tissue": 1,
    "Granulation tissue": 1,
}

DYSPLASIA_SEVERITY = {
    "Adenocarcinoma  high grade": 10,
    "Adenocarcinoma  low grade": 9,
    "Intramucosal carcinoma": 9,
    "High Grade dysplasia": 8,
    "Focal high-grade dysplasia": 7,
    "Focal High Grade Dysplasia": 7,
    "Severely active - with dysplasia": 6,
    "Moderately active - with dysplasia": 5,
    "Mildly active - with dysplasia": 4,
    "Low Grade dysplasia": 3,
    "Severely active": 3,
    "Moderately active": 2,
    "Mildly active": 2,
    "No dysplasia": 1,
    "Unable to assess": 0,
}


# ─────────────────────────────────────────────────────────────────
# OPTIONAL SPACY NLP ENGINE
# ─────────────────────────────────────────────────────────────────
class ClinicalSpacyEngine:
    """
    Lightweight spaCy rule pipeline for report language.

    The legacy regex layer remains as the safety net, but this engine adds
    token-aware sentence segmentation and entity matching for sites,
    morphology, interventions, diagnosis groups, and dysplasia phrases.
    It uses a blank English model so it does not require downloading
    en_core_web_sm before the pipeline can run.
    """
    def __init__(self):
        import os
        if spacy is None or os.environ.get("DISABLE_SPACY") == "1":
            self.available = False
            self.nlp = None
            self.matcher = None
            self.phrase_matcher = None
            return
        ## to run with spacy off # Run 1 -- spaCy forced OFF
        ## $env:DISABLE_SPACY="1"
        ## python pipeline_v13_spacy.py --output "data/output_no_spacy.xlsx"

        self.available = True
        self.nlp = spacy.blank("en")
        if "sentencizer" not in self.nlp.pipe_names:
            self.nlp.add_pipe("sentencizer")

        ruler = self.nlp.add_pipe("entity_ruler", config={"overwrite_ents": True})
        ruler.add_patterns(self._entity_patterns())

        self.matcher = Matcher(self.nlp.vocab)
        self._add_token_patterns()

    def _entity_patterns(self):
        patterns = []

        def add(label, values):
            for value in values:
                patterns.append({"label": label, "pattern": value, "id": value})

        add("COLON_SITE", [
            "anal canal", "appendiceal orifice", "ascending colon", "caecum", "cecum",
            "caecal", "cecal", "descending colon", "distal ascending colon",
            "mid ascending colon", "proximal ascending colon", "distal descending colon",
            "mid descending colon", "proximal descending colon", "distal rectum", "hepatic flexure",
            "ileocaecal valve", "ileocecal valve", "left colon", "lower rectum",
            "rectosigmoid junction", "rectum", "rectal", "right colon",
            "sigmoid colon", "distal sigmoid colon", "mid sigmoid colon",
            "proximal sigmoid colon", "sigmoid", "splenic flexure", "terminal ileum",
            "transverse colon", "distal transverse colon", "mid transverse colon",
            "proximal transverse colon", "whole colon", "entire colon"
        ])
        add("MORPHOLOGY", [
            "Paris classification Is", "Paris classification Ip", "Paris classificationIIa",
            "Paris 0-IIa+c", "Paris 0-IIa+Is", "Paris 0-Is+IIa", "Paris 0-IIa+Ib", "Paris 0-Ib+IIa",
            "Paris 0-Is+IIc", "Paris 0-IIc+Is", "type Is", "type Ip", "type IIa", "type IIb", "type IIc",
            "type III", "type IIa+IIc", "type IIc+IIa", "flat", "laterally spreading", "mass",
            "multi-lobulated", "pedunculated", "semi-pedunculated",
            "semi-sessile", "sessile"
        ])
        add("INTERVENTION", [
            "biopsy only", "biopsied", "cold snare", "EMR", "endoscopic mucosal resection",
            "en bloc", "ESD", "hot snare", "not excised", "not removed",
            "piecemeal", "snare", "surgical excision"
        ])
        add("DIAGNOSIS", [
            "adenocarcinoma", "granulation tissue", "hyperplastic polyp",
            "inflammatory polyp", "juvenile polyp", "leiomyoma", "lipoma",
            "neuroendocrine tumour", "neuroendocrine tumor", "pseudo-polyp",
            "pseudopolyp", "sessile serrated adenoma", "sessile serrated lesion",
            "sessile serrated polyp", "traditional serrated adenoma",
            "tubular adenoma", "tubulovillous adenoma", "villous adenoma",
            "normal mucosa", "unremarkable mucosa"
        ])
        add("DYSPLASIA", [
            "adenocarcinoma high grade", "adenocarcinoma low grade",
            "focal high grade dysplasia", "high grade dysplasia",
            "intramucosal carcinoma", "low grade dysplasia", "no dysplasia",
            "unable to assess", "without dysplasia"
        ])
        return patterns

    def _add_token_patterns(self):
        number_words = [
            "one", "two", "three", "four", "five", "six", "seven", "eight",
            "nine", "ten"
        ]
        number = {"LOWER": {"IN": number_words + [str(i) for i in range(1, 51)]}}
        self.matcher.add("POLYP_COUNT", [[
            number,
            {"LOWER": {"REGEX": r"^(polyps?|lesions?|adenomas?)$"}}
        ]])
        self.matcher.add("POLYP_SIZE", [[
            {"LIKE_NUM": True},
            {"LOWER": {"IN": ["mm", "millimetre", "millimetres", "millimeter", "millimeters", "cm"]}}
        ]])

    def doc(self, text: str):
        if not self.available or not text:
            return None
        return self.nlp(clean_clinical_text(text))

    def entities(self, text: str, label: str) -> list[str]:
        doc = self.doc(text)
        if doc is None:
            return []
        return [ent.text for ent in doc.ents if ent.label_ == label]

    def sentences(self, text: str) -> list[str]:
        doc = self.doc(text)
        if doc is None:
            return []
        return [sent.text.strip() for sent in doc.sents if sent.text.strip()]


SPACY_ENGINE = ClinicalSpacyEngine()


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


# ─────────────────────────────────────────────────────────────────
# NUMBER WORD MAP
# ─────────────────────────────────────────────────────────────────
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

def parse_polyp_count(text: str) -> int:
    """Extract polyp count from text. Returns integer."""
    # Count should come from the finding sentence, not later treatment details
    # such as "two haemostatic clips".
    t = re.split(r'\.\s+(?:the\s+polyps?|these\s+polyps?|this\s+polyp|to\s+prevent|there\s+was)\b', text.lower())[0]

    site_count_total = sum(cnt for _, cnt in extract_site_count_pairs(text))
    if site_count_total > 1:
        return site_count_total

    # Supervisor feedback: compact forms like "2 x4mm polyps" and
    # "11 much smaller polyps 2-3mm" are counts, not sizes.
    m = re.search(r'\b(\d+)\s*x\s*\d+(?:\.\d+)?\s*mm\s+polyps?\b', t)
    if m:
        return int(m.group(1))

    # Compound: "one X...and four Y...polyps" — sum number words before Paris/sessile
    # Exclude numbers immediately followed by mm/cm (those are sizes not counts)
    compound_nums = re.findall(
        r'\b(one|two|three|four|five|six|seven|eight|nine|ten|\d+)\b'
        r'(?!\s*(?:mm|cm))'   # not a size
        r'(?:\s+\w+){0,8}\s+(?:paris|classification|sessile|flat|pedunculated)',
        t
    )
    if compound_nums and re.search(r'\bpolyps?\b', t):
        total = 0
        for n in compound_nums:
            total += NUMBER_WORDS.get(n, int(n) if n.isdigit() else 0)
        if total > 1:
            return total

    # Digit before polyps
    m = re.search(r'\b(\d+)\s+(?:other\s+)?(?:much\s+)?(?:smaller\s+|tiny\s+|sessile\s+|flat\s+|pedunculated\s+){0,3}polyps?\b', t)
    if m:
        return int(m.group(1))

    m = re.search(r'\b(\d+)\s*(?:polyps?|lesions?|adenomas?)', t)
    if m:
        return int(m.group(1))

    # Word numbers before polyps
    m = re.search(
        r'\b(one|two|three|four|five|six|seven|eight|nine|ten)\b'
        r'(?:\s+\w+){0,5}\s*(?:polyps?|lesions?|adenomas?|\d+\s*mm)',
        t
    )
    if m:
        return NUMBER_WORDS.get(m.group(1), 1)

    return 1

def number_word_to_int(value: str) -> int:
    value_l = str(value).lower()
    return NUMBER_WORDS.get(value_l, int(value_l) if value_l.isdigit() else 1)


# ─────────────────────────────────────────────────────────────────
# NEGATION & HEDGE
# ─────────────────────────────────────────────────────────────────
NEGATION_RE = re.compile(
    r'\b(no|not|without|normal|negative|absence of|failed to|unable to|'
    r'did not|was not|could not|never|no evidence)\b', re.I
)
HEDGE_UNCERTAIN_RE = re.compile(
    r'\?|\bpossible\b|\bprobable\b|\bsuspected?\b|\bmay be\b|\blikely\b', re.I
)
HEDGE_INTENDED_RE = re.compile(
    r'\bintend(?:ed)? to\b|\battempt(?:ed)? to\b|\bplan(?:ned)? to\b', re.I
)
POLYP_INDICATOR_RE = re.compile(
    r'\bpolyps?\b|\badenomas?\b|\blesions?\b|\bmass\b|\bgrowth\b|\btumou?rs?\b', re.I
)


# ─────────────────────────────────────────────────────────────────
# SITE EXTRACTION (endoscopy text → dropdown)
# ─────────────────────────────────────────────────────────────────
SITE_RAW_PATTERNS = [
    # Sub-sites must be checked before their parent segments.
    (re.compile(r'distal[-\s]+ascending(?:\s+colon)?(?:\s+(?:polyp|biopsy|mass|lesion))?', re.I), "Distal ascending colon"),
    (re.compile(r'mid[-\s]+ascending(?:\s+colon)?(?:\s+(?:polyp|biopsy|mass|lesion))?', re.I), "Mid ascending colon"),
    (re.compile(r'proximal[-\s]+ascending(?:\s+colon)?(?:\s+(?:polyp|biopsy|mass|lesion))?', re.I), "Proximal ascending colon"),
    (re.compile(r'distal[-\s]+descending(?:\s+colon)?(?:\s+(?:polyp|biopsy|mass|lesion))?', re.I), "Distal descending colon"),
    (re.compile(r'mid[-\s]+descending(?:\s+colon)?(?:\s+(?:polyp|biopsy|mass|lesion))?', re.I), "Mid descending colon"),
    (re.compile(r'proximal[-\s]+descending(?:\s+colon)?(?:\s+(?:polyp|biopsy|mass|lesion))?', re.I), "Proximal descending colon"),
    (re.compile(r'distal[-\s]+sigmoid(?:\s+colon)?(?:\s+(?:polyp|biopsy|mass|lesion))?', re.I), "Distal sigmoid colon"),
    (re.compile(r'mid[-\s]+sigmoid(?:\s+colon)?(?:\s+(?:polyp|biopsy|mass|lesion))?', re.I), "Mid sigmoid colon"),
    (re.compile(r'proximal[-\s]+sigmoid(?:\s+colon)?(?:\s+(?:polyp|biopsy|mass|lesion))?', re.I), "Proximal sigmoid colon"),
    (re.compile(r'distal[-\s]+transverse(?:\s+colon)?(?:\s+(?:polyp|biopsy|mass|lesion))?', re.I), "Distal transverse colon"),
    (re.compile(r'mid[-\s]+transverse(?:\s+colon)?(?:\s+(?:polyp|biopsy|mass|lesion))?', re.I), "Mid transverse colon"),
    (re.compile(r'proximal[-\s]+transverse(?:\s+colon)?(?:\s+(?:polyp|biopsy|mass|lesion))?', re.I), "Proximal transverse colon"),
    (re.compile(r'\banastomo(?:sis|tic|sed)?(?:\s+(?:polyp|biopsy|mass|lesion))?', re.I), "Anastomosis"),
    # Rectum / rectal — before rectosigmoid and sigmoid
    # Also handle distance-from-anus: <20cm = rectum, 20-40cm = sigmoid
    (re.compile(r'upper[-\s]+rect(?:um|al)', re.I), "Upper rectum"),
    (re.compile(r'mid[-\s]+rect(?:um|al)', re.I), "Mid rectum"),
    (re.compile(r'lower rectum|distal[-\s]+rect(?:um|al)', re.I), "Distal rectum"),
    (re.compile(r'rectal\s+(?:colon["\']?\s+)?(?:polyp|biopsy|mass)|rectum', re.I), "Rectum"),
    (re.compile(r'\b(?:[1-9]|1[0-9])\s*cm\s+(?:proximal\s+to|from)\s+(?:the\s+)?anus', re.I), "Rectum"),
    (re.compile(r'\b(?:2[0-9]|3[0-9]|40)\s*cm\s+(?:proximal\s+to|from)\s+(?:the\s+)?anus', re.I), "Sigmoid colon"),
    # Rectosigmoid junction — before sigmoid
    (re.compile(r'recto[-\s]?(?:sigmoid|sgmoid)(?:\s+(?:colon|junction|mass|biopsy|polyp))?|rectosigmoid\s*(?:junction|mass|biopsy|polyp)?', re.I), "Rectosigmoid junction"),
    # Sigmoid — including "sig polyp", "sigmoid polyp"
    (re.compile(r'(?<!recto[-\s])sig(?:moid)?\s+(?:colon\s+)?(?:polyp|biopsy|mass)|(?<!recto[-\s])sigmoid\s+colon|(?<!recto[-\s])sigmoid', re.I), "Sigmoid colon"),
    # Descending
    (re.compile(r'descending\s+(?:colon\s+)?(?:polyp|biopsy|mass)|descending\s+colon|descending', re.I), "Descending colon"),
    # Splenic flexure — before transverse
    (re.compile(r'splenic\s+flexure', re.I), "Splenic flexure of colon"),
    # Transverse — including "distal transverse", "Tx 2", "Polyp Tx"
    (re.compile(r'(?:distal\s+|proximal\s+)?transverse\s+(?:colon\s+)?(?:polyp|biopsy|mass|lesion)|polyp\s+tx\s*\d|transverse\s+colon|transverse', re.I), "Transverse colon"),
    # Hepatic flexure — before ascending
    (re.compile(r'hepatic\s+flexure', re.I), "Hepatic flexure of colon"),
    # Ascending — including "asc polyp", "EMR 1 of ascending", "ascending colon and caecum"
    (re.compile(r'EMR\s+\d+\s+(?:of\s+)?ascending|asc(?:ending)?\s+(?:colon\s+)?(?:polyp|biopsy|mass|EMR)|ascending\s+colon\s+(?:and\s+caecum\s+)?(?:tumou?r|mass|biopsy|polyp)|ascending\s+colon|ascending', re.I), "Ascending colon"),
    # Supervisor feedback 26.06.26: finding-site extraction must preserve
    # ileocaecal/ICV wording. Only the Procedure intubation field maps these
    # terms to Caecum.
    (re.compile(r'ileo[-\s]?caecal\s+valve', re.I), "Ileocaecal valve"),
    (re.compile(r'ileo[-\s]?cecal\s+valve', re.I), "Ileocecal valve"),
    (re.compile(r'\bICV\b|\bIC\s+valve\b', re.I), "ICV"),
    # Caecum — including "caecal biopsy/polyp", "cacum" (sic)
    (re.compile(r'cae?c(?:al|um)\s*(?:polyp|biopsy|mass|lesion)?|cacum', re.I), "Caecum"),
    # Terminal ileum
    (re.compile(r'terminal\s+ileum', re.I), "Terminal Ileum"),
    # Small bowel — before ileum
    (re.compile(r'small\s+bowel\s+(?:polyp|biopsy)|small\s+bowel', re.I), "Small bowel biopsy"),
    (re.compile(r'\bileum\b', re.I), "Ileum"),
    # Duodenum
    (re.compile(r'duodenal?\s+(?:polyp|biopsy)|duodenum', re.I), "Duodenum"),
    # Oesophagus — before gastric
    (re.compile(r'oesophag(?:us|eal)|esophag(?:us|eal)', re.I), "Oesophagus"),
    # Gastric
    (re.compile(r'gastric\s+(?:polyp|biopsy)|stomach|gastric', re.I), "Gastric"),
    # Right / left colon
    (re.compile(r'right\s+colon\s+(?:polyp|biopsy)|right\s+colon', re.I), "Right colon"),
    (re.compile(r'left\s+colon\s+(?:polyp|biopsy)|left\s+colon', re.I), "Left colon"),
    # Anal canal — only when it IS the site, not when used as a distance landmark
    (re.compile(r'(?:found\s+in|polyp\s+(?:at|in)|site\s+(?:at|in)|biopsy\s+(?:at|from))\s+(?:the\s+)?(?:anal\s+canal|anus\b)', re.I), "Anal canal"),
    # Appendiceal orifice
    (re.compile(r'appendiceal\s+orifice', re.I), "Appendiceal orifice"),
    # Entire colon
    (re.compile(r'entire\s+colon|whole\s+colon|pan.?colon', re.I), "Colon - multiple polyps"),
    (re.compile(r'(?:anastomosis|anastomotic)\s+polyp', re.I), "Anastomosis")
]

def extract_site_raw(text: str) -> Optional[str]:
    for pat, label in SITE_RAW_PATTERNS:
        if pat.search(text):
            return label
    for ent in SPACY_ENGINE.entities(text, "COLON_SITE"):
        site_norm, score = normalise(ent, FINDING_SITE_VOCAB, threshold=65)
        if score >= 0.65 and not site_norm.startswith("[UNMATCHED"):
            return site_norm
    return None

SITE_PARENT_BY_SPECIFIC = {
    "Distal ascending colon": "Ascending colon",
    "Mid ascending colon": "Ascending colon",
    "Proximal ascending colon": "Ascending colon",
    "Distal descending colon": "Descending colon",
    "Mid descending colon": "Descending colon",
    "Proximal descending colon": "Descending colon",
    "Distal sigmoid colon": "Sigmoid colon",
    "Mid sigmoid colon": "Sigmoid colon",
    "Proximal sigmoid colon": "Sigmoid colon",
    "Distal transverse colon": "Transverse colon",
    "Mid transverse colon": "Transverse colon",
    "Proximal transverse colon": "Transverse colon",
    "Upper rectum": "Rectum",
    "Mid rectum": "Rectum",
    "Distal rectum": "Rectum",
    "Rectosigmoid junction": "Sigmoid colon",
}

def dedupe_sites_preferring_specific(sites: list[str]) -> list[str]:
    """Drop generic parent sites when the same sentence also names the subsite."""
    specific_parents = {SITE_PARENT_BY_SPECIFIC[s] for s in sites if s in SITE_PARENT_BY_SPECIFIC}
    deduped = []
    for site in sites:
        if site in specific_parents:
            continue
        if site not in deduped:
            deduped.append(site)
    return deduped

def normalise_compound_site_text(text: str) -> str:
    """Expand compact report wording before finding splitting.

    Supervisor feedback: phrases such as "mid-transverse and distal sigmoid",
    "proximal and distal descending", and "upper and mid-rectum" need separate
    site-specific rows, not one generic segment row.
    """
    text = re.sub(r'\b(proximal|mid|distal|upper|lower)-(?=\w)', r'\1 ', text, flags=re.I)
    for segment in ("ascending", "descending", "sigmoid", "transverse"):
        text = re.sub(
            rf'\b(proximal|mid|distal)\s+and\s+(proximal|mid|distal)\s+{segment}\b',
            rf'\1 {segment} and \2 {segment}',
            text,
            flags=re.I
        )
    text = re.sub(r'\bupper\s+and\s+mid\s+rect(?:um)?\b', 'upper rectum and mid rectum', text, flags=re.I)
    text = re.sub(r'\bmid\s+and\s+upper\s+rect(?:um)?\b', 'mid rectum and upper rectum', text, flags=re.I)
    return text

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
# MORPHOLOGY EXTRACTION
# ─────────────────────────────────────────────────────────────────
MORPHOLOGY_RAW = [
    ## changes made to each row as the "Paris-0" needs to be accounted for
    (re.compile(r'type\s+IIa\+IIc\b|paris\s+(?:classification\s+)?IIa\+IIc\b|IIa\+IIc', re.I), "type IIa+IIc"),
    (re.compile(r'type\s+IIc\+IIa\b|paris\s+(?:classification\s+)?IIc\+IIa\b|IIc\+IIa', re.I), "type IIc+IIa"),

    (re.compile(r'paris\s+0-IIa\+c\b', re.I), "type IIa+c"),
    (re.compile(r'paris\s+0-IIa\+Is\b', re.I), "type IIa+Is"),
    (re.compile(r'paris\s+0-Is\+IIa\b', re.I), "type Is+IIa"),
    (re.compile(r'paris\s+0-IIa\+Ib\b', re.I), "type IIa+Ib"),
    (re.compile(r'paris\s+0-Ib\+IIa\b', re.I), "type Ib+IIa"),
    (re.compile(r'paris\s+0-Is\+IIc\b', re.I), "type Is+IIc"),
    (re.compile(r'paris\s+0-IIc\+Is\b', re.I), "type IIc+Is"),

    (re.compile(r'paris\s+(?:classification\s+)?(?:0[-\s]+)?Is\b|type\s+(?:0[-\s]+)?Is\b|protruding.*sessile', re.I), "type Is"),
    (re.compile(r'paris\s+(?:classification\s+)?(?:0[-\s]+)?Ip\b|type\s+(?:0[-\s]+)?Ip\b|protruding.*pedunculated', re.I), "type Ip"),
    (re.compile(r'type\s+(?:0[-\s]+)?IIa\b|paris\s+(?:classification\s+)?(?:0[-\s]+)?IIa\b', re.I), "type IIa"),
    (re.compile(r'type\s+(?:0[-\s]+)?IIb\b|paris\s+(?:classification\s+)?(?:0[-\s]+)?IIb\b', re.I), "type IIb"),
    (re.compile(r'type\s+(?:0[-\s]+)?IIc\b|paris\s+(?:classification\s+)?(?:0[-\s]+)?IIc\b', re.I), "type IIc"),
    (re.compile(r'type\s+(?:0[-\s]+)?III\b|paris\s+(?:classification\s+)?(?:0[-\s]+)?III\b', re.I), "type III"),

    (re.compile(r'\bsemi-?pedunculated\b', re.I), "semi-pedunculated"),
    (re.compile(r'\bsemi-?sessile\b', re.I), "semi-sessile"),
    (re.compile(r'\bmulti-?lobulated\b', re.I), "multi-lobulated"),
    (re.compile(r'\blaterally\s+spreading\b|LST', re.I), "laterally spreading"),
    (re.compile(r'\bpedunculated\b', re.I), "pedunculated"),
    (re.compile(r'\bsessile\b', re.I), "sessile"),
    (re.compile(r'\bflat\b', re.I), "flat"),
    (re.compile(r'\bmass\b', re.I), "mass"),
]

def extract_morphology_raw(text: str) -> Optional[str]:
    # Supervisor feedback 03.07.26: when the same polyp phrase says
    # "sessile and semi-sessile", match nurse convention and prefer sessile.
    if re.search(r'\bsessile\s+and\s+semi-?sessile\b|\bsemi-?sessile\s+and\s+sessile\b', text, re.I):
        return "sessile"
    for pat, label in MORPHOLOGY_RAW:
        if pat.search(text):
            return label
    for ent in SPACY_ENGINE.entities(text, "MORPHOLOGY"):
        morph_norm, score = normalise(ent, FINDING_MORPHOLOGY_VOCAB, threshold=65)
        if score >= 0.65 and not morph_norm.startswith("[UNMATCHED"):
            return morph_norm
    return None


# ─────────────────────────────────────────────────────────────────
# SIZE EXTRACTION
# ─────────────────────────────────────────────────────────────────
def extract_size(text: str) -> str:
    # SIZE CAN BE WRITTEN 3 DIMENSIONALLY, 2 DIMENSIONALLY, AS A RANGE OR AS
    # A SINGLE MEASUREMENT, mm can also appear on either the first, second or last number
    # Range: "4 to 6 mm" or "4-6mm"
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:to|-)\s*(\d+(?:\.\d+)?)\s*(mm|cm)', text, re.I)
    if m:
        lo, hi, unit = float(m.group(1)), float(m.group(2)), m.group(3).lower()
        if unit == "cm":
            lo, hi = lo * 10, hi * 10
        return f"{int(lo)}-{int(hi)}mm"
    # Single: "7 mm"
    m = re.search(r'(\d+(?:\.\d+)?)\s*(mm|cm)', text, re.I)
    if m:
        val, unit = float(m.group(1)), m.group(2).lower()
        if unit == "cm":
            val *= 10
        return f"{int(val)}mm"
    return "Not documented"


def extract_site_count_pairs(text: str) -> list[tuple[str, int]]:
    """Extract explicit site-specific counts such as rectum(4), ascending colon (2)."""
    text = normalise_compound_site_text(text)
    pairs = []
    seen = set()
    site_terms = [
        ("Rectosigmoid junction", r"recto[-\s]?(?:sigmoid|sgmoid)(?:\s+(?:colon|junction))?|rectosigmoid(?:\s+junction)?"),
        ("Distal ascending colon", r"distal[-\s]+ascending(?:\s+colon)?"),
        ("Mid ascending colon", r"mid[-\s]+ascending(?:\s+colon)?"),
        ("Proximal ascending colon", r"proximal[-\s]+ascending(?:\s+colon)?"),
        ("Ascending colon", r"ascending(?:\s+colon)?"),
        ("Caecum", r"cae?cum|cae?cal"),
        ("Distal descending colon", r"distal[-\s]+descending(?:\s+colon)?"),
        ("Mid descending colon", r"mid[-\s]+descending(?:\s+colon)?"),
        ("Proximal descending colon", r"proximal[-\s]+descending(?:\s+colon)?"),
        ("Descending colon", r"descending(?:\s+colon)?"),
        ("Distal sigmoid colon", r"distal[-\s]+sigmoid(?:\s+colon)?"),
        ("Mid sigmoid colon", r"mid[-\s]+sigmoid(?:\s+colon)?"),
        ("Proximal sigmoid colon", r"proximal[-\s]+sigmoid(?:\s+colon)?"),
        ("Sigmoid colon", r"(?<!recto[-\s])sigmoid(?:\s+colon)?"),
        ("Distal transverse colon", r"distal[-\s]+transverse(?:\s+colon)?"),
        ("Mid transverse colon", r"mid[-\s]+transverse(?:\s+colon)?"),
        ("Proximal transverse colon", r"proximal[-\s]+transverse(?:\s+colon)?"),
        ("Transverse colon", r"transverse(?:\s+colon)?"),
        ("Hepatic flexure of colon", r"hepatic\s+flexure"),
        ("Splenic flexure of colon", r"splenic\s+flexure"),
        ("Upper rectum", r"upper[-\s]+rect(?:um|al)"),
        ("Mid rectum", r"mid[-\s]+rect(?:um|al)"),
        ("Distal rectum", r"distal[-\s]+rect(?:um|al)|lower\s+rectum"),
        ("Rectum", r"rectum|rectal"),
    ]
    for label, term in site_terms:
        for m in re.finditer(rf'\b(?:in\s+the\s+)?(?:{term})\s*\(?\s*(?:x\s*)?(\d+)\s*\)?', text, re.I):
            key = (label, m.start())
            if key not in seen:
                seen.add(key)
                pairs.append((label, int(m.group(1))))
    pairs.sort(key=lambda p: text.lower().find(p[0].split()[0].lower()))
    return [(s, c) for s, c in pairs if s in dedupe_sites_preferring_specific([x for x, _ in pairs])]


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
    if re.search(r'resection and retrieval were complete|complete resection|retrieved.*complete', t):
        return "Complete"
    if re.search(r'\bincomplete\b|\bpartial\b', t):
        return "Incomplete"
    if re.search(r'not retrieved\b', t):
        return "Not retrieved"
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
        return "No difficulty", "NA"

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
            return difficulty, reason

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

    explicit_quality_patterns = [
        r'\bbowel prep(?:aration)? (?:was|is)\s+(excellent|good|adequate|fair|poor|inadequate|unsatisfactory)\b',
        r'\bquality of the bowel prep(?:aration)? (?:was|is)\s+(excellent|good|adequate|fair|poor|inadequate|unsatisfactory)\b',
        r'\bbowel prep(?:aration)? quality[:\s]+(excellent|good|adequate|fair|poor|inadequate|unsatisfactory)\b',
        r'\bprep quality[:\s]+(excellent|good|adequate|fair|poor|inadequate|unsatisfactory)\b',
    ]

    for pat in explicit_quality_patterns:
        m = re.search(pat, note_clean, re.I)
        if m:
            prep_quality_raw = m.group(1).capitalize()
            break

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
            re.search(r'\b(?:bowel prep(?:aration)?\s+was\s+)?not good\b', note_clean, re.I)
            or re.search(r'\binadequate bowel prep(?:aration)?\b|\bpoor endoscopic visuali[sz]ation\b', note_clean, re.I)
            or total_bbps == "0"
        ):
            prep_quality_raw = "Poor"
        elif re.search(r'\bentire colon was well visuali[sz]ed\b', note_clean, re.I):
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
            r"(?:fairly\s+well|reasonably\s+well|adequately|fairly)\s*[\.,;:]?\s+but\b",
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

    manual_curation = "No specimens collected" if impression_has_no_specimens_collected(note) else "No"

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

# ─────────────────────────────────────────────────────────────────
# STAGE 2: ENDOSCOPY FINDINGS EXTRACTOR
# Splits by bullet/finding sentence, counts polyps, expands to N rows
# ─────────────────────────────────────────────────────────────────
@dataclass
class EndoscopyFinding:
    site: str
    site_raw: str
    morphology: str
    count: int
    size: str
    intervention: str
    completeness: str
    finding_type: str        # "Polyp" or "Biopsy"
    is_polyp: bool
    is_negated: bool
    is_hedged: bool
    hedge_type: str = ""
    confidence: float = 1.0
    raw_text: str = ""

def extract_endoscopy_findings(note: str) -> list[EndoscopyFinding]:
    # Clean report artefacts while preserving function structure
    note = normalise_compound_site_text(clean_clinical_text(note))

    # Try to isolate findings section — many formats
    findings_text = None
    for pat in [
        r'Findings?:(.*?)(?:Impression:|Scope Withdrawal|Recommendation|Complications:|$)',
        r'(?:Procedure:|PROCEDURE:)(.*?)(?:Impression:|Scope Withdrawal|Recommendation|Complications:|$)',
    ]:
        m = re.search(pat, note, re.I | re.S)
        if m:
            findings_text = m.group(1).strip()
            break

    # No section header found — scan full note but stop at Impression/Recommendation
    if not findings_text:
        note_trimmed = re.split(r'\s*(?:Impression|Recommendation|Complications):', note, flags=re.I)[0]
        findings_text = note_trimmed

    # STEP 1: Pre-split on ALL finding-starter sentences first
    # Supervisor feedback: compound sentences often contain multiple polyp
    # groups with different sizes/sites, so split before secondary groups
    # such as "2 x4mm polyps", "Six other smaller polyps", and ", and a 4mm polyp".
    findings_text = re.sub(
        r'(?<!\bto)(?<!-)\s+(?=(?:About\s+)?(?:One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten|\d+)\s+(?:x\s*)?\d*\s*(?:mm\s+)?(?:(?:other|much|smaller|tiny|sessile|flat|pedunculated|hyperplastic)\s+){0,5}polyps?\b)',
        '\n',
        findings_text,
        flags=re.I
    )
    findings_text = re.sub(
        r',\s+and\s+(?=(?:a|an)\s+\d+(?:\s*(?:to|-)\s*\d+)?\s*mm\s+polyp\b)',
        '\n',
        findings_text,
        flags=re.I
    )
    findings_text = re.sub(
        r'\s+(?=(?:A|An)\s+\d+(?:\s*(?:to|-)\s*\d+)?\s*mm\s+polyp\b)',
        '\n',
        findings_text,
        flags=re.I
    )
    findings_text = re.sub(
        r'\s+(?=(?:A|An)\s+\d+(?:\s*(?:to|-)\s*\d+)?\s*mm\s+(?:(?:inflam+ed|sessile|flat|pedunculated|semi-?sessile|semi-?pedunculated)\s+){0,4}polyp\b)',
        '\n',
        findings_text,
        flags=re.I
    )
    findings_text = re.sub(
        r'\s+(?=(?:A|An)\s+(?:small|large|tiny)\s+\(?\d+(?:\s*(?:to|-)\s*\d+)?\s*mm\)?\s+polyp\b)',
        '\n',
        findings_text,
        flags=re.I
    )

    # Insert newline before each new finding sentence
    findings_text = re.sub(
        r'(?<=\.)\s+(?='
        r'(?:A|An)\s+\d+(?:\s*(?:to|-)\s*\d+)?\s*mm\s+(?:(?:inflam+ed|sessile|flat|pedunculated|semi-?sessile|semi-?pedunculated)\s+){0,4}(?:polyp|mass|tumou?r)|'
        r'(?:A|An)\s+(?:small|large|tiny)\s+\(?\d+(?:\s*(?:to|-)\s*\d+)?\s*mm\)?\s+(?:polyp|mass|tumou?r)|'
        r'(?:A|An)\s+colonic\s+(?:tumou?r|mass)|'
        r'(?:One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten|\d+)\s+(?:\d+\s*mm\s+)?(?:paris|sessile|flat|pedunculated|polyps?)|'
        r'Biopsies?\s+were\s+taken|'
        r'(?:Multiple|Several|Many)\s+(?:sessile|semi-pedunculated|flat|pedunculated|polyps?)|'
        r'The\s+retroflexed?\s+view|'
        r'Internal\s+haemorrhoids?\s+were\s+found'
        r')',
        '\n', findings_text, flags=re.I
    )

    # Also isolate standalone "Biopsied." lines
    findings_text = re.sub(r'(?<!\w)(Biopsied\.?)(?!\w)', r'\nBiopsied\n', findings_text, flags=re.I)

    # STEP 2: After splitting, merge tumour sentence with its immediately following biopsy sentence
    # They will now be on consecutive lines
    findings_text = re.sub(
        r'((?:A|An)\s+colonic\s+(?:tumou?r|mass)[^\n]+)\n(Biopsies?\s+were\s+taken[^\n]+)',
        r'\1 \2', findings_text, flags=re.I
    )

    # Also insert newline before ALL-CAPS finding lines
    findings_text = re.sub(r'(?<=\s)([A-Z]{4,}[A-Z\s]+(?:CM|MM)\b)', r'\n\1', findings_text)

    # Split by bullet points if present, otherwise by sentence-level newlines
    if re.search(r'\n\s*[-•]\s+', findings_text):
        blocks = re.split(r'\n\s*[-•]\s+', findings_text)
        blocks = [re.sub(r'^[-•]\s+', '', b).strip() for b in blocks if len(b.strip()) > 15]
    else:
        raw_lines = re.split(r'\n+', findings_text)
        blocks = []
        current = ""
        for line in raw_lines:
            line = line.strip()
            if not line:
                continue
            # New finding starts with these patterns
            is_new_finding = bool(re.match(
                r'^(?:A\s+\d|An?\s+\d|An?\s+(?:small|large|tiny)\s+\(?\d|\d+\s+(?:polyp|sessile|flat)|'
                r'(?:one|two|three|four|five|six|seven|eight|nine|ten)'
                r'\s+(?:(?:hyperplastic|sessile|flat|pedunculated|small|tiny|other)\s+){0,5}(?:paris|sessile|flat|pedunculated|polyps?|[0-9]|\d)|'
                r'(?:(?:Multiple|Several|Many)|A\s+(?:sessile|flat|pedunculated|mass|colonic\s+(?:tumou?r|mass))))',
                line, re.I
            ))
            # Continuation lines belong to current block
            is_continuation = bool(re.match(
                r'^(?:The\s+polyp|These\s+polyp|This\s+polyp|Resection\s+and|The\s+defect|'
                r'Bleeding\s+had|To\s+prevent|The\s+polyps?\s+w)',
                line, re.I
            ))
            # Lines that should always start a new (non-finding) block
            is_terminal = bool(re.match(
                r'^(?:The\s+)?(?:retroflexed?|terminal\s+ileum|perianal|'
                r'internal\s+haemorrhoid|The\s+colon\s+appeared|The\s+exam)',
                line, re.I
            ))
            if is_new_finding and current:
                blocks.append(current)
                current = line
            elif is_continuation and current and not is_terminal:
                current = current + " " + line
            else:
                if current:
                    blocks.append(current)
                current = "" if is_terminal else line
        if current:
            blocks.append(current)

    findings = []
    for block in blocks:
        # ── Skip continuation-only polyp sentences ────────────────
        # Avoid creating duplicate "Unknown" findings from sentences like:
        # "The polyp was sessile. The polyp was removed..."
        if re.match(r'^(?:The|This|These)\s+polyps?\b', block, re.I):
            continue

        # Skip polyp mentions without a proper new finding structure
        # (no count/size intro AND no site)
        if (
            re.search(r'\bpolyps?\b', block, re.I)
            and not re.search(
                r'\b(?:A|An|One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten|\d+)\b',
                block, re.I
            )
            and not extract_site_raw(block)
        ):
            continue

        # Skip ALL-CAPS lines that are clinical notes, not findings
        if re.match(r'^[A-Z\s\d]+$', block) and not re.search(r'\bpolyp\b|\bmass\b', block, re.I):
            continue

        # Skip diverticula-only findings — not polyps
        if re.search(r'\bdiverticul', block, re.I) and not POLYP_INDICATOR_RE.search(
                re.sub(r'\bdiverticul\w*', '', block, flags=re.I)):
            continue

        if not POLYP_INDICATOR_RE.search(block):
            continue

        # Skip purely normal/negative sentences and retroflexion sentences
        if re.match(r'^(?:The\s+)?(?:terminal\s+ileum|perianal|rectal\s+exam|ileum|internal\s+haemorrhoid)', block, re.I):
            continue
        primary_sentence_for_skip = re.split(r'\.\s+', block)[0]
        if re.search(r'\bretroflexed?\s+view\b|\bretroflexion\b', primary_sentence_for_skip, re.I):
            continue

        # ── Multi-site detection (up to 3 sites in one sentence) ────
        # Only look in the primary finding sentence (first sentence of block)
        # to avoid picking up sites from "terminal ileum appeared normal" etc.
        primary_sentence = normalise_compound_site_text(re.split(r'\.\s+', block)[0])
        site_count_pairs = extract_site_count_pairs(primary_sentence)
        all_site_mentions = []
        seen_sites = set()
        for pat, label in SITE_RAW_PATTERNS:
            if pat.search(primary_sentence) and label not in seen_sites:
                seen_sites.add(label)
                all_site_mentions.append(label)
        all_site_mentions = dedupe_sites_preferring_specific(all_site_mentions)
        # Only use multi-site if the primary sentence explicitly lists multiple sites
        # (must contain 'and' or comma between site names)
        has_multi_site_syntax = bool(re.search(
            r'\b(?:and|,)\s*(?:in\s+(?:the\s+)?)?(?:' +
            '|'.join(re.escape(s.split()[0]) for s in FINDING_SITE_VOCAB if s not in ('Unknown','Other')) +
            r')\b', primary_sentence, re.I
        ))
        multi_sites = all_site_mentions if (len(all_site_mentions) >= 2 and has_multi_site_syntax) else []
        if site_count_pairs:
            # Keep explicit counts (e.g. distal sigmoid x2) while retaining
            # the other sites listed in the same sentence.
            multi_sites = dedupe_sites_preferring_specific(all_site_mentions + [site for site, _ in site_count_pairs])

        site_raw = all_site_mentions[0] if all_site_mentions else (extract_site_raw(block) or "Unknown")
        site_norm, site_conf = normalise(site_raw, FINDING_SITE_VOCAB, threshold=70)

        morph_raw = extract_morphology_raw(block) or "Not documented"
        morph_norm, morph_conf = normalise(morph_raw, FINDING_MORPHOLOGY_VOCAB, threshold=70)

        count = parse_polyp_count(block)
        size  = extract_size(block)

        interv_raw  = extract_intervention_raw(block)
        if interv_raw == "Not documented" and not re.search(r'\bdestroyed\b|diathermy|ablated', block, re.I) and re.search(
            r'(?:were\s+all|all|also)\s+excised\s+with\s+cold\s+snare|cold\s+snare',
            findings_text,
            re.I
        ):
            # Supervisor feedback: list-style sentences can put "all excised
            # with cold snare" after the final site, after we split the list.
            interv_raw = "Cold snare"
        interv_norm, _ = normalise(interv_raw, INTERVENTION_VOCAB, threshold=60)

        # ── Type inference ───────────────────────────────────────────
        # Rule: POLYP wins if the word "polyp" appears anywhere in the block
        # Biopsy only when: mass/tumour identified, OR random/surveillance biopsy
        #                   AND no polyp mentioned
        has_polyp_word = bool(re.search(r'\bpolyps?\b', block, re.I))
        is_explicit_tumour = bool(re.search(
            r'\b(?:tumou?r|mass)\s+(?:was\s+)?(?:identified|found|seen|biopsied)',
            block, re.I))
        is_mass = (morph_norm == "mass" or is_explicit_tumour) and not has_polyp_word
        is_random_biopsy = bool(re.search(
            r'random\s+biopsy|surveillance\s+biopsy|biops(?:y|ied)\s+(?:of\s+)?(?:flat\s+)?mucosa|'
            r'microscopic colitis|random colon biopsy|biopsies\s+were\s+taken',
            block, re.I
        )) and not has_polyp_word
        finding_type = "Biopsy" if (is_mass or is_random_biopsy) else "Polyp"

        # ── Completeness inference ───────────────────────────────────
        comp_raw = extract_completeness_raw(block)
        if comp_raw != "Not documented":
            comp_norm, _ = normalise(comp_raw, INTERVENTION_COMPLETENESS_VOCAB, threshold=60)
        elif finding_type == "Biopsy":
            comp_norm = "Not applicable"
        elif interv_norm == "Biopsy only":
            comp_norm = "Not retrieved"
        elif interv_norm == "Not excised":
            comp_norm = "Not retrieved"
        elif interv_norm == "Not documented":
            comp_norm = "Not retrieved"
        else:
            comp_norm = "Not reported"

        is_neg = bool(NEGATION_RE.search(primary_sentence))
        is_hed = bool(HEDGE_UNCERTAIN_RE.search(block) or HEDGE_INTENDED_RE.search(block))
        htype  = "uncertain" if HEDGE_UNCERTAIN_RE.search(block) else (
                 "intended" if HEDGE_INTENDED_RE.search(block) else "")

        confidence = min(site_conf, morph_conf) if site_conf > 0 and morph_conf > 0 else 0.8
        if is_hed: confidence *= 0.7
        if "[UNMATCHED" in site_norm: confidence *= 0.5

        if multi_sites:
            # Distribute count as evenly as possible across sites
            explicit_counts = dict(site_count_pairs)
            n = len(multi_sites)
            remaining_total = max(count - sum(explicit_counts.values()), 0)
            unspecified_sites = [s for s in multi_sites if s not in explicit_counts]
            base = remaining_total // len(unspecified_sites) if unspecified_sites else 0
            remainder = remaining_total % len(unspecified_sites) if unspecified_sites else 0
            for idx, site_r in enumerate(multi_sites):
                if site_r in explicit_counts:
                    cnt = explicit_counts[site_r]
                else:
                    cnt = base + (1 if idx < remainder else 0)
                    if cnt == 0:
                        cnt = 1
                site_n, _ = normalise(site_r, FINDING_SITE_VOCAB, threshold=70)
                findings.append(EndoscopyFinding(
                    site=site_n, site_raw=site_r, morphology=morph_norm,
                    count=cnt, size=size, intervention=interv_norm,
                    completeness=comp_norm, finding_type=finding_type,
                    is_polyp=(finding_type == "Polyp"),
                    is_negated=is_neg, is_hedged=True,
                    hedge_type="multi_site",
                    confidence=round(confidence * 0.85, 2),
                    raw_text=block,
                ))
        else:
            findings.append(EndoscopyFinding(
                site=site_norm, site_raw=site_raw, morphology=morph_norm,
                count=count, size=size, intervention=interv_norm,
                completeness=comp_norm, finding_type=finding_type,
                is_polyp=(finding_type == "Polyp"),
                is_negated=is_neg, is_hedged=is_hed,
                hedge_type=htype,
                confidence=round(confidence, 2),
                raw_text=block,
            ))

    return findings

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

DIAG_PATTERNS = [
    # Malignant — checked first, highest priority
    (re.compile(r'squamous\s+(?:cell\s+)?carcinoma|\bSCC\b', re.I), "Squamous cell carcinoma"),
    (re.compile(r'adenocarcinoma|invasive.*carcinoma|malignant.*adenocarcinoma|low.grade\s+colonic\s+adenocarcinoma', re.I), "Adenocarcinoma"),
    (re.compile(r'intramucosal\s+carcinoma', re.I), "Adenocarcinoma"),
    (re.compile(r'\bcarcinoma\b', re.I), "Adenocarcinoma"),
    # Adenoma subtypes — most specific first
    (re.compile(r'tubular\s+and\s+villous|tubulovillous\s+adenoma', re.I), "Tubulovillous adenoma"),
    (re.compile(r'villous\s+adenoma', re.I), "Villous adenoma"),
    (re.compile(r'tubular\s+adenoma|portions?\s+of\s+tubular\s+adenoma|fragments?\s+of\s+tubular\s+adenoma', re.I), "Tubular adenoma"),
    (re.compile(r'sessile\s+serrated\s+(?:adenoma|lesion|polyp)|SSA\b|SSL\b', re.I), "Sessile Serrated Polyp"),
    (re.compile(r'traditional\s+serrated\s+adenoma|TSA\b', re.I), "Traditional Serrated Adenoma"),
    (re.compile(r'serrated\s+adenoma', re.I), "Sessile Serrated Polyp"),
    (re.compile(r'hyperplastic\s+polyp|benign\s+hyperplastic|goblet\s+cell.*hyperplastic', re.I), "Hyperplastic polyp"),
    (re.compile(r'inflammatory\s+polyp', re.I), "Inflammatory polyp"),
    (re.compile(r'granulation\s+tissue', re.I), "Granulation tissue"),
    (re.compile(r'\bmelanosis\s+coli\b', re.I), "Melanosis coli"),
    (re.compile(r'\b(?:acute\s+)?ileitis\b', re.I), "Ileitis"),
    (re.compile(r'lipoma', re.I), "Lipoma"),
    (re.compile(r'leiomyoma', re.I), "Leiomyoma"),
    (re.compile(r'neuroendocrine', re.I), "Neuroendocrine tumour"),
    (re.compile(r'juvenile\s+polyp', re.I), "Juvenile polyp"),
    (re.compile(r'pseudo.?polyp', re.I), "Pseudo-polyp"),
    # "No significant pathology", "no diagnostic lesion", "no specific diagnosis",
    # "no histological polyp", "benign prominent mucosal fold", "unremarkable mucosa"
    (re.compile(
        r'no\s+definite\s+polyp\s+identified|'
        r'no\s+significant\s+pathology|no\s+diagnostic\s+lesion|no\s+specific\s+diagnosis|'
        r'no\s+significant\s+histoligcal\s+abnormality(?:\s+identified)?|'
        r'no\s+histolog(?:ical)?\s+(?:evidence\s+of\s+a\s+)?polyp|'
        r'benign\s+prominent\s+mucosal\s+fold|unremarkable\s+(?:colonic\s+)?mucosa|'
        r'no\s+serrated\s+polyp.*no.*dysplas|degenerate\s+fragment|'
        r'within\s+normal\s+(?:histological\s+)?limits|normal\s+histolog|'
        r'normal\s+colonic\s+mucosa|'
        r'colonic\s+mucosa\s+within\s+normal\s+limits|'
        r'mild\s+architectural\s+distortion|'
        r'biopsy\s*:\s*normal\b|'
        r'(?:^|\:\s*)normal\.?\s*(?:$|\s)',
        re.I), "Normal tissue"),
]

DYSPLASIA_PATTERNS = [
    # Adenocarcinoma-level — most specific first
    (re.compile(r'low.grade\s+(?:colonic\s+)?adenocarcinoma|adenocarcinoma.*low.grade|adenocarcinoma.*low\s+grade', re.I), "Adenocarcinoma  low grade"),
    (re.compile(r'high.grade\s+(?:colonic\s+)?adenocarcinoma|adenocarcinoma.*high.grade|adenocarcinoma.*high\s+grade|'
                r'poorly\s+differentiated.*adenocarcinoma', re.I), "Adenocarcinoma  high grade"),
    (re.compile(r'invasive.*adenocarcinoma|adenocarcinoma.*invasive|Sm[123]\b|submucosal\s+invasion', re.I), "Adenocarcinoma  high grade"),
    (re.compile(r'intramucosal\s+carcinoma', re.I), "Intramucosal carcinoma"),
    # HGD — check focal/multifocal qualifiers before generic HGD.
    # Supervisor feedback 26.06.26: preserve clinically relevant qualifiers
    # such as "focal" in "focal high-grade adenomatous dysplasia".
    (re.compile(r'\bfocal\s+high.grade(?:\s+adenomatous)?\s+dysplasia\b', re.I), "Focal high-grade dysplasia"),
    (re.compile(r'(?<!no )(?<!without )(?<!towards )high.grade\s+dysplasia|HGD\b|severe\s+(?:\(high\s+grade\)\s+)?dysplasia|'
                r'focal\s+severe|suspicious\s+for\s+invasive', re.I), "High Grade dysplasia"),
    # LGD
    (re.compile(r'(?<!no )(?<!without )low.grade\s+dysplasia|LGD\b|low\s+grade\s+epithelial\s+dysplasia|'
                r'(?:mild|moderate)\s+\(low\s+grade\)\s+dysplasia|focal\s+low\s+grade\s+dysplasia', re.I), "Low Grade dysplasia"),
    # Supervisor feedback 19.06.26: pathology often writes "low-grade tubular
    # adenoma" or "high-grade tubular adenoma" without repeating "dysplasia".
    (re.compile(r'(?<!no )(?<!without )\bhigh.grade\b(?=.{0,80}\b(?:adenoma|dysplasia|lesion|polyp)\b)', re.I), "High Grade dysplasia"),
    (re.compile(r'(?<!no )(?<!without )\blow.grade\b(?=.{0,80}\b(?:adenoma|dysplasia|lesion|polyp)\b)', re.I), "Low Grade dysplasia"),
    # No dysplasia — must come after LGD/HGD checks
    (re.compile(
        r'no\s+(?:high.grade\s+)?dysplasia|no\s+dysplasia|no\s+evidence\s+of.*dysplas|'
        r'no\s+conventional\s+dysplasia|without\s+dysplasia|'
        r'non[-\s]?dysplastic|negative\s+for\s+dysplasia|'
        r'no\s+serrated\s+polyp.*dysplas|no\s+dysplas.*malign',
        re.I), "No dysplasia"),
    (re.compile(r'unable\s+to\s+assess', re.I), "Unable to assess"),
]

def clean_clinical_text(text: str) -> str:
    """Normalise common report artefacts and spacing before NLP matching."""
    if text is None:
        return ""
    text = str(text)
    text = re.sub(r'_?x000D_|_?x000A_', ' ', text, flags=re.I)
    text = re.sub(r'\r|\n|\t', ' ', text)
    text = re.sub(r'[\u2010-\u2015]', '-', text)  # normalise dash variants
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
    """Return the colorectal section when Upper GI and lower-GI reports are combined.

    Supervisor feedback 26.06.26: some source reports contain both Upper GI
    Endoscopy and Colonoscopy/Flexible Sigmoidoscopy sections. We preserve the
    original report in Excel, but all colorectal NLP should run only on the
    colonoscopy/sigmoidoscopy section to avoid mixing upper-GI findings.
    """
    if is_missing_report_text(note) or is_upper_endoscopy_type(procedure_type):
        return "" if is_missing_report_text(note) else str(note)

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
        return str(note)

    def kind(title: str) -> str:
        return "colorectal" if re.search(r'\b(?:colon|sigmoid)\b', title, re.I) else "upper"

    colorectal_sections = []
    first_heading_start = headings[0][0]
    prefix = text[:first_heading_start].strip()
    for idx, (start, end, title) in enumerate(headings):
        section_end = headings[idx + 1][0] if idx + 1 < len(headings) else len(text)
        if kind(title) == "colorectal":
            colorectal_sections.append(text[start:section_end].strip())

    if not colorectal_sections:
        # Some mixed reports label only the later Upper GI section with a formal
        # heading; the earlier colorectal section is introduced as
        # "Procedure: Colonoscopy". In that common layout, drop everything from
        # the Upper GI heading onward.
        if headings and re.search(r'\b(?:colonoscopy|sigmoidoscopy|flexible\s+sigmoid)', procedure_type, re.I):
            before_first_upper = text[:headings[0][0]].strip()
            if re.search(r'\bProcedure\s*:?\s*(?:Colonoscopy|Flexible\s+Sigmoid)', before_first_upper, re.I):
                return before_first_upper
        return str(note)

    return "\n".join(part for part in [prefix] + colorectal_sections if part).strip()


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

def extract_diagnosis(text: str) -> str:
    t = clean_clinical_text(text)

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
    """
    path_note = clean_clinical_text(path_note)
    m = re.search(r'Microscopic[:\s]*(.*?)(?:Comment|Macroscopic|Supplementary|Addendum|$)', path_note, re.I | re.S)
    if not m:
        return []

    micro_text = m.group(1).strip()
    micro_text = re.sub(r'(?<!\n)\s*(\d+)\.\s+(?=["\'A-Z])', r'\n\1. ', micro_text)
    items = re.split(r'\n\s*\d+\.\s*', micro_text)
    items = [i.strip() for i in items if len(i.strip()) > 25]

    specimens = []
    for item in items:
        if not re.search(r'\b(polyp|adenoma|serrated|hyperplastic|polypectomy|biopsy|bx)\b', item, re.I):
            continue
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
            specimen_type="Biopsy" if is_biopsy_specimen_text(item) else "Polyp",
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

def extract_pathology_specimens(path_note: str) -> list[PathologySpecimen]:
    path_note = clean_clinical_text(path_note)
    # Focus on Diagnosis section
    m = re.search(r'Diagnosis[:\s]*(.*?)(?:Macroscopic|Microscopic|Comment|$)', path_note, re.I | re.S)
    diag_text = m.group(1).strip() if m else path_note

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


# ─────────────────────────────────────────────────────────────────
# STAGE 4: MERGE ENGINE
# Endoscopy drives row count. Match by site. Worst diagnosis wins.
# ─────────────────────────────────────────────────────────────────
def site_match_score(endo_site: str, path_site: str) -> int:
    rectal_sites = {"Rectum", "Upper rectum", "Mid rectum", "Distal rectum"}
    if endo_site in rectal_sites and path_site in rectal_sites:
        return 92
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
        "confidence": "0%",
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
    size_override=None
):
    size = size_override if size_override else ef.size
    diag_group, dysplasia = postprocess_pathology_result(diag_group, dysplasia)
    manual_curation, curation_reason = manual_curation_decision(
        diag_group=diag_group,
        dysplasia=dysplasia,
        path_matched=path_matched,
        mixed=mixed,
        count_mismatch=count_mismatch,
        current_reason=curation_reason,
    )

    rows.append({
        "finding_id": f"F-{str(uuid.uuid4())[:6].upper()}",
        "CMBSMain": CMBSMain,
        "site": ef.site,
        "finding_morphology": ef.morphology,
        "number_categorical": count,
        "size_mm": size,
        "finding_intervention_performed": ef.intervention,
        "finding_intervention_completeness": ef.completeness,
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
        "confidence": f"{int(ef.confidence * 100)}%",
        "manual_curation": manual_curation,
        "curation_reason": curation_reason,
        "urgent_flag": "No",
        "extraction_discrepancy_flag": "No",
    })

def merge_findings(
    endo_findings: list[EndoscopyFinding],
    path_specimens: list[PathologySpecimen],
    CMBSMain: str
) -> list[dict]:

    """
    Row generation rules:
    - Endoscopy drives row count for polyp findings
    - When endoscopy has no findings but pathology has specimens → generate pathology-only rows
    - Same site + same diagnosis → 1 row
    - Different diagnoses at same site → split rows
    """

    # If no endoscopy findings but pathology has specimens → pathology-only rows
    if not endo_findings and path_specimens:
        rows = []
        for ps in path_specimens:
            if ps.specimen_type == "Biopsy" and ps.site_norm == "Unknown":
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
                "confidence":                        "85%",
                "manual_curation":                   "Yes",
                "curation_reason": (
                    "Pathology-only polyp finding — no endoscopy finding extracted"
                    if is_polyp_case
                    else "Biopsy-only procedure — no endoscopy polyp findings"
                ),
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
        best_specimen = None
        best_score = 0
        # Supervisor feedback 26.06.26: when pathology has separate same-site
        # specimens, avoid reusing the first specimen for every matching
        # endoscopy row. Fall back to all specimens only when nothing unused
        # matches.
        candidate_specimens = [ps for ps in path_specimens if id(ps) not in matched_specimen_ids] or path_specimens
        for ps in candidate_specimens:
            score = site_match_score(ef.site, ps.site_norm)
            if score > best_score:
                best_score = score
                best_specimen = ps

        path_matched = best_specimen is not None and best_score >= 60

        if not path_matched:
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
        rows.append({
            "finding_id": f"F-{str(uuid.uuid4())[:6].upper()}",
            "CMBSMain": CMBSMain,
            "site": ps.site_norm,
            "finding_morphology": "Not documented",
            "number_categorical": ps.fragment_count_total if 0 < ps.fragment_count_total < 99 else 1,
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
            "confidence": "80%",
            "manual_curation": "Yes",
            "curation_reason": (
                "Pathology-only polyp finding — no matching endoscopy row"
                if is_polyp_case
                else "Biopsy-only pathology specimen — no matching endoscopy row"
            ),
            "urgent_flag": "No",
            "extraction_discrepancy_flag": "Yes",
        })

    # Supervisor feedback 03.07.26: add explicit endoscopy/path report count
    # imbalance wording after all matched and unmatched rows have been built.
    return apply_report_count_discrepancy_reason(rows, endo_findings, path_specimens)
# ─────────────────────────────────────────────────────────────────
# STAGE 5: EXCEL OUTPUT
# ─────────────────────────────────────────────────────────────────
HEADER_BLUE   = "1F4E79"
HEADER_GREEN  = "1A5276"
ALT_BLUE      = "EBF3FB"
ALT_GREEN     = "E8F6F3"
FLAG_YELLOW   = "FFF2CC"
FLAG_RED      = "FDECEA"

def make_border():
    s = Side(style="thin", color="CCCCCC")
    return Border(left=s, right=s, top=s, bottom=s)

def header_cell(cell, color):
    # Plain workbook output requested: white background and black text only.
    cell.font      = Font(bold=True, color="000000", name="Arial", size=10)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border    = make_border()

def data_cell(cell, alt=False, flag=None, alt_color=ALT_BLUE):
    # Keep manual-curation and unmatched values as data only; do not colour cells.
    cell.font      = Font(name="Arial", size=10, color="000000")
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border    = make_border()
    cell.fill      = PatternFill(fill_type=None)

def write_sheet(ws, headers, col_widths, rows_data, header_color, alt_color, flag_col=None):
    for col, h in enumerate(headers, 1):
        header_cell(ws.cell(row=1, column=col, value=h), header_color)
        ws.column_dimensions[get_column_letter(col)].width = col_widths[col-1]
    ws.row_dimensions[1].height = 40

    for row_i, row_data in enumerate(rows_data, 2):
        for col, val in enumerate(row_data, 1):
            data_cell(ws.cell(row=row_i, column=col, value=val))
        ws.row_dimensions[row_i].height = 30

    ws.freeze_panes = "A2"

def build_excel_multi(proc_rows: list[dict], finding_rows: list[dict], output_path: str, indication_rows: Optional[list[dict]] = None):
    """Build Excel with multiple procedure rows and their linked findings."""
    indication_rows = indication_rows or []
    wb = Workbook()

    # ── Sheet 1: Procedure ─────────────────────────────────────────
    ws1 = wb.active
    ws1.title = "Procedure"
    proc_headers = [
        "registration_id", "procedure_id", "procedure_finding_id",
        "procedure_date", "procedure_type", "procedure_location",
        "procedure_provider",
        "Procedure_completeness", "Reason_incomplete",
        "Intubation_distance", "Bowel_prep_given", "Bowel_prep_quality",
        "BBPS_right", "BBPS_transverse", "BBPS_left", "Total_BBPS_score",
        "Withdrawal_time", "Manual_curation", "Procedure_room",
        "Instrument_name", "Medication", "Tolerated_procedure",
        "Difficulty", "Difficulty_reason", "Complications",
        "Procedure_finding_2", "Procedure_finding_3", "Procedure_finding_4", "Procedure_finding_5",
        "Urgent_flag", "Extraction_discrepancy_flag",
        "pathology_report_text", "endoscopy_report_text"
    ]
    proc_widths = [18,16,24,16,20,22,24,20,18,20,22,20,12,16,12,16,16,24,16,18,16,18,32,20,20,20,20,20,20,16,26,100,100]

    rows_data = [[
        p.get("registration_id", ""),
        p["procedure_id"],
        p.get("procedure_finding_id", ""),
        p.get("procedure_date", ""),
        p.get("procedure_type", ""),
        p.get("procedure_location", ""),
        p.get("procedure_provider", ""),
        p["procedure_completeness"],
        p["reason_incomplete"],
        p["intubation_distance"],
        p["bowel_prep_given"],
        p["bowel_prep_quality"],
        p["bbps_right"],
        p["bbps_transverse"],
        p["bbps_left"],
        p["total_bbps_score"],
        p["withdrawal_time"],
        p["manual_curation"],
        p["procedure_room"],
        p["instrument_name"],
        p["medication"],
        p["tolerated_procedure"],
        p["difficulty"],
        p["difficulty_reason"],
        p["complications"],
        p.get("procedure_finding_2", p.get("other_findings", "None")),
        p.get("procedure_finding_3", ""),
        p.get("procedure_finding_4", ""),
        p.get("procedure_finding_5", ""),
        p.get("urgent_flag", "No"),
        p.get("extraction_discrepancy_flag", "No"),
        p.get("pathology_report_text", ""),
        p.get("endoscopy_report_text", ""),
    ] for p in proc_rows]

    write_sheet(ws1, proc_headers, proc_widths, rows_data, HEADER_BLUE, ALT_BLUE)

    # ── Sheet 2: Findings ──────────────────────────────────────────
    ws2 = wb.create_sheet("Findings")
    find_headers = [
        "finding_id", "registration_id", "procedure_id", "procedure_finding_id",
        "procedure_date", "procedure_type", "procedure_location",
        "Site", "Finding_morphology",
        "Number_categorical", "Size_mm",
        "Finding_intervention_performed", "Finding_intervention_completeness",
        "Type", "Diagnosis_group", "Dysplasia",
        "Path_site_matched", "Path_match_score", "Mixed_pathology",
        "Count_mismatch", "Negated", "Hedged", "Confidence",
        "Manual_curation", "Curation_reason",
        "Urgent_flag", "Extraction_discrepancy_flag",
        "pathology_report_text", "endoscopy_report_text"
    ]
    find_widths = [14,18,16,24,16,20,22,22,20,14,12,24,26,10,28,24,22,14,14,14,10,10,12,14,40,16,26,100,100]

    manual_cur_col = find_headers.index("Manual_curation") + 1

    frows_data = []
    for fr in finding_rows:
        frows_data.append([
            fr["finding_id"],
            fr.get("registration_id", ""),
            fr["procedure_id"],
            fr.get("procedure_finding_id", ""),
            fr.get("procedure_date", ""),
            fr.get("procedure_type", ""),
            fr.get("procedure_location", ""),
            fr["site"],
            fr["finding_morphology"],
            fr["number_categorical"],
            fr["size_mm"],
            fr["finding_intervention_performed"],
            fr["finding_intervention_completeness"],
            fr["type"],
            fr["diagnosis_group"],
            fr["dysplasia"],
            fr["path_site_matched"],
            fr["path_match_score"],
            fr["mixed_pathology"],
            fr["count_mismatch"],
            fr["negated"],
            fr["hedged"],
            fr["confidence"],
            fr["manual_curation"],
            fr["curation_reason"],
            fr.get("urgent_flag", "No"),
            fr.get("extraction_discrepancy_flag", "No"),
            fr.get("pathology_report_text", ""),
            fr.get("endoscopy_report_text", ""),
        ])

    write_sheet(ws2, find_headers, find_widths, frows_data,
                HEADER_GREEN, ALT_GREEN, flag_col=manual_cur_col)

    # ── Sheet 3: Indications ───────────────────────────────────────
    ws3 = wb.create_sheet("Indications")
    indication_headers = [
        "indication_id", "registration_id", "procedure_id", "procedure_finding_id",
        "procedure_date", "procedure_type", "procedure_location", "indication", "indication_nbcsp",
        "endoscopy_report_text"
    ]
    indication_widths = [14, 18, 16, 24, 16, 20, 22, 100, 100, 100]
    indication_data = [[
        row.get("indication_id", ""),
        row.get("registration_id", ""),
        row.get("procedure_id", ""),
        row.get("procedure_finding_id", ""),
        row.get("procedure_date", ""),
        row.get("procedure_type", ""),
        row.get("procedure_location", ""),
        row.get("indication", ""),
        row.get("indication_nbcsp", ""),
        row.get("endoscopy_report_text", ""),
    ] for row in indication_rows]
    write_sheet(ws3, indication_headers, indication_widths, indication_data, HEADER_BLUE, ALT_BLUE)

    # ── Legend ─────────────────────────────────────────────────────
    lr = len(finding_rows) + 4
    ws2.cell(row=lr, column=1, value="Legend:").font = Font(bold=True, name="Arial", size=10)

    wb.save(output_path)

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
    import pandas as pd

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


def _match_text(value) -> str:
    """Normalise a finding attribute for source-to-extraction matching."""
    if value is None:
        return ""
    value = str(value).strip()
    if not value or value.lower() in {"nan", "none", "not documented", "not reported"}:
        return ""
    return re.sub(r'\s+', ' ', value).lower()


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
    import pandas as pd

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


def run_batch(
    df_path: str,
    output_path: str,
    sheet_name: Optional[str] = "vw_scoop_procedures",
    findings_sheet: Optional[str] = "vw_scoop_procedure_findings",
    year: int = 2024,
):
    """Run pipeline on a batch Excel file using real source columns."""
    import pandas as pd

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

    build_excel_multi(all_proc_rows, all_finding_rows, output_path, all_indication_rows)
    print(f"\nTotal: {len(all_proc_rows)} procedures, {len(all_finding_rows)} findings, {len(all_indication_rows)} indications")

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
        default="data/ testing NLP output.xlsx",
        help="Output workbook path"
    )
    parser.add_argument("--sheet", default="Sheet1", help="Procedure sheet to process")
    parser.add_argument("--findings-sheet", default=None, help="Sheet containing procedure_finding_id")
    parser.add_argument("--year", type=int, default=2024, help="Procedure year to process")
    args = parser.parse_args()
    run_batch(args.input, args.output, args.sheet, args.findings_sheet, args.year)

