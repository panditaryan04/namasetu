# Standards alignment

What NamaSetu conforms to today, how that is checked, and what is **not** certified. SAMPLE data is illustrative, not official.

## HL7 FHIR R4 (4.0.1)

| Check | How |
|---|---|
| Structure and cardinality of every response | `fhir.resources` models (R4B package, wire-compatible with R4 for the resources below), run on **every** outgoing `/fhir` response; an invalid resource is never sent (HTTP 500 OperationOutcome instead) |
| Required bindings not enforced by the models | explicit checks in `backend/app/fhir/validator.py` (ConceptMap equivalence, publication status, Condition clinical/verification status, Bundle type, issue severity) |
| Authoritative R4 validation | official **HL7 FHIR Validator CLI**, `-version 4.0.1`, over `docs/fhir-examples/` (`.github/workflows/fhir-validate.yml`) |

**Latest validator run** (regenerated examples, HL7 Validator, FHIR 4.0.1, `ndhm.in#6.5.0` loaded, `-tx n/a`):
**23/23 files, 0 errors.** The remaining warnings are:

- **Designation languages:** BCP-47 tags (`sa-Deva`, `sa-Latn`, `hi-Latn`, `en`) can't be checked without a terminology server.
- **Audit subtype CodeSystem:** our `namasetu-audit-action` CodeSystem isn't published.
- **TM2 → biomedicine ConceptMap:** with SAMPLE data both sides share one stand-in system URI.
- **Client-supplied Bundles:** these have no narrative.

> Note: `fhir.resources` 8.x (Pydantic v2) ships R5, R4B and STU3 models. Pure-R4 models exist only in 6.x
> (Pydantic v1). R4B models are used in-process for that reason; the HL7 Validator run is the R4 authority.

### Resources and operations

| Endpoint | Output |
|---|---|
| `GET /fhir/metadata` | CapabilityStatement |
| `GET /fhir/CodeSystem/namaste` | NAMASTE CodeSystem, `content=complete`, `hierarchyMeaning=is-a`, designations in IAST / Devanagari / English / Hinglish, properties `parent`, `entity-type`, `discipline` |
| `GET /fhir/CodeSystem/icd11-mms-sample` | SAMPLE stand-in for ICD-11 MMS (only when SAMPLE data is loaded) |
| `GET /fhir/ValueSet[/{id}]` | `namaste-ayurveda`, `icd11-tm2` (active); `namaste-siddha`, `namaste-unani` (draft placeholders) |
| `GET /fhir/CodeSystem/$lookup`, `$validate-code` (GET + POST Parameters) | Parameters |
| `GET /fhir/ValueSet/$expand?filter=`, `ValueSet/$validate-code` | ValueSet with expansion. The NAMASTE filter uses the multi-script autocomplete |
| `GET/POST /fhir/ConceptMap/$translate` | Parameters (`result`, `match.equivalence`, `match.concept`, `match.source`) |
| `GET /fhir/ConceptMap/{id}`, `/_history` | `namaste-to-tm2`, `tm2-to-biomedicine`, versioned |
| `POST /fhir/Condition/$build` | tri-coded Condition |
| `POST /fhir/Bundle` | Parameters { `outcome`: OperationOutcome, `return`: enriched Bundle } |
| `POST /fhir/$validate` | OperationOutcome |
| `GET /fhir/AuditEvent`, `/fhir/Provenance` | searchset Bundles |

### System URIs

| System | URI |
|---|---|
| NAMASTE | `{PUBLIC_BASE_URL}/fhir/CodeSystem/namaste` |
| ICD-11 MMS (real data) | `http://id.who.int/icd/release/11/mms` |
| ICD-11 (SAMPLE data) | `{PUBLIC_BASE_URL}/fhir/CodeSystem/icd11-mms-sample` |

SAMPLE codes are never published under WHO's URI.

## ConceptMaps, mapping safety and versioning

- **Contents:** ConceptMaps are built **only** from the Approved Mapping Store (NAMASTE → TM2) and approved TM2 → biomedicine links. Machine suggestions never appear.
- **Official crosswalk:** imported as approved rows (reviewer `official-crosswalk`), each with a Provenance. With SAMPLE data only the **train** split is imported, so the held-out test split stays out.
- **Versioning:** every content change publishes a new immutable version (`ConceptMap.version`, `/_history`), with a Provenance and a hash-chained ledger entry.
- **Equivalence codes:** R4 `ConceptMap.group.element.target.equivalence`. Biomedical links are always `relatedto`.

## Tri-coded Condition

