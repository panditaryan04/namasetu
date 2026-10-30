# Matching pipeline

How NamaSetu turns a query (any script) into ranked, explained, gated NAMASTE → ICD-11 TM2 (→ biomedicine)
candidates. Code: `backend/app/engine/`. All bundled data is SAMPLE — illustrative, not official.

## 0. Resolve the query

The query is normalised (below) and matched against every NAMASTE surface form: title, IAST, Devanagari,
English gloss and synonyms (English / Hinglish). It **resolves** to a NAMASTE concept only on an exact
normalised match or a near-identical spelling, such as a shared consonant skeleton (`amlpitt` → Amlapitta). A
prefix ("amla") or a bare root term ("pitta") does not resolve. Those return suggestions, and the query is
then matched as free text.

## 1. Normalisation and transliteration (`normalizer.py`, `transliterator.py`)

| Step | Example |
|---|---|
| NFC, lowercase, punctuation stripped (Devanagari vowel signs kept) | `Āmavāta,` → `āmavāta` |
| Devanagari / Harvard-Kyoto → IAST (`indic-transliteration`) | `आमवात` → `āmavāta`, `AmavAta` → `āmavāta` |
| Diacritics → ASCII; anusvara → n/m by place | `saṃdhi` → `sandhi`, `saṃpūrṇa` → `sampurna` |
| Phonetic folds: `aa→a`, `ee/ii→i`, `oo/uu→u`, `sh/ś/ṣ→s`, `w→v`, `f→ph`, `chh→ch`, doubled consonants | `aamavaata` → `amavata` |
| Compact key (space/hyphen-insensitive) | `Amla-pitta` → `amlapita` |
| Consonant skeleton (Hinglish schwa-dropping) | `aamvaat`, `Amavata` → `amvt` |
| Root splitting: greedy longest match over the root dictionary, with vowel-sandhi backtracking | `kashtartava` → `kasta + artava` |

Roots only count when a word *starts* with a root and roots cover ≥ 60% of it. This stops "ama" in "tamak"
from counting as a root.

## 2. Features per TM2 candidate (`matcher.py`, `semantic.py`)

| Feature | Meaning |
|---|---|
| `exact` | A source surface equals a target title / index term after normalisation |
| `lexical` | Best RapidFuzz blend (token-set / token-sort / partial / compact ratio) over all surface pairs |
| `ngram` | Max TF-IDF cosine over character 3–4-grams (`char_wb`) |
| `transliteration` | 1.0 cross-script identity · 0.85 same-script phonetic folding · 0.75 consonant skeleton |
| `semantic` | `SEMANTIC_MODE=off`: IDF-weighted overlap of glosses, titles and definitions. `full` / `onnx-int8`: multilingual embedding cosine (FAISS `IndexFlatIP`) |
| `hierarchy` | NAMASTE ancestors vs TM2 ancestors (identity or weighted overlap) |
| `root_overlap` | Jaccard of Sanskrit root terms |
| `ancestor_match` | The candidate names an ancestor of the source (e.g. Vataja Jvara → "Jvara") |

## 3. Ranker and gate (`ranker.py`, `gate.py`)

- **Ranker:** logistic regression over the features above, trained **only on the gold TRAIN split** with the Stage-0 baseline disabled.
- **Regularisation:** chosen by grouped 5-fold CV. Only models with all weights ≥ 0 are eligible, so more evidence never lowers confidence.
- **DISORDER/PATTERN rule:** an explicit penalty, not a learned weight. A mismatched entity type multiplies confidence by 0.6.
- **Thresholds:** calibrated on the CV predictions.
  - **T_HIGH:** the lowest value (≥ 0.5) at which top-1 precision is ≥ 95%, measured on real queries plus "absent-answer" queries (each training query with its correct target removed).
  - **T_LOW:** keeps ≥ 95% of correct top-1 suggestions.
- **Artifact:** `backend/data/artifacts/ranker.json` holds the weights, thresholds and the reliability table, and is served at `GET /eval/ranker`. `T_HIGH` / `T_LOW` env vars override the thresholds.

| Band | Rule | Outcome |
|---|---|---|
| high | ≥ T_HIGH | suggested; one-click confirmation |
| medium | T_LOW ≤ x < T_HIGH | clinician review queue |
| low | < T_LOW | **never returned**: "No safe automated match found" |

**Stage 0 (deterministic baseline):** approved mappings plus the official crosswalk. With sample data,
`STAGE0_GOLD_SPLIT=train` uses only the train split as the "official crosswalk", so the test split stays held out.
A Stage-0 hit gets confidence 1.0 and the stage `exact`.

## 4. Relationship rules (`relationship.py`; FHIR R4 ConceptMap equivalence)

1. A Stage-0 baseline uses its recorded relationship.
2. The best candidate that *names* the source (identity, with agreeing entity type) is the anchor: `equivalent`.
3. An ancestor of the anchor is `wider`; a descendant of the anchor is `narrower`.
4. With no anchor, a candidate that names an ancestor of the NAMASTE source is `wider`, and one that names a descendant is `narrower`.
5. Anything else is `relatedto`.

Biomedical links come only from WHO-index linkages or clinician approvals. They are always `relatedto`, never
inferred, and when none exists the response says "No biomedical link (clinician may add)".

## 5. Safety

- **Code validation:** every emitted code (NAMASTE, TM2, biomedicine) is checked against the loaded code tables (`MappingEngine.validate`). A failure raises an error rather than returning the code.
- **Reasoning:** `reasoning` is a template filled only from evidence fields. No generative model writes it.
- **SAMPLE codes:** these are published under a `…/fhir/CodeSystem/icd11-mms-sample` system URI, never under WHO's official URI.
