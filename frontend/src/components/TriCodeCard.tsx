import { useMutation } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Link } from "react-router-dom";
import { proposeReview, type Band, type Explanation, type Relationship } from "../services/api";
import { can } from "../services/auth";
import { useAuth } from "../services/useAuth";
import { ConfidenceBar } from "./ConfidenceBar";
import { EvidencePanel } from "./EvidencePanel";
import { HierarchyBreadcrumb } from "./HierarchyBreadcrumb";
import { InsuranceCheck } from "./InsuranceCheck";
import { MedicationPanel } from "./MedicationPanel";

const BAND_CHIP: Record<Band, string> = {
  high: "bg-brand-700 text-white",
  medium: "bg-saffron-500 text-slate-950",
  low: "bg-red-600 text-white",
};
const BAND_LABEL: Record<Band, string> = {
  high: "High · suggested",
  medium: "Medium · review queue",
  low: "Low · not emitted",
};
const REL_LABEL: Record<Relationship, string> = {
  equivalent: "= equivalent",
  wider: "⊃ wider",
  narrower: "⊂ narrower",
  relatedto: "~ related to",
};

function Column({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0 rounded-xl bg-slate-50 p-3 dark:bg-slate-950/60">
      <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
        {title}
      </p>
      {children}
    </div>
  );
}

function Arrow() {
  return (
    <span aria-hidden="true" className="hidden self-center text-xl text-slate-400 md:block">
      →
    </span>
  );
}

export function TriCodeCard({
  c,
  rank,
  thresholds,
  semanticMethod,
  namasteDevanagari,
}: {
  c: Explanation;
  rank: number;
  thresholds: { high: number; low: number };
  semanticMethod: string;
  namasteDevanagari?: string | null;
}) {
  const [open, setOpen] = useState(rank === 1);
  const [meds, setMeds] = useState(false);
  const user = useAuth();
  const propose = useMutation({
    mutationFn: () =>
      proposeReview({ namaste_code: c.namaste!.code, tm2_code: c.tm2.code, query_text: c.query.text }),
  });
  const panelId = useId();
  const bio = c.biomedicine;
  return (
    <article
      aria-label={`Candidate ${rank}: ${c.tm2.display}`}
      className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900"
    >
      <header className="flex flex-wrap items-center gap-2">
        <h2 className="sr-only">
          Candidate {rank}: {c.tm2.display}
        </h2>
        <span className="font-mono text-xs text-slate-500">#{rank}</span>
        <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${BAND_CHIP[c.band]}`}>
          {BAND_LABEL[c.band]}
        </span>
        <span
          className="rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset ring-slate-300 dark:ring-slate-600"
          title="FHIR R4 ConceptMap equivalence"
        >
          {REL_LABEL[c.relationship]}
        </span>
        {c.evidence.baseline && (
          <span className="rounded-full bg-slate-900 px-2.5 py-0.5 text-xs font-semibold text-white dark:bg-slate-100 dark:text-slate-900">
            {c.evidence.baseline === "approved-mapping" ? "Approved mapping" : "Official crosswalk"}
          </span>
        )}
        <span className="ml-auto">
          <InsuranceCheck bio={bio} />
        </span>
      </header>

      <div className="mt-4 grid items-stretch gap-2 md:grid-cols-[1fr_auto_1fr_auto_1fr]">
        <Column title="NAMASTE">
          {c.namaste ? (
            <>
              <p className="mt-1 font-semibold">{c.namaste.display}</p>
              {namasteDevanagari && (
                <p className="text-slate-600 dark:text-slate-400" lang="sa">
                  {namasteDevanagari}
                </p>
              )}
              <p className="mt-1 font-mono text-xs text-slate-500">{c.namaste.code}</p>
            </>
          ) : (
            <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
              Free-text query “{c.query.text}”
            </p>
          )}
        </Column>
        <Arrow />
        <Column title={`ICD-11 TM2 · ${c.tm2.entity_type}`}>
          <p className="mt-1 font-semibold">{c.tm2.display}</p>
          <p className="mt-1 font-mono text-xs text-slate-500">{c.tm2.code}</p>
        </Column>
        <Arrow />
        <Column title="ICD-11 Biomedicine">
          {bio.code ? (
            <>
              <p className="mt-1 font-semibold">{bio.display}</p>
              <p className="mt-1 font-mono text-xs text-slate-500">{bio.code}</p>
              <p className="mt-1 text-xs text-slate-600 dark:text-slate-400">
                {REL_LABEL[bio.relationship]} · {bio.source} · {bio.status}
              </p>
            </>
          ) : (
            <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">{bio.message}</p>
          )}
        </Column>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-[1fr_auto] md:items-end">
        <div className="space-y-2">
          <HierarchyBreadcrumb path={c.evidence.hierarchy_path} />
          <ConfidenceBar value={c.confidence} band={c.band} thresholds={thresholds} />
        </div>
        <div className="flex flex-wrap gap-2">
          {c.namaste && !c.evidence.baseline && can(user, "propose") && (
            <button
              type="button"
              onClick={() => propose.mutate()}
              disabled={propose.isPending || propose.isSuccess}
              className="rounded-lg px-3 py-2 text-sm font-semibold text-saffron-900 ring-1 ring-inset ring-saffron-300 hover:bg-saffron-50 disabled:opacity-70 dark:text-saffron-100 dark:ring-saffron-700 dark:hover:bg-saffron-900/30"
            >
              {propose.isSuccess ? `Queued for review #${propose.data.id}` : "Send to review"}
            </button>
          )}
          {c.namaste && (c.evidence.baseline || c.band === "high") && can(user, "build_condition") ? (
            <Link
              to={`/condition?namaste=${encodeURIComponent(c.namaste.code)}&tm2=${encodeURIComponent(c.tm2.code)}&basis=${
                c.evidence.baseline ? "approved" : "suggestion"
              }`}
              className="rounded-lg bg-brand-700 px-3 py-2 text-sm font-semibold text-white hover:bg-brand-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-700"
            >
              Build FHIR Condition
            </Link>
          ) : (
            c.band === "medium" && (
              <span className="rounded-lg px-3 py-2 text-sm text-slate-600 ring-1 ring-inset ring-slate-200 dark:text-slate-300 dark:ring-slate-700">
                Needs clinician review
              </span>
            )
          )}
          <button
            type="button"
            aria-expanded={open}
            aria-controls={panelId}
            onClick={() => setOpen((o) => !o)}
            className="rounded-lg px-3 py-2 text-sm font-semibold text-brand-800 ring-1 ring-inset ring-brand-200 hover:bg-brand-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-600 dark:text-brand-200 dark:ring-brand-800 dark:hover:bg-brand-950"
          >
            {open ? "Hide evidence" : "Why this match?"}
          </button>
          <button
            type="button"
            aria-expanded={meds}
            onClick={() => setMeds((m) => !m)}
            className="rounded-lg px-3 py-2 text-sm font-semibold text-slate-700 ring-1 ring-inset ring-slate-300 hover:bg-slate-50 dark:text-slate-200 dark:ring-slate-600 dark:hover:bg-slate-800"
          >
            {meds ? "Hide medications" : "Medications"}
          </button>
        </div>
      </div>

      {meds && <MedicationPanel c={c} />}

      <div id={panelId} hidden={!open}>
        {open && <EvidencePanel c={c} semanticMethod={semanticMethod} />}
      </div>
    </article>
  );
}
