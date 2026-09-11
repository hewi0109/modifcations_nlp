import re


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
        r'no\s+significant\s+histological\s+abnormality(?:\s+identified)?|'
        r'no\s+histolog(?:ical)?\s+(?:evidence\s+of\s+a\s+)?polyp|'
        r'benign\s+prominent\s+mucosal\s+fold|unremarkable\s+(?:colonic\s+)?mucosa|'
        r'no\s+serrated\s+polyp.*no.*dysplas|degenerate\s+fragment|'
        r'within\s+normal\s+(?:histological\s+)?limits|normal\s+histolog|'
        r'normal\s+colonic\s+mucosa|'
        r'colonic\s+mucosa\s+within\s+normal\s+limits|'
        r'mild\s+architectural\s+distortion|'
        r'biopsy\s*:\s*normal\b',
        re.I), "Normal tissue"),
        ]

def is_bare_normal_statement(text: str) -> bool:
    """True only when the text, once stripped of whitespace/trailing period,
    is literally just the word 'normal' -- not merely containing it
    somewhere, e.g. 'Normal result:' from an ancillary IHC block."""
    return text.strip().rstrip('.').strip().lower() == "normal"

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

COLITIS_FAMILY_PATTERNS = [
    (re.compile(r"crohn'?s\s+(?:disease|colitis)|crohn'?s\b", re.I), "Crohn's colitis"),
    (re.compile(r'\bulcerative\s+colitis\b', re.I), "Ulcerative colitis"),
    (re.compile(r'\bindeterminate\s+colitis\b', re.I), "Indeterminate colitis"),
    (re.compile(r'\bquiescent\s+colitis\b', re.I), "Quiescent colitis"),
    (re.compile(r'\bpouchitis\b', re.I), "Pouchitis"),
    (re.compile(r'\bproctitis\b', re.I), "Proctitis"),
    (re.compile(r'\bcolitis\b', re.I), "Colitis"),
]