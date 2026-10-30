"""Exports of mappings, review decisions and the audit ledger as JSON, CSV or PDF.

Every file carries a header: what it is, when it was generated, the dataset releases it reflects, the
prototype disclaimer and (for SAMPLE data) the SAMPLE label. Built by a background task (see workers.tasks).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AuditEvent, DatasetRelease, ExportJob, Mapping, ReviewItem

ExportKind = Literal["mappings", "decisions", "audit"]
ExportFormat = Literal["json", "csv", "pdf"]
DISCLAIMER = (
    "Research / decision-support prototype. Not a certified medical device. "
    "Mappings require clinician validation."
)
SAMPLE_LABEL = "SAMPLE — illustrative, not official"
MEDIA_TYPES = {"json": "application/json", "csv": "text/csv; charset=utf-8", "pdf": "application/pdf"}
TITLES = {
    "mappings": "NAMASTE ↔ ICD-11 mappings",
    "decisions": "Clinician review decisions",
    "audit": "Audit ledger (hash-chained)",
}


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


def _mappings(session: Session) -> list[dict[str, Any]]:
    return [
        {
            "id": m.id,
            "source_system": m.source_system,
            "source_code": m.source_code,
            "target_system": m.target_system,
            "target_code": m.target_code,
            "relationship": m.relationship,
            "status": m.status,
            "version": m.version,
            "needs_review": m.needs_review,
            "reviewer": m.reviewer,
            "ranker_version": m.ranker_version,
            "dataset_releases": " ".join(map(str, m.dataset_releases or [])),
            "created_at": _iso(m.created_at),
        }
        for m in session.scalars(select(Mapping).order_by(Mapping.id))
    ]


def _decisions(session: Session) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(ReviewItem).where(ReviewItem.decided_at.is_not(None)).order_by(ReviewItem.id)
    )
    out = []
    for r in rows:
        d = r.decision or {}
        out.append(
            {
                "id": r.id,
                "kind": r.kind,
                "source_code": r.source_code,
                "band_when_queued": r.band,
                "top_confidence": round(r.top_confidence, 4),
                "reason": r.reason,
                "status": r.status,
                "action": d.get("action"),
                "target_code": d.get("target"),
                "override": d.get("override"),
                "relationship": d.get("relationship"),
                "decided_by": r.decided_by,
                "decided_at": _iso(r.decided_at),
                "comment": r.comment,
                "ranker_version": r.ranker_version,
            }
        )
    return out


def _audit(session: Session) -> list[dict[str, Any]]:
    return [
        {
            "id": e.id,
            "ts": e.ts,
            "actor": e.actor,
            "role": e.role,
            "action": e.action,
            "entity_type": e.entity_type,
            "entity_id": e.entity_id,
            "purpose_of_use": e.purpose_of_use,
            "prev_hash": e.prev_hash,
            "hash": e.hash,
        }
        for e in session.scalars(select(AuditEvent).order_by(AuditEvent.id))
    ]


ROWS: dict[str, Callable[[Session], list[dict[str, Any]]]] = {
    "mappings": _mappings,
    "decisions": _decisions,
    "audit": _audit,
}
# Columns shown in the (landscape A4) PDF; JSON / CSV carry every column.
PDF_COLUMNS = {
    "mappings": ["id", "source_code", "target_code", "relationship", "status", "version", "reviewer"],
    "decisions": [
        "id",
        "source_code",
        "band_when_queued",
        "action",
        "target_code",
        "decided_by",
        "decided_at",
    ],
    "audit": ["id", "ts", "actor", "action", "entity_type", "entity_id", "hash"],
}


def header(session: Session, kind: str) -> dict[str, Any]:
    latest = select(func.max(DatasetRelease.id)).group_by(DatasetRelease.source).scalar_subquery()
    rels = sorted(
        session.scalars(select(DatasetRelease).where(DatasetRelease.id.in_(latest))), key=lambda r: r.source
    )
    sample = (not rels) or any(r.is_sample for r in rels)
    return {
        "title": TITLES[kind],
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "dataset_releases": {r.source: r.version for r in rels},
        "disclaimer": DISCLAIMER,
        **({"sample": SAMPLE_LABEL} if sample else {}),
    }


def render(kind: str, fmt: str, head: dict[str, Any], rows: list[dict[str, Any]]) -> bytes:
    if fmt == "json":
        return json.dumps({"header": head, "rows": rows}, ensure_ascii=False, indent=2).encode("utf-8")
    if fmt == "csv":
        buf = io.StringIO()
        for k, v in head.items():  # header as comment lines, then a normal CSV table
            buf.write(f"# {k}: {json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v}\n")
        cols = list(rows[0]) if rows else []
        w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
        return buf.getvalue().encode("utf-8")
    if fmt == "pdf":
        return _pdf(kind, head, rows)
    raise ValueError(fmt)


def _pdf(kind: str, head: dict[str, Any], rows: list[dict[str, Any]]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=f"NamaSetu — {head['title']}",
        author="NamaSetu",
    )
    styles = getSampleStyleSheet()
    small = styles["BodyText"].clone("small", fontSize=8, leading=10)
    # Built-in PDF fonts are Latin-1 only; the arrow and dash in titles are replaced, codes are ASCII.
    safe = lambda s: str(s).replace("↔", "<->").replace("—", "-")  # noqa: E731
    story: list[Any] = [
        Paragraph(f"NamaSetu - {safe(head['title'])}", styles["Title"]),
        Paragraph(f"Generated {head['generated_at']}", small),
        Paragraph(
            "Dataset releases: " + ", ".join(f"{k} {v}" for k, v in head["dataset_releases"].items()), small
        ),
        Paragraph(safe(head["disclaimer"]), small),
    ]
    if "sample" in head:
        story.append(Paragraph(f"<b>{safe(head['sample'])}</b>", small))
    story.append(Spacer(1, 4 * mm))
    cols = PDF_COLUMNS[kind]
    cell = styles["BodyText"].clone("cell", fontSize=7, leading=8.5)
    data = [cols] + [
        [Paragraph(safe("" if r.get(c) is None else r[c])[:200], cell) for c in cols] for r in rows
    ]
    if not rows:
        data.append([Paragraph("No rows.", cell)] + [""] * (len(cols) - 1))
    table = Table(data, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f766e")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(table)

    def footer(canvas: Any, d: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.drawString(12 * mm, 6 * mm, f"{len(rows)} rows - page {d.page} - {safe(DISCLAIMER)}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def build_export(session: Session, job: ExportJob) -> ExportJob:
    rows = ROWS[job.kind](session)
    content = render(job.kind, job.format, header(session, job.kind), rows)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    job.filename = f"namasetu-{job.kind}-{stamp}.{job.format}"
    job.content, job.rows, job.size = content, len(rows), len(content)
    job.sha256 = hashlib.sha256(content).hexdigest()
    job.status = "done"
    return job


def job_view(job: ExportJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "kind": job.kind,
        "format": job.format,
        "status": job.status,
        "created_at": _iso(job.created_at),
        "created_by": job.created_by,
        "filename": job.filename,
        "rows": job.rows,
        "size": job.size,
        "sha256": job.sha256,
        "error": job.error,
        "download_url": f"/export/jobs/{job.id}/download" if job.status == "done" else None,
    }
