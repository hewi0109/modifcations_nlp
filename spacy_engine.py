


# ─────────────────────────────────────────────────────────────────
# OPTIONAL SPACY NLP ENGINE
# ─────────────────────────────────────────────────────────────────


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
        from textClean import clean_clinical_text
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


