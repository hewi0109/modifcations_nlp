import re
from dataclasses import dataclass, field
from vocab import FINDING_SITE_VOCAB, FINDING_MORPHOLOGY_VOCAB, INTERVENTION_COMPLETENESS_VOCAB, INTERVENTION_VOCAB
from patterns.sites import SITE_RAW_PATTERNS, extract_site_raw, normalise_compound_site_text, extract_site_count_pairs, dedupe_sites_preferring_specific, parse_polyp_count
from textClean import clean_clinical_text, normalise
from patterns.morphology import extract_morphology_raw
from extract_procedure import extract_completeness_raw, extract_intervention_raw, extract_size


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
        r'\s+(?=(?:A|An)\s+(?:small|large|tiny)\s+\(?\d+(?:\s*(?:to|-)\s*\d+)?\s*mm\)?\s+(?:(?:inflam+ed|sessile|flat|pedunculated|semi-?sessile|semi-?pedunculated)\s+){0,4}polyp\b)',
        '\n',
        findings_text,
        flags=re.I
    )

    # Insert newline before each new finding sentence
    findings_text = re.sub(
        r'(?<=\.)\s+(?='
        r'(?:A|An)\s+\d+(?:\s*(?:to|-)\s*\d+)?\s*mm\s+(?:(?:inflam+ed|sessile|flat|pedunculated|semi-?sessile|semi-?pedunculated)\s+){0,4}(?:polyp|mass|tumou?r)|'
        r'(?:A|An)\s+(?:small|large|tiny|minute)\s+\(?\d+(?:\s*(?:to|-)\s*\d+)?\s*mm\)?\s+(?:polyp|mass|tumou?r)|'
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
                raw_text=block,
            ))

    return findings