import re
from spacy_engine import SPACY_ENGINE
from vocab import FINDING_MORPHOLOGY_VOCAB
from typing import Optional
from textClean import normalise

# ─────────────────────────────────────────────────────────────────
# MORPHOLOGY EXTRACTION
# ─────────────────────────────────────────────────────────────────

## Per Supervisor Feedback 1.09.2026 - TA
## List of paris classificatoins that should be in the database are as follows,
## type Ip, type Is, type IIa, type IIb, type IIc, type IIa+IIb, type IIa+Is, type IIa+IIc , type Isp

MORPHOLOGY_RAW = [
    ## changes made to each row as the "Paris-0" needs to be accounted for
    (re.compile(r'type\s+IIa\+IIc\b|paris\s+(?:classification\s+)?IIa\+IIc\b|IIa\+IIc', re.I), "type IIa+IIc"),
    (re.compile(r'type\s+IIc\+IIa\b|paris\s+(?:classification\s+)?IIc\+IIa\b|IIc\+IIa', re.I), "type IIa+IIc"),

    (re.compile(r'paris\s+0-IIa\+c\b', re.I), "Manual Curation"),
    (re.compile(r'paris\s+0-IIa\+Is\b', re.I), "type IIa+Is"),
    (re.compile(r'paris\s+0-Is\+IIa\b', re.I), "type IIa+IS"),
    (re.compile(r'paris\s+0-IIa\+Ib\b', re.I), "Manual Curation"),
    (re.compile(r'paris\s+0-Ib\+IIa\b', re.I), "Manual Curation"),
    (re.compile(r'paris\s+0-Is\+IIc\b', re.I), "Manual Curation"),
    (re.compile(r'paris\s+0-IIc\+Is\b', re.I), "Manual Curation"),

    (re.compile(r'paris\s+(?:classification\s+)?(?:0[-\s]+)?Is\b|type\s+(?:0[-\s]+)?Is\b|protruding.*sessile', re.I), "type Is"),
    (re.compile(r'paris\s+(?:classification\s+)?(?:0[-\s]+)?Ip\b|type\s+(?:0[-\s]+)?Ip\b|protruding.*pedunculated', re.I), "type Ip"),
    (re.compile(r'paris\s+(?:classification\s+)?(?:0[-\s]+)?Isp\b|type\s+(?:0[-\s]+)?Isp\b', re.I), "type Isp"),
    (re.compile(r'type\s+(?:0[-\s]+)?IIa\b|paris\s+(?:classification\s+)?(?:0[-\s]+)?IIa\b', re.I), "type IIa"),
    (re.compile(r'type\s+(?:0[-\s]+)?IIb\b|paris\s+(?:classification\s+)?(?:0[-\s]+)?IIb\b', re.I), "type IIb"),
    (re.compile(r'type\s+(?:0[-\s]+)?IIc\b|paris\s+(?:classification\s+)?(?:0[-\s]+)?IIc\b', re.I), "type IIc"),
    (re.compile(r'type\s+(?:0[-\s]+)?III\b|paris\s+(?:classification\s+)?(?:0[-\s]+)?III\b', re.I), "Manual Curation"),

    (re.compile(r'\bsemi-?pedunculated\b', re.I), "semi-pedunculated"),
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