- **Codings:** `code.coding` = NAMASTE + ICD-11 TM2, plus ICD-11 biomedicine only when an **approved** link exists. Pending links are never used.
- **TM2 source:** the TM2 coding must come from an approved mapping, or be a HIGH-band suggestion that the clinician explicitly confirms (`confirm_suggestion=true`). Medium and low suggestions are refused (422 OperationOutcome) and belong in the review queue.
- **Subject:** `Patient/{id}` with an ABHA identifier (`https://healthid.ndhm.gov.in`). Only TEST / synthetic values are used in the sandbox.
- **Audit trail:** every build writes a Provenance and an AuditEvent carrying its purpose-of-use (v3-ActReason).

## ABDM / NRCeS profiles: the honest position

The validator run loaded the official NRCeS IG package **`ndhm.in#6.5.0`**. Its Condition profile
(`https://nrces.in/ndhm/fhir/r4/StructureDefinition/Condition`) defines **closed** slicing on
`Condition.code.coding` (discriminator `system`), allowing only **ICD-10** (`http://hl7.org/fhir/sid/icd-10`) and
**SNOMED CT** (`http://snomed.info/sct`).

A Condition coded with NAMASTE or ICD-11 therefore **cannot conform** to that profile version. With the profile
claimed, the validator reported 3 errors ("does not match any known slice … slicing is CLOSED"). NamaSetu therefore:

- does **not** claim the NRCeS Condition profile in `meta.profile`, and emits a valid base R4 Condition;
- would need either an NRCeS profile revision that opens the slicing (or adds NAMASTE / ICD-11 slices), or a
  SNOMED CT / ICD-10 coding alongside, before ABDM conformance can be claimed. SNOMED CT India is a pluggable
  loader planned with the medication milestone.

Other NRCeS profiles (Patient, Encounter, document Bundles) are not claimed yet.

## Medications (reference only)

- **Never a prescription:** NamaSetu never creates `MedicationRequest`. No dose, frequency, duration or regimen
  field exists in any API schema (a test scans the OpenAPI document), and every medication response carries
  "Reference only — not a prescription. Consult a qualified clinician."
- **Medication:** composition with `ingredient.itemCodeableConcept` and `isActive`.
  - `strength` is a Ratio **only when the source states it**: UCUM `mg`, `ug`, `g`, `[IU]` per unit of dosage
    form; `% w/w` as g per 100 g; Ayush proportions as parts of the total.
- **MedicationKnowledge (R4):** `code`, `doseForm`, `ingredient`, `synonym` (brand examples), `intendedRoute`.
  - Approved condition links appear under `administrationGuidelines.indicationCodeableConcept`, with **no**
    `dosage` element.
  - Each approved link has a Provenance citing its source (`/fhir/Provenance?target=MedicationLink/<id>`).
- **Cited and reviewed:**
  - Every medication, brand row and condition link must cite a source (title / edition / page), or the row is
    rejected at ingest.
  - Pending links are visible only in the review tab; approved SAMPLE links are labelled "illustrative sample".
  - Brands are resolved by exact match or a unique ≤1–2-edit typo, never guessed ("Brand not found").
- **Validation:** the HL7 Validator (FHIR 4.0.1, `ndhm.in` 6.5.0) reports 0 errors on the 21 example files,
  including Medication, MedicationKnowledge, a MedicationKnowledge search Bundle, a DetectedIssue and a
  MedicationStatement timeline Bundle.
- **Interaction alerts as DetectedIssue:**
  - `code` from v3-ActCode: `DRG` drug interaction, or `DUPTHPY` duplicate therapy.
  - `severity` high / moderate / low; `implicated` Medication references; `evidence.detail` carries the citation
    and dataset version.
  - Clinician acknowledge / override is recorded under `mitigation`.
- **Medication history:** exported as MedicationStatements with the resolved salts and herbs in a contained
  Medication.
- **Not yet done:** SNOMED CT India codes are not loaded (pluggable loader, off).

## Not certified / not yet done

- **No certification:** this is not a certified medical device, and NamaSetu holds no ABDM certification.
- **ABHA:** mock tokens and TEST identifiers only; the real ABDM gateway isn't integrated.
- **ICD-11 live client:** built and tested against a simulated WHO API only (no keys yet). The TM2 module title match and the disorder/pattern rule are configurable assumptions.
- **SNOMED CT / ICD-10:** not coded.
- **Biomedical linkages in LIVE mode:** WHO linkage fields aren't auto-imported; links come from the SAMPLE WHO-index data or from clinicians.
- **India EHR Standards 2016:** alignment is not yet assessed item by item.
