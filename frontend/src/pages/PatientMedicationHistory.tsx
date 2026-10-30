import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { AlertList } from "../components/InteractionAlert";
import { ReferenceBanner } from "../components/MedicationCard";
import { MedicationTimeline } from "../components/MedicationTimeline";
import {
  importHistoryText,
  medicationSearch,
  patientInteractions,
  patientTimeline,
  patients,
} from "../services/api";
import { setSelectedPatient } from "../services/patientContext";

export function PatientList() {
  const list = useQuery({ queryKey: ["patients"], queryFn: patients });
  return (
    <section aria-labelledby="pl-h" className="space-y-4">
      <h1 id="pl-h" className="text-2xl font-bold tracking-tight">
        Patient <span className="text-base font-normal text-slate-500">(SYNTHETIC — demo only)</span>
      </h1>
      <p className="text-sm text-slate-600 dark:text-slate-400">
        Choose a patient to open their workspace: medication timeline and active salts, interaction checks,
        and diagnosis coding as a FHIR Condition with that patient as the subject.
      </p>
      <ul className="grid gap-2 sm:grid-cols-2">
        {list.data?.map((p) => (
          <li key={p.id}>
            <Link
              to={`/patients/${p.id}`}
              className="block rounded-xl px-4 py-3 ring-1 ring-inset ring-slate-200 hover:bg-brand-50 dark:ring-slate-700 dark:hover:bg-brand-950"
            >
              <span className="font-medium">{p.display}</span>
              <span className="block text-xs text-slate-500">
                ABHA {p.abha_number} · {p.active} active / {p.medications} recorded
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function PatientMedicationHistory() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const timeline = useQuery({ queryKey: ["timeline", id], queryFn: () => patientTimeline(id) });
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [candidate, setCandidate] = useState<{ code: string; name: string } | null>(null);
  const importer = useMutation({
    mutationFn: () => importHistoryText(id, text),
    onSuccess: () => {
      setText("");
      qc.invalidateQueries({ queryKey: ["timeline", id] });
    },
  });
  const search = useQuery({
    queryKey: ["med-search", query],
    queryFn: () => medicationSearch(query),
    enabled: query.trim().length >= 2,
  });
  const alerts = useQuery({
    queryKey: ["interactions", id, candidate?.code],
    queryFn: () => patientInteractions(id, [candidate!.code]),
    enabled: Boolean(candidate),
  });
  useEffect(
    () => setSelectedPatient(id, timeline.data?.patient.display, timeline.data?.patient.abha_number),
    [id, timeline.data],
  );

  if (timeline.isPending)
    return <div className="h-48 animate-pulse rounded-2xl bg-slate-100 dark:bg-slate-900" />;
  if (!timeline.data) return <p role="alert">Patient not found.</p>;
  const t = timeline.data;
  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-wider text-slate-500">
            <Link to="/patients" className="underline">
              Patient
            </Link>{" "}
            · SYNTHETIC record
          </p>
          <h1 className="text-2xl font-bold tracking-tight">{t.patient.display}</h1>
          <p className="text-sm text-slate-600 dark:text-slate-400">
            ABHA {t.patient.abha_number} (TEST) · {t.patient.active} active medication(s) · interaction data{" "}
            {t.dataset ?? "not loaded"}
          </p>
        </div>
        <nav aria-label="Patient actions" className="flex flex-wrap gap-2">
          <Link
            to="/"
            className="rounded-lg px-3 py-2 text-sm font-semibold ring-1 ring-inset ring-slate-300 hover:bg-slate-50 dark:ring-slate-600 dark:hover:bg-slate-900"
          >
            Find a diagnosis code
          </Link>
          <Link
            to="/condition"
            className="rounded-lg bg-brand-700 px-3 py-2 text-sm font-semibold text-white hover:bg-brand-800"
          >
            Record a Condition
          </Link>
        </nav>
      </header>
      <ReferenceBanner />

      <section
        aria-labelledby="tl-h"
        className="rounded-2xl border border-slate-200 p-4 dark:border-slate-800"
      >
        <h2 id="tl-h" className="mb-2 font-semibold">
          Medication timeline
        </h2>
        <MedicationTimeline items={t.items} asOf={t.as_of} />
        {t.existing_alerts.length > 0 && (
          <div className="mt-4">
            <h3 className="text-sm font-semibold">Interactions among current medications</h3>
            <AlertList
              result={{ alerts: t.existing_alerts, message: "From the loaded dataset; informational." }}
              canDecide={false}
            />
          </div>
        )}
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <section
          aria-labelledby="cons-h"
          className="rounded-2xl border border-slate-200 p-4 dark:border-slate-800"
        >
          <h2 id="cons-h" className="font-semibold">
            Consider a medication
          </h2>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Brand, generic, formulation or herb"
            aria-label="Medication to consider"
            className="mt-2 w-full rounded-lg border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-950"
          />
          <ul className="mt-2 flex flex-wrap gap-2">
            {search.data?.hits
              .filter((h) => h.type !== "ingredient")
              .slice(0, 6)
              .map((h) => (
                <li key={h.ref}>
                  <button
                    type="button"
                    aria-pressed={candidate?.code === h.ref}
                    onClick={() => setCandidate({ code: h.ref, name: h.name })}
                    className={`rounded-full px-3 py-1 text-sm ring-1 ring-inset ${candidate?.code === h.ref ? "bg-brand-700 text-white ring-brand-700" : "ring-slate-300 dark:ring-slate-600"}`}
                  >
                    {h.name}
                  </button>
                </li>
              ))}
          </ul>
          {candidate && (
            <div className="mt-3">
              <p className="mb-2 text-sm">
                Checking <strong>{candidate.name}</strong> against active medications:
              </p>
              {alerts.data && <AlertList result={alerts.data} />}
            </div>
          )}
        </section>

        <section
          aria-labelledby="imp-h"
          className="rounded-2xl border border-slate-200 p-4 dark:border-slate-800"
        >
          <h2 id="imp-h" className="font-semibold">
            Import medication list
          </h2>
          <p className="text-xs text-slate-500">
            One per line, e.g. “Crocin 500”, “Ashwagandha churna”, “Tab Metformin 500mg BD”.
          </p>
          <label className="sr-only" htmlFor="imp-text">
            Medication list
          </label>
          <textarea
            id="imp-text"
            rows={5}
            value={text}
            onChange={(e) => setText(e.target.value)}
            className="mt-2 w-full rounded-lg border border-slate-300 px-3 py-2 font-mono text-sm dark:border-slate-700 dark:bg-slate-950"
          />
          <button
            type="button"
            disabled={!text.trim() || importer.isPending}
            onClick={() => importer.mutate()}
            className="mt-2 rounded-lg bg-brand-700 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"
          >
            Import as active
          </button>
          {importer.data && (
            <ul className="mt-2 space-y-1 text-sm" role="status">
              {importer.data.items.map((i) => (
                <li key={i.id}>
                  “{i.text}” →{" "}
                  {i.resolution.kind === "unresolved" ? (
                    <strong className="text-saffron-800 dark:text-saffron-200">
                      Unrecognised — please verify
                    </strong>
                  ) : (
                    <>
                      {i.resolution.label}{" "}
                      <span className="text-xs text-slate-500">({i.resolution.kind})</span>
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
