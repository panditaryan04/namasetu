import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  medicationsByCondition,
  patientInteractions,
  type Explanation,
  type LinkedMedication,
} from "../services/api";
import { useSelectedPatient } from "../services/patientContext";
import { AlertList } from "./InteractionAlert";
import { PatientSelect } from "./PatientSelect";
import { MedicationCard, ReferenceBanner } from "./MedicationCard";

function unique(items: LinkedMedication[]): LinkedMedication[] {
  const seen = new Set<string>();
  return items.filter((i) => (seen.has(i.medication.code) ? false : (seen.add(i.medication.code), true)));
}

/** Reference medications for a mapped condition: Ayush (via NAMASTE + TM2) and biomedical (via an APPROVED ICD-11 link). */
export function MedicationPanel({ c }: { c: Explanation }) {
  const [tab, setTab] = useState<"ayush" | "bio">("ayush");
  const namaste = useQuery({
    queryKey: ["meds", "namaste", c.namaste?.code],
    queryFn: () => medicationsByCondition("namaste", c.namaste!.code),
    enabled: Boolean(c.namaste),
  });
  const tm2 = useQuery({
    queryKey: ["meds", "tm2", c.tm2.code],
    queryFn: () => medicationsByCondition("icd11-tm2", c.tm2.code),
  });
  const bioApproved = c.biomedicine.status === "approved" && c.biomedicine.code;
  const bio = useQuery({
    queryKey: ["meds", "bio", c.biomedicine.code],
    queryFn: () => medicationsByCondition("icd11-bio", c.biomedicine.code!),
    enabled: Boolean(bioApproved),
  });
  const ayush = unique([...(namaste.data?.ayush ?? []), ...(tm2.data?.ayush ?? [])]);
  const biomedical = bio.data?.biomedical ?? [];
  const patient = useSelectedPatient();
  const codes = [...ayush, ...biomedical].map((i) => i.medication.code);
  const checks = useQuery({
    queryKey: ["interactions", patient?.id, codes.join(",")],
    queryFn: () => patientInteractions(patient!.id, codes),
    enabled: Boolean(patient && codes.length),
  });
  const alertsFor = (code: string) => checks.data?.alerts.filter((a) => a.candidate_code === code) ?? [];
  const card = (i: LinkedMedication) => (
    <div key={i.medication.code} className="space-y-2">
      <MedicationCard m={i.medication} link={i.link} />
      {patient && checks.data && (
        <AlertList
          result={{
            alerts: alertsFor(i.medication.code),
            message: alertsFor(i.medication.code).length
              ? `For ${patient.display}:`
              : `${patient.display}: no interaction found in the loaded dataset (${checks.data.dataset ?? "none"}).`,
          }}
        />
      )}
    </div>
  );
  const tabBtn = (id: "ayush" | "bio", label: string, n: number) => (
    <button
      type="button"
      role="tab"
      aria-selected={tab === id}
      onClick={() => setTab(id)}
      className={`rounded-md px-3 py-1.5 text-sm font-medium ${tab === id ? "bg-white shadow dark:bg-slate-950" : "text-slate-600 dark:text-slate-300"}`}
    >
      {label} <span className="text-xs text-slate-500">({n})</span>
    </button>
  );
  const loading = namaste.isPending || tm2.isPending || (bioApproved && bio.isPending);
  return (
    <section
      aria-label="Reference medications"
      className="mt-4 space-y-3 border-t border-slate-200 pt-4 dark:border-slate-800"
    >
      <div className="flex flex-wrap items-center gap-3">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
          Reference medications
        </h4>
        <div
          role="tablist"
          aria-label="Medication type"
          className="inline-flex rounded-lg bg-slate-100 p-1 dark:bg-slate-800"
        >
          {tabBtn("ayush", "Ayush formulations", ayush.length)}
          {tabBtn("bio", "Biomedical (salt)", biomedical.length)}
        </div>
      </div>
      <ReferenceBanner />
      <PatientSelect />
      {loading && <div className="h-16 animate-pulse rounded-xl bg-slate-100 dark:bg-slate-900" />}
      {!loading && tab === "ayush" && (
        <div role="tabpanel" className="grid gap-2 lg:grid-cols-2">
          {ayush.length === 0 && (
            <p className="text-sm text-slate-500">
              No approved Ayush reference medication for this condition.
            </p>
          )}
          {ayush.map(card)}
        </div>
      )}
      {!loading && tab === "bio" && (
        <div role="tabpanel" className="grid gap-2 lg:grid-cols-2">
          {!bioApproved && (
            <p className="text-sm text-slate-500">
              No approved biomedical link for this TM2 code, so no biomedical medications are shown.
            </p>
          )}
          {bioApproved && biomedical.length === 0 && (
            <p className="text-sm text-slate-500">No approved biomedical reference medication.</p>
          )}
          {biomedical.map(card)}
        </div>
      )}
    </section>
  );
}
