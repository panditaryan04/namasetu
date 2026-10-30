import { useQueries, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { InteractionAlert } from "../components/InteractionAlert";
import { REFERENCE_ONLY } from "../components/MedicationCard";
import {
  autocomplete,
  mapTerm,
  medication,
  medicationLinks,
  medicationsByCondition,
  patientInteractions,
  patients,
  patientTimeline,
  type Alert,
  type Explanation,
  type MedicationView,
  type Relationship,
  type TimelineItem,
} from "../services/api";
import { setSelectedPatient, useSelectedPatient } from "../services/patientContext";
import { useAuth } from "../services/useAuth";

/* ------------------------------------------------------------------ shared bits */

type Dx = { code: string; display: string };

const REL: Record<Relationship, string> = {
  equivalent: "Equivalent",
  wider: "Broader",
  narrower: "Narrower",
  relatedto: "Related to (not an exact equivalent)",
};
const SEVERITY_RANK: Record<Alert["severity"], number> = { major: 0, moderate: 1, minor: 2 };
const CHIP: Record<Alert["severity"], string> = {
  major: "bg-red-50 text-red-800 ring-red-300 dark:bg-red-950 dark:text-red-200 dark:ring-red-800",
  moderate:
    "bg-saffron-50 text-saffron-900 ring-saffron-300 dark:bg-saffron-900/30 dark:text-saffron-100 dark:ring-saffron-700",
  minor:
    "bg-slate-100 text-slate-800 ring-slate-300 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-600",
};
// Frequency abbreviations as written in Indian prescriptions; shown exactly as recorded, never suggested.
const FREQUENCY: Record<string, string> = {
  OD: "OD (once daily)",
  BD: "BD (twice daily)",
  BID: "BID (twice daily)",
  TDS: "TDS (three times daily)",
  TID: "TID (three times daily)",
  QID: "QID (four times daily)",
  HS: "HS (at bedtime)",
  SOS: "SOS (when needed)",
};
// Synthetic patients and SAMPLE codes that exist in the seeded data.
const DEMOS = [
  {
    label: "Diabetic patient · Madhumeha",
    patient: "test-patient-01",
    dx: { code: "SAMPLE-AYU-0044", display: "Madhumeha" },
  },
  {
    label: "Arthritis patient · Amavata",
    patient: "test-patient-03",
    dx: { code: "SAMPLE-AYU-0001", display: "Amavata" },
  },
];

const card = "rounded-2xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900";
const eyebrow = "text-xs font-bold uppercase tracking-wide text-slate-600 dark:text-slate-400";
const muted = "text-sm text-slate-600 dark:text-slate-400";

function regimen(item: TimelineItem, med?: MedicationView): string {
  const strength = med?.composition
    .map((c) => c.strength)
    .filter(Boolean)
    .join(" + ");
  const token = item.text.toUpperCase().match(/\b(OD|BD|BID|TDS|TID|QID|HS|SOS)\b/)?.[1];
  const when = token ? FREQUENCY[token] : "frequency not recorded";
  const food = /before food|after food/i.exec(item.text)?.[0];
  return [strength || null, when, food].filter(Boolean).join(" · ");
}

const domain = (med?: MedicationView) =>
  !med ? "Not resolved" : med.kind === "biomedical" ? "Allopathy" : "AYUSH";

/* ------------------------------------------------------------------ container 1: medicines */

function MedicinesTable({
  items,
  meds,
  indications,
  alerts,
  screening,
}: {
  items: TimelineItem[];
  meds: Map<string, MedicationView>;
  indications: Map<string, string[]>;
  alerts: Alert[];
  screening: boolean;
}) {
  return (
    <div className="mt-3 overflow-x-auto">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">Current medicines and their active chemical salts</caption>
        <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-600 dark:bg-slate-800/60 dark:text-slate-400">
          <tr>
            <th className="px-3 py-2">Brand / formulation</th>
            <th className="px-3 py-2">Active chemical salt</th>
            <th className="px-3 py-2">Regimen</th>
            <th className="px-3 py-2">Domain</th>
            <th className="px-3 py-2">Clinical indication</th>
            <th className="px-3 py-2">Interaction screen</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
          {items.map((it) => {
            const med = it.resolution.medication_code ? meds.get(it.resolution.medication_code) : undefined;
            const indication = it.resolution.medication_code
              ? indications.get(it.resolution.medication_code)
              : undefined;
            const hits = alerts
              .filter((a) => a.other_medication.includes(it.text))
              .sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity]);
            return (
              <tr key={it.id}>
                <td className="px-3 py-2.5">
                  <span className="font-medium">{it.text}</span>
                  {med && (
                    <span className="block text-xs text-slate-600 dark:text-slate-400">{med.name}</span>
                  )}
                </td>
                <td className="px-3 py-2.5">
                  {it.agents.length ? (
                    it.agents.map((a) => (
                      <span
                        key={a.name}
                        className="mb-1 mr-1 inline-block rounded-full px-2 py-0.5 font-mono text-xs text-emerald-900 ring-1 ring-inset ring-emerald-300 dark:text-emerald-200 dark:ring-emerald-800"
                      >
                        {a.name}
                      </span>
                    ))
                  ) : (
                    <span className={muted}>Not resolved</span>
                  )}
                </td>
                <td className="px-3 py-2.5 text-xs">
                  {it.active_now ? regimen(it, med) : <span className={muted}>Stopped</span>}
                </td>
                <td className="px-3 py-2.5">
                  <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs dark:bg-slate-800">
                    {domain(med)}
                  </span>
                </td>
                <td className="px-3 py-2.5 text-xs">
                  {indication?.length
                    ? indication.join(", ")
                    : med?.therapeutic_class
                      ? `${med.therapeutic_class} (drug class)`
                      : "—"}
                </td>
                <td className="px-3 py-2.5 text-xs">
                  {!it.active_now ? (
                    <span className={muted}>Stopped: not screened</span>
                  ) : !screening ? (
                    <span className={muted}>Choose a diagnosis</span>
                  ) : hits[0] ? (
                    <span
                      className={`rounded-md px-2 py-0.5 font-semibold ring-1 ring-inset ${CHIP[hits[0].severity]}`}
                    >
                      ⚠ {hits[0].severity} conflict{hits.length > 1 ? ` (+${hits.length - 1})` : ""}
                    </span>
                  ) : (
                    <span className="text-slate-700 dark:text-slate-300">No alert in loaded data*</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-slate-600 dark:text-slate-400">
        Regimen is shown as recorded in the patient's history. Indication comes from approved
        medicine–condition links. *Absence of data is not proof of safety.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ container 2: diagnosis + alert */

function DiagnosisPanel({
  dx,
  onPick,
  formulations,
  chosen,
  setChosen,
}: {
  dx: Dx | null;
  onPick: (d: Dx) => void;
  formulations: MedicationView[];
  chosen: string[];
  setChosen: (fn: (xs: string[]) => string[]) => void;
}) {
  const [q, setQ] = useState("");
  const [notes, setNotes] = useState("");
  const search = useQuery({
    queryKey: ["summary-search", q],
    queryFn: ({ signal }) => autocomplete(q, signal),
    enabled: q.trim().length >= 2,
  });
  const hits = (search.data?.items ?? []).filter((s) => s.system.endsWith("namaste"));
  return (
    <section className={card} aria-labelledby="dx-h">
      <h2 id="dx-h" className={eyebrow}>
        Selected diagnosis
      </h2>
      {dx ? (
        <p className="mt-1 text-xl font-bold">
          {dx.display}{" "}
          <span className="font-mono text-sm font-semibold text-emerald-800 dark:text-emerald-300">
            {dx.code}
          </span>
        </p>
      ) : (
        <p className={`mt-1 ${muted}`}>None yet. Search below or load a demo case.</p>
      )}
      <label className="mt-3 block text-sm font-medium">
        {dx ? "Change diagnosis" : "Search a NAMASTE term"}
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Amavata, आमवात, Madhumeha…"
          className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-950"
        />
      </label>
      {q.trim().length >= 2 && (
        <ul className="mt-2 divide-y divide-slate-100 rounded-lg ring-1 ring-slate-200 dark:divide-slate-800 dark:ring-slate-800">
          {hits.length ? (
            hits.map((s) => (
              <li key={s.code}>
                <button
                  type="button"
                  onClick={() => {
                    onPick({ code: s.code, display: s.display });
                    setQ("");
                  }}
                  className="w-full px-3 py-2 text-left text-sm hover:bg-teal-50 dark:hover:bg-teal-950"
                >
                  <strong>{s.display}</strong> <span className="font-mono text-xs">{s.code}</span>
                  {s.devanagari && <span className="ml-2">{s.devanagari}</span>}
                </button>
              </li>
            ))
          ) : (
            <li className="px-3 py-2 text-sm">
              {search.isLoading ? "Searching…" : "No NAMASTE term found."}
            </li>
          )}
        </ul>
      )}
      <label className="mt-4 block text-sm font-medium">
        Consultation notes
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={3}
          className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-950"
        />
      </label>
      {dx && (
        <div className="mt-4">
          <p className={eyebrow}>Formulations screened (approved links)</p>
          {formulations.length ? (
            <ul className="mt-1 space-y-1">
              {formulations.map((m) => (
                <li key={m.code} className="flex items-start gap-2 text-sm">
                  <input
                    id={`screen-${m.code}`}
                    type="checkbox"
                    className="mt-1"
                    checked={chosen.includes(m.code)}
                    onChange={(e) =>
                      setChosen((xs) => (e.target.checked ? [...xs, m.code] : xs.filter((x) => x !== m.code)))
                    }
                  />
                  <label htmlFor={`screen-${m.code}`}>
                    <span className="font-semibold">{m.name}</span>{" "}
                    <span className="text-xs text-slate-600 dark:text-slate-400">
                      ({m.composition.map((c) => c.canonical_name).join(", ")})
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          ) : (
            <p className={`mt-1 ${muted}`}>No approved formulation link for this diagnosis.</p>
          )}
          <p className="mt-2 text-xs font-semibold">{REFERENCE_ONLY}</p>
        </div>
      )}
    </section>
  );
}

function InteractionPanel({
  ready,
  loading,
  alerts,
  message,
}: {
  ready: boolean;
  loading: boolean;
  alerts: Alert[];
  message: string;
}) {
  const sorted = [...alerts].sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity]);
  const top = sorted[0];
  // Tinted by the most severe alert; a plain card when there is none.
  const box =
    top?.severity === "major"
      ? "rounded-2xl border border-red-300 bg-red-50 p-5 dark:border-red-800 dark:bg-red-950/40"
      : top
        ? "rounded-2xl border border-saffron-300 bg-saffron-50 p-5 dark:border-saffron-700 dark:bg-saffron-900/20"
        : card;
  return (
    <section className={box} aria-labelledby="hdi-h">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="hdi-h" className={eyebrow}>
          High-priority herb–drug interaction
        </h2>
        {top && <span className="text-xs font-semibold">Triggered by: {top.other_medication}</span>}
      </div>
      {!ready ? (
        <p className={`mt-2 ${muted}`}>Choose a patient and a diagnosis with an approved formulation.</p>
      ) : loading ? (
        <p className="mt-2 text-sm">Screening…</p>
      ) : !top ? (
        <p className="mt-2 text-sm">{message}</p>
      ) : (
        <>
          <p className="mt-2 text-lg font-bold">
            <span
              className={`mr-2 rounded px-1.5 py-0.5 align-middle text-xs uppercase ring-1 ring-inset ${CHIP[top.severity]}`}
            >
              {top.severity}
            </span>
            {top.agents.join(" × ")}
          </p>
          <p className="mt-1 text-sm">{top.mechanism}</p>
          {top.management_note && (
            <p className="mt-2 text-sm">
              <strong>Clinical recommendation (from source):</strong> {top.management_note}
            </p>
          )}
          <p className="mt-2 font-mono text-[11px] opacity-90">{top.path}</p>
          <p className="mt-1 text-[11px] text-slate-600 dark:text-slate-400">
            Evidence: {top.evidence_level ?? "not stated"} · Source: {top.citation}
            {top.dataset && ` · ${top.dataset}`}
          </p>
          <details className="mt-3">
            <summary className="cursor-pointer text-sm font-semibold">
              All alerts ({sorted.length}) · acknowledge or override
            </summary>
            <div className="mt-2 space-y-2">
              <p className="text-xs" role="status">
                {message}
              </p>
              {sorted.map((a, i) => (
                <InteractionAlert key={a.id ?? `${a.path}-${i}`} a={a} />
              ))}
            </div>
          </details>
        </>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ container 3: crosswalk */

function CodeTile({
  title,
  code,
  display,
  note,
  tone,
}: {
  title: string;
  code: string | null;
  display: string | null;
  note: string;
  tone: string;
}) {
  return (
    <div className={`rounded-xl p-4 ring-1 ${tone}`}>
      <p className="text-[11px] font-bold uppercase tracking-wide">{title}</p>
      <p className="mt-1 font-mono text-xl font-bold">{code ?? "—"}</p>
      <p className="text-sm font-semibold">{display ?? "No approved link"}</p>
      <p className="mt-1 text-xs">{note}</p>
    </div>
  );
}

function CrosswalkPanel({
  c,
  devanagari,
  status,
}: {
  c: Explanation;
  devanagari?: string | null;
  status?: string;
}) {
  const bio = c.biomedicine;
  return (
    <>
      <div className="mt-4 grid gap-3 md:grid-cols-3">
        <CodeTile
          title="NAMASTE standard"
          code={c.namaste?.code ?? null}
          display={c.namaste?.display ?? null}
          note={devanagari ?? "Ayush terminology"}
          tone="bg-emerald-50 ring-emerald-200 text-emerald-950 dark:bg-emerald-950/40 dark:ring-emerald-900 dark:text-emerald-50"
        />
        <CodeTile
          title="WHO ICD-11 (TM2)"
          code={c.tm2.code}
          display={c.tm2.display}
          note={`From NAMASTE: ${REL[c.relationship]}`}
          tone="bg-sky-50 ring-sky-200 text-sky-950 dark:bg-sky-950/40 dark:ring-sky-900 dark:text-sky-50"
        />
        <CodeTile
          title="Biomedical equivalent"
          code={bio.code}
          display={bio.display}
          note={
            bio.code
              ? `From TM2: ${REL[bio.relationship]} · ${bio.source === "who-index" ? "WHO index" : bio.source} · ${bio.status}`
              : (bio.message ?? "A clinician can add a link.")
          }
          tone="bg-amber-50 ring-amber-200 text-amber-950 dark:bg-amber-950/40 dark:ring-amber-900 dark:text-amber-50"
        />
      </div>
      {(c.band !== "high" || status !== "suggested") && (
        <p className="mt-3 rounded-lg bg-saffron-50 px-3 py-2 text-sm text-saffron-900 dark:bg-saffron-900/30 dark:text-saffron-100">
          Needs clinician review: a mapping below the high band is never recorded without a clinician
          decision.
        </p>
      )}
      <div className="mt-3 rounded-xl bg-slate-50 p-3 text-sm dark:bg-slate-800/60">
        <p className={eyebrow}>Clinical rationale (why the engine matched)</p>
        <p className="mt-1">{c.reasoning}</p>
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ page */

export default function ClinicalSummary() {
  const user = useAuth();
  const selected = useSelectedPatient();
  const list = useQuery({ queryKey: ["patients"], queryFn: patients });
  const pid = selected?.id ?? null;
  const patient = list.data?.find((p) => p.id === pid);
  const [dx, setDx] = useState<Dx | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);

  const timeline = useQuery({
    queryKey: ["timeline", pid],
    queryFn: () => patientTimeline(pid!),
    enabled: !!pid,
  });
  const codes = useMemo(
    () => [
      ...new Set(
        (timeline.data?.items ?? []).map((i) => i.resolution.medication_code).filter((c): c is string => !!c),
      ),
    ],
    [timeline.data],
  );
  const medQueries = useQueries({
    queries: codes.map((code) => ({ queryKey: ["medication", code], queryFn: () => medication(code) })),
  });
  const meds = useMemo(() => {
    const out = new Map<string, MedicationView>();
    medQueries.forEach((q) => q.data && out.set(q.data.code, q.data));
    return out;
  }, [medQueries]);
  const links = useQuery({
    queryKey: ["medication-links", "approved"],
    queryFn: () => medicationLinks("approved"),
  });
  const indications = useMemo(() => {
    const out = new Map<string, string[]>();
    for (const l of links.data ?? []) {
      if (!l.condition.system.endsWith("bio") || !l.condition.display) continue;
      out.set(l.medication.code, [...(out.get(l.medication.code) ?? []), l.condition.display]);
    }
    return out;
  }, [links.data]);

  const mapped = useQuery({
    queryKey: ["summary-map", dx?.code],
    queryFn: () => mapTerm({ code: dx!.code, top_k: 1 }),
    enabled: !!dx,
  });
  const linked = useQuery({
    queryKey: ["by-condition", "namaste", dx?.code],
    queryFn: () => medicationsByCondition("namaste", dx!.code),
    enabled: !!dx,
  });
  useEffect(() => setChosen(linked.data?.ayush.map((l) => l.medication.code) ?? []), [linked.data]);
  const screen = useQuery({
    queryKey: ["interactions", pid, chosen.join(",")],
    queryFn: () => patientInteractions(pid!, chosen),
    enabled: !!pid && chosen.length > 0,
  });

  const c = mapped.data?.candidates[0];
  const alerts = screen.data?.alerts ?? [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Clinical summary</h1>
          <p className={muted}>
            Current medicines, herb–drug screening and the NAMASTE → WHO ICD-11 crosswalk for one synthetic
            patient.
          </p>
        </div>
        <div className="flex flex-wrap gap-2" aria-label="Demo cases">
          {DEMOS.map((d) => (
            <button
              key={d.label}
              type="button"
              onClick={() => {
                const p = list.data?.find((x) => x.id === d.patient);
                if (p) setSelectedPatient(p.id, p.display, p.abha_number);
                setDx(d.dx);
              }}
              className="rounded-lg px-3 py-1.5 text-sm font-medium text-teal-800 ring-1 ring-inset ring-teal-200 hover:bg-teal-50 dark:text-teal-200 dark:ring-teal-800 dark:hover:bg-teal-950"
            >
              Demo: {d.label}
            </button>
          ))}
        </div>
      </div>

      <section
        aria-label="Patient"
        className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-2xl bg-slate-900 px-5 py-3 text-sm text-slate-100"
      >
        <label className="flex min-w-0 max-w-full items-center gap-2">
          <span className="text-slate-300">Patient</span>
          <select
            style={{ maxWidth: "calc(100vw - 7rem)" }}
            value={pid ?? ""}
            onChange={(e) => {
              const p = list.data?.find((x) => x.id === e.target.value);
              setSelectedPatient(p?.id ?? null, p?.display, p?.abha_number);
            }}
            className="rounded-lg border border-slate-600 bg-slate-800 px-2 py-1 text-slate-100"
          >
            <option value="">Choose a synthetic patient…</option>
            {list.data?.map((p) => (
              <option key={p.id} value={p.id}>
                {p.display}
              </option>
            ))}
          </select>
        </label>
        {patient && (
          <span className="rounded-md bg-emerald-900 px-2 py-0.5 font-mono text-emerald-100">
            ABHA: {patient.abha_number}
          </span>
        )}
        <span className="ml-auto text-slate-300">
          Practitioner: <strong className="text-white">{user?.display_name}</strong>
        </span>
      </section>

      {/* Container 1 */}
      <section className={card} aria-labelledby="meds-h">
        <h2 id="meds-h" className="font-semibold">
          Current medicines &amp; active chemical salts
          {patient && (
            <span className="ml-2 text-xs font-normal text-slate-600 dark:text-slate-400">
              from the prescription history
            </span>
          )}
        </h2>
        {!pid ? (
          <p className={`mt-2 ${muted}`}>Choose a patient or load a demo case.</p>
        ) : timeline.isLoading ? (
          <p className="mt-2 text-sm">Loading…</p>
        ) : (
          <MedicinesTable
            items={timeline.data?.items ?? []}
            meds={meds}
            indications={indications}
            alerts={alerts}
            screening={chosen.length > 0}
          />
        )}
      </section>

      {/* Container 2 */}
      <div className="grid gap-6 lg:grid-cols-2">
        <InteractionPanel
          ready={!!pid && chosen.length > 0}
          loading={screen.isLoading}
          alerts={alerts}
          message={screen.data?.message ?? ""}
        />
        <DiagnosisPanel
          dx={dx}
          onPick={setDx}
          formulations={linked.data?.ayush.map((l) => l.medication) ?? []}
          chosen={chosen}
          setChosen={setChosen}
        />
      </div>

      {/* Container 3 */}
      <section className={card} aria-labelledby="cw-h">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h2 id="cw-h" className="font-semibold">
              Interoperability cross-walk
            </h2>
            <p className="text-xs text-slate-600 dark:text-slate-400">
              Three-way mapping across AYUSH and WHO terminology
            </p>
          </div>
          {c && (
            <span className="rounded-md bg-slate-100 px-2 py-1 font-mono text-xs dark:bg-slate-800">
              Confidence: {c.confidence.toFixed(2)} · {c.band} band
            </span>
          )}
        </div>
        {!dx ? (
          <p className={`mt-3 ${muted}`}>Choose a diagnosis.</p>
        ) : mapped.isLoading ? (
          <p className="mt-3 text-sm">Mapping…</p>
        ) : !c ? (
          <p className="mt-3 text-sm">{mapped.data?.message ?? "No candidate."}</p>
        ) : (
          <>
            <CrosswalkPanel
              c={c}
              devanagari={mapped.data?.resolved?.devanagari}
              status={mapped.data?.status}
            />
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-3 dark:border-slate-800">
              <p className="text-xs text-slate-600 dark:text-slate-400">
                {c.tm2.code.startsWith("SAMPLE-") && "SAMPLE — illustrative, not official codes. "}
                Datasets: {mapped.data?.dataset_releases.join(", ")}
              </p>
              <Link
                to={`/condition?namaste=${encodeURIComponent(c.namaste?.code ?? dx.code)}`}
                className="rounded-xl bg-teal-700 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-800"
              >
                Record as FHIR Condition →
              </Link>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
