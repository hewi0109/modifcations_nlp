## Vocab dropdowns

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
    "Complete", "Incomplete", "Not clear", "Not reported", "Not retrieved", "N/A"
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
    "Crohn's colitis": 4,
    "Ulcerative colitis": 4,
    "Indeterminate colitis": 4,
    "Quiescent colitis": 2,
    "Colitis": 4,
    "Pouchitis": 3,
    "Proctitis": 3,
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
