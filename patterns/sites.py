
import re
from spacy_engine import SPACY_ENGINE
from vocab import FINDING_SITE_VOCAB
from typing import Optional
from textClean import normalise

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
    (re.compile(r'rectal\s+(?:colon["\']?\s+)?(?:polyp|biopsy|mass|mucosa)|\brectum\b|\brectal\b', re.I), "Rectum"),
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
    (re.compile(r'terminal\s+ile(?:um|al)', re.I), "Terminal Ileum"),
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


