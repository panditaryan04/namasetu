"""ORM models. One schema serves both Postgres and SQLite-local mode."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Coding systems held in the concept table.
SYSTEM_NAMASTE = "namaste"
SYSTEM_TM2 = "icd11-tm2"
SYSTEM_BIO = "icd11-bio"

# Dataset release sources.
SOURCE_NAMASTE = "namaste"
SOURCE_ICD11 = "icd11"
SOURCE_SYNONYMS = "synonyms"
SOURCE_GOLD = "gold"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSON, list[Any]: JSON}


class DatasetRelease(Base):
    """One ingest of one source. Every concept and mapping points back to the releases it came from."""

    __tablename__ = "dataset_release"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[str] = mapped_column(String(64))
    checksum: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ingested_by: Mapped[str] = mapped_column(String(64), default="system")
    previous_id: Mapped[int | None] = mapped_column(ForeignKey("dataset_release.id"), nullable=True)
    report: Mapped[dict[str, Any]] = mapped_column(default=dict)
    diff: Mapped[dict[str, Any]] = mapped_column(default=dict)

    @property
    def tag(self) -> str:
        return f"{self.source}@{self.version}"


class Concept(Base):
    """A code from NAMASTE or ICD-11 (TM2 / biomedicine). Unique per (system, code)."""

    __tablename__ = "concept"
    __table_args__ = (UniqueConstraint("system", "code", name="uq_concept_system_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    system: Mapped[str] = mapped_column(String(32), index=True)
    discipline: Mapped[str | None] = mapped_column(String(32), nullable=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    display: Mapped[str] = mapped_column(String(512))
    term_en: Mapped[str | None] = mapped_column(String(512), nullable=True)
    term_iast: Mapped[str | None] = mapped_column(String(512), nullable=True)
    term_devanagari: Mapped[str | None] = mapped_column(String(512), nullable=True)
    definition: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    entity_type: Mapped[str] = mapped_column(String(16))  # disorder | pattern | category
    class_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(default=dict)
    content_hash: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    release_id: Mapped[int] = mapped_column(ForeignKey("dataset_release.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    terms: Mapped[list[ConceptTerm]] = relationship(
        back_populates="concept", cascade="all, delete-orphan", passive_deletes=True
    )


class ConceptTerm(Base):
    """A searchable surface form of a concept (title, index term, synonym in any script)."""

    __tablename__ = "concept_term"
    __table_args__ = (Index("ix_concept_term_concept_source", "concept_id", "source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    concept_id: Mapped[int] = mapped_column(ForeignKey("concept.id", ondelete="CASCADE"))
    term: Mapped[str] = mapped_column(String(512))
    # primary | english | iast | devanagari | hinglish | index_term
    kind: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(32))  # dataset source that supplied it
    release_id: Mapped[int] = mapped_column(ForeignKey("dataset_release.id"))

    concept: Mapped[Concept] = relationship(back_populates="terms")


class ConceptLink(Base):
    """TM2 -> biomedicine link. Only WHO-published linkages or clinician approvals, never inferred."""

    __tablename__ = "concept_link"
    __table_args__ = (UniqueConstraint("source_code", "target_code", "origin", name="uq_concept_link"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_system: Mapped[str] = mapped_column(String(32))
    source_code: Mapped[str] = mapped_column(String(64), index=True)
    target_system: Mapped[str] = mapped_column(String(32))
    target_code: Mapped[str] = mapped_column(String(64))
    relationship: Mapped[str] = mapped_column(String(16), default="relatedto")
    origin: Mapped[str] = mapped_column(String(16))  # who-index | clinician
    status: Mapped[str] = mapped_column(String(16))  # approved | pending
    release_id: Mapped[int | None] = mapped_column(ForeignKey("dataset_release.id"), nullable=True)


class GoldMapping(Base):
    """Held-out reference mapping NAMASTE -> TM2, with a fixed train/test split."""

    __tablename__ = "gold_mapping"
    __table_args__ = (UniqueConstraint("namaste_code", "tm2_code", name="uq_gold_pair"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    namaste_code: Mapped[str] = mapped_column(String(64), index=True)
    tm2_code: Mapped[str] = mapped_column(String(64))
    relationship: Mapped[str] = mapped_column(String(16))
    split: Mapped[str] = mapped_column(String(8))  # train | test
    release_id: Mapped[int] = mapped_column(ForeignKey("dataset_release.id"))


class Mapping(Base):
    """Approved Mapping Store row (clinician workflow is added in Milestone 5)."""

    __tablename__ = "mapping"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_system: Mapped[str] = mapped_column(String(32))
    source_code: Mapped[str] = mapped_column(String(64), index=True)
    target_system: Mapped[str] = mapped_column(String(32))
    target_code: Mapped[str] = mapped_column(String(64), index=True)
    relationship: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="approved")  # approved | rejected | superseded
    version: Mapped[int] = mapped_column(Integer, default=1)
    reviewer: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ranker_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dataset_releases: Mapped[list[Any]] = mapped_column(default=list)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    """Append-only, hash-chained ledger. `ts` is an ISO-8601 string so the hashed value round-trips."""

    __tablename__ = "audit_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[str] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(32), index=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(128))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    purpose_of_use: Mapped[str | None] = mapped_column(String(32), nullable=True)
    request_id: Mapped[str] = mapped_column(String(64))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class FhirPublication(Base):
    """A published, immutable version of a FHIR terminology resource (e.g. a ConceptMap)."""

    __tablename__ = "fhir_publication"
    __table_args__ = (UniqueConstraint("resource_type", "logical_id", "version", name="uq_fhir_publication"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    resource_type: Mapped[str] = mapped_column(String(32))
    logical_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64))
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_by: Mapped[str] = mapped_column(String(64), default="system")


class FhirProvenance(Base):
    """Stored FHIR Provenance resources (mapping approvals, ConceptMap publications, Condition builds)."""

    __tablename__ = "fhir_provenance"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    target: Mapped[str] = mapped_column(String(128), index=True)  # e.g. "Condition/abc", "Mapping/12"
    recorded: Mapped[str] = mapped_column(String(40))
    content: Mapped[dict[str, Any]] = mapped_column(JSON)


class User(Base):
    """Local account (bcrypt). Roles: admin | clinician | coder | viewer."""

    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16))
    password_hash: Mapped[str] = mapped_column(String(128))
    abha_address: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewItem(Base):
    """A mapping awaiting a clinician decision (medium band, high-band confirmation, or coder proposal)."""

    __tablename__ = "review_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="mapping")  # mapping | bio_link
    status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True
    )  # pending|approved|rejected
    source_system: Mapped[str] = mapped_column(String(32))
    source_code: Mapped[str] = mapped_column(String(64), index=True)
    discipline: Mapped[str | None] = mapped_column(String(32), nullable=True)
    query_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    band: Mapped[str] = mapped_column(String(8), index=True)  # band of the top candidate when queued
    top_confidence: Mapped[float] = mapped_column(default=0.0)
    proposed_target: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(String(32))  # medium-band | high-band-confirm | proposed
    candidates: Mapped[list[Any]] = mapped_column(default=list)  # Explanation snapshots
    dataset_releases: Mapped[list[Any]] = mapped_column(default=list)
    ranker_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    mapping_id: Mapped[int | None] = mapped_column(ForeignKey("mapping.id"), nullable=True)


class AppSetting(Base):
    """Runtime settings changed from the Admin UI (e.g. confidence thresholds). Versioned per key."""

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# ---------------------------------------------------------------- medications (reference only)
# Deliberately no dose / frequency / duration / regimen columns anywhere: NamaSetu does not prescribe.

SOURCE_MEDICATIONS = "medications"


class Ingredient(Base):
    """A salt (biomedical active ingredient), herb, processed mineral or other printed ingredient."""

    __tablename__ = "ingredient"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True)  # e.g. "salt:paracetamol", "herb:haridra"
    kind: Mapped[str] = mapped_column(String(16))  # salt | herb | mineral | other
    canonical_name: Mapped[str] = mapped_column(String(128))
    botanical_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    iast: Mapped[str | None] = mapped_column(String(128), nullable=True)
    devanagari: Mapped[str | None] = mapped_column(String(128), nullable=True)
    synonyms: Mapped[list[Any]] = mapped_column(default=list)  # [{"term": ..., "kind": ...}]
    metal_mineral: Mapped[bool] = mapped_column(Boolean, default=False)
    release_id: Mapped[int] = mapped_column(ForeignKey("dataset_release.id"))


class Medication(Base):
    __tablename__ = "medication"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(24))  # ayush_formulation | ayush_single | biomedical
    name: Mapped[str] = mapped_column(String(256))
    name_iast: Mapped[str | None] = mapped_column(String(256), nullable=True)
    name_devanagari: Mapped[str | None] = mapped_column(String(256), nullable=True)
    discipline: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dosage_form: Mapped[str] = mapped_column(String(64))
    route: Mapped[str | None] = mapped_column(String(32), nullable=True)
    therapeutic_class: Mapped[str | None] = mapped_column(String(128), nullable=True)
    schedule: Mapped[str | None] = mapped_column(
        String(8), nullable=True
    )  # H | H1 | X | OTC (only if source states)
    nlem: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    source_title: Mapped[str] = mapped_column(String(256))
    source_edition: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_page: Mapped[str | None] = mapped_column(String(64), nullable=True)
    caution_note: Mapped[str | None] = mapped_column(Text, nullable=True)  # only as printed in the source
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    release_id: Mapped[int] = mapped_column(ForeignKey("dataset_release.id"))

    composition: Mapped[list[MedicationIngredient]] = relationship(
        back_populates="medication", cascade="all, delete-orphan", order_by="MedicationIngredient.position"
    )


class MedicationIngredient(Base):
    __tablename__ = "medication_ingredient"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    medication_id: Mapped[int] = mapped_column(ForeignKey("medication.id", ondelete="CASCADE"), index=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredient.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    printed_name: Mapped[str] = mapped_column(String(128))  # exactly as in the source
    botanical_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    part_used: Mapped[str | None] = mapped_column(String(64), nullable=True)
    proportion: Mapped[str | None] = mapped_column(String(32), nullable=True)  # Ayush, as printed ("1 part")
    strength_text: Mapped[str | None] = mapped_column(String(32), nullable=True)  # biomedical, as printed
    strength_value: Mapped[str | None] = mapped_column(String(32), nullable=True)  # exact decimal as text
    strength_unit: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # mg | mcg | g | IU | %w/w ...

    medication: Mapped[Medication] = relationship(back_populates="composition")
    ingredient: Mapped[Ingredient] = relationship()


class Brand(Base):
    __tablename__ = "brand"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand_name: Mapped[str] = mapped_column(String(128))
    key: Mapped[str] = mapped_column(String(128), index=True)
    medication_id: Mapped[int] = mapped_column(ForeignKey("medication.id", ondelete="CASCADE"))
    source_title: Mapped[str] = mapped_column(String(256))
    release_id: Mapped[int] = mapped_column(ForeignKey("dataset_release.id"))


class MedicationLink(Base):
    """Condition -> reference medication, from a cited guideline or a clinician approval. Never inferred."""

    __tablename__ = "medication_link"
    __table_args__ = (
        UniqueConstraint("condition_system", "condition_code", "medication_id", name="uq_medication_link"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    condition_system: Mapped[str] = mapped_column(String(32))
    condition_code: Mapped[str] = mapped_column(String(64), index=True)
    medication_id: Mapped[int] = mapped_column(ForeignKey("medication.id", ondelete="CASCADE"))
    source_title: Mapped[str] = mapped_column(String(256))
    source_edition: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_page: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), index=True)  # approved | pending | rejected
    reviewer: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    release_id: Mapped[int] = mapped_column(ForeignKey("dataset_release.id"))

    medication: Mapped[Medication] = relationship()


SOURCE_INTERACTIONS = "interactions"


class Constituent(Base):
    """Key phytochemical / mineral constituent of an Ayush ingredient (cited; never inferred)."""

    __tablename__ = "constituent"
    __table_args__ = (UniqueConstraint("ingredient_id", "key", name="uq_constituent"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredient.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(128), index=True)  # "constituent:curcumin"
    name: Mapped[str] = mapped_column(String(128))
    constituent_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_title: Mapped[str] = mapped_column(String(256))
    source_edition: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_page: Mapped[str | None] = mapped_column(String(64), nullable=True)
    release_id: Mapped[int] = mapped_column(ForeignKey("dataset_release.id"))


class InteractionRule(Base):
    """One cited interaction between two agents (salt, herb or constituent). Never inferred."""

    __tablename__ = "interaction_rule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_a_type: Mapped[str] = mapped_column(String(16))
    agent_a_key: Mapped[str] = mapped_column(String(128), index=True)
    agent_a_name: Mapped[str] = mapped_column(String(128))
    agent_b_type: Mapped[str] = mapped_column(String(16))
    agent_b_key: Mapped[str] = mapped_column(String(128), index=True)
    agent_b_name: Mapped[str] = mapped_column(String(128))
    severity: Mapped[str] = mapped_column(String(16))  # major | moderate | minor
    evidence_level: Mapped[str] = mapped_column(
        String(16)
    )  # established | probable | theoretical | case-report
    mechanism_summary: Mapped[str] = mapped_column(Text)
    management_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    citation: Mapped[str] = mapped_column(String(256))
    source_version: Mapped[str] = mapped_column(String(64))
    release_id: Mapped[int] = mapped_column(ForeignKey("dataset_release.id"))


class Patient(Base):
    """Patients known to the sandbox. Demo data is SYNTHETIC only (fake names, TEST ABHA-like identifiers)."""

    __tablename__ = "patient"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display: Mapped[str] = mapped_column(String(128))
    abha_number: Mapped[str] = mapped_column(String(32), unique=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PatientMedication(Base):
    """A medication from a prior encounter (read-only history; NamaSetu never prescribes)."""

    __tablename__ = "patient_medication"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.id", ondelete="CASCADE"), index=True)
    encounter_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recorded_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16))  # active | stopped | unknown
    source_system: Mapped[str] = mapped_column(String(64))
    source_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    text: Mapped[str] = mapped_column(String(256))  # as received; never written to logs
    resolution_kind: Mapped[str] = mapped_column(String(16))  # brand|product|formulation|salt|herb|unresolved
    medication_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ingredient_ids: Mapped[list[Any]] = mapped_column(default=list)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    imported_by: Mapped[str] = mapped_column(String(64))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InteractionAlert(Base):
    __tablename__ = "interaction_alert"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient.id", ondelete="CASCADE"), index=True)
    candidate_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    alert_key: Mapped[str] = mapped_column(String(256), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # duplicate | herb-drug | drug-drug
    severity: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | acknowledged | overridden
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvalRun(Base):
    """One evaluation run: results are stored with the dataset releases and model version they measured."""

    __tablename__ = "eval_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str] = mapped_column(String(64))
    dataset_releases: Mapped[list[Any]] = mapped_column(default=list)
    model_version: Mapped[str] = mapped_column(String(32))
    duration_s: Mapped[float] = mapped_column(default=0.0)
    safety_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON)
    results: Mapped[dict[str, Any]] = mapped_column(JSON)


class ExportJob(Base):
    """An export file (mappings / review decisions / audit ledger) built by a background task.

    Stored in the database so the API and the worker do not need a shared volume.
    """

    __tablename__ = "export_job"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))  # mappings | decisions | audit
    format: Mapped[str] = mapped_column(String(8))  # json | csv | pdf
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | done | failed
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rows: Mapped[int] = mapped_column(Integer, default=0)
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
