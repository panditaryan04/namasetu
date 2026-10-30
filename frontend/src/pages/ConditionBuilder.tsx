import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { FHIRViewer } from "../components/FHIRViewer";
import { AlertList } from "../components/InteractionAlert";
import { PatientSelect } from "../components/PatientSelect";
import {
  buildCondition,
  medicationsByCondition,
  patientInteractions,
  type ConditionBuildRequest,
} from "../services/api";
import { useSelectedPatient } from "../services/patientContext";

const field =
  "mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-teal-600 focus:outline-none focus:ring-2 focus:ring-teal-600/20 dark:border-slate-700 dark:bg-slate-950";
const label = "block text-sm font-medium text-slate-700 dark:text-slate-300";

export default function ConditionBuilder() {
  const [params] = useSearchParams();
  const fromSuggestion = params.get("basis") === "suggestion";
  const [form, setForm] = useState<ConditionBuildRequest>({
    namaste_code: params.get("namaste") ?? "",
    tm2_code: fromSuggestion ? (params.get("tm2") ?? undefined) : undefined,
    confirm_suggestion: false,
    include_biomedicine: true,
    patient: { abha_number: "91-TEST-0000-0001", display: "Test Patient (SYNTHETIC)" },
    encounter_id: "enc-demo-1",
    clinical_status: "active",
    verification_status: "confirmed",
    category: "encounter-diagnosis",
    purpose_of_use: "TREAT",
  });
  const build = useMutation({ mutationFn: buildCondition });
  const patient = useSelectedPatient();
  // The patient in focus (from the Patient workspace or the selector) is the Condition's subject.
  useEffect(() => {
    if (patient?.abha_number)
      setForm((f) => ({
        ...f,
        patient: { id: patient.id, abha_number: patient.abha_number!, display: patient.display },
      }));
  }, [patient?.id, patient?.abha_number, patient?.display]);
  const refMeds = useQuery({
    queryKey: ["meds", "namaste", form.namaste_code],
    queryFn: () => medicationsByCondition("namaste", form.namaste_code),
    enabled: Boolean(build.data && form.namaste_code),
  });
  const medCodes = refMeds.data
    ? [...refMeds.data.ayush, ...refMeds.data.biomedical].map((m) => m.medication.code)
    : [];
  const checks = useQuery({
    queryKey: ["interactions", patient?.id, medCodes.join(",")],
    queryFn: () => patientInteractions(patient!.id, medCodes),
    enabled: Boolean(patient && medCodes.length),
  });
  const set = <K extends keyof ConditionBuildRequest>(k: K, v: ConditionBuildRequest[K]) =>
    setForm((f) => ({ ...f, [k]: v }));

  return (
    <div className="grid gap-6 lg:grid-cols-[380px_1fr]">
      <section aria-labelledby="cb-heading">
        <h1 id="cb-heading" className="text-2xl font-bold tracking-tight">
          Tri-coded Condition
        </h1>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          NAMASTE + ICD-11 TM2 (+ biomedicine when an approved link exists), as a FHIR R4 Condition.
        </p>
        <form
          className="mt-4 space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            build.mutate(form);
          }}
        >
          <label className={label}>
            NAMASTE code
            <input
              className={field}
              required
              value={form.namaste_code}
              onChange={(e) => set("namaste_code", e.target.value)}
            />
          </label>
          <label className={label}>
            ICD-11 TM2 code <span className="font-normal text-slate-500">(blank = approved mapping)</span>
            <input
              className={field}
              value={form.tm2_code ?? ""}
              onChange={(e) => set("tm2_code", e.target.value || undefined)}
            />
          </label>
          {form.tm2_code && (
            <label className="flex items-start gap-2 rounded-lg bg-saffron-50 p-3 text-sm text-saffron-900 dark:bg-saffron-900/30 dark:text-saffron-100">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={Boolean(form.confirm_suggestion)}
                onChange={(e) => set("confirm_suggestion", e.target.checked)}
              />
              I confirm this HIGH-band suggestion as the treating clinician. Medium/low suggestions are
              refused and belong in the review queue.
            </label>
          )}
          <div className="grid grid-cols-2 gap-3">
            <label className={label}>
              ABHA number (TEST)
              <input
                className={field}
                required
                value={form.patient.abha_number}
                onChange={(e) => set("patient", { ...form.patient, abha_number: e.target.value })}
              />
            </label>
            <label className={label}>
              Encounter
              <input
                className={field}
                value={form.encounter_id ?? ""}
                onChange={(e) => set("encounter_id", e.target.value || undefined)}
              />
            </label>
            <label className={label}>
              Clinical status
              <select
                className={field}
                value={form.clinical_status}
                onChange={(e) => set("clinical_status", e.target.value)}
              >
                {["active", "recurrence", "relapse", "inactive", "remission", "resolved"].map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            </label>
            <label className={label}>
              Verification
              <select
                className={field}
                value={form.verification_status}
                onChange={(e) => set("verification_status", e.target.value)}
              >
                {["confirmed", "provisional", "differential", "unconfirmed"].map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            </label>
            <label className={label}>
              Category
              <select
                className={field}
                value={form.category}
                onChange={(e) => set("category", e.target.value)}
              >
                <option>encounter-diagnosis</option>
                <option>problem-list-item</option>
              </select>
            </label>
            <label className={label}>
              Purpose of use
              <select
                className={field}
                value={form.purpose_of_use}
                onChange={(e) => set("purpose_of_use", e.target.value)}
              >
                {["TREAT", "ETREAT", "HPAYMT", "PUBHLTH", "HRESCH"].map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            </label>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={Boolean(form.include_biomedicine)}
              onChange={(e) => set("include_biomedicine", e.target.checked)}
            />
            Include approved biomedical code (supports claim workflows; insurers decide)
          </label>
          <button
            type="submit"
            disabled={build.isPending}
            className="w-full rounded-xl bg-teal-700 px-4 py-2.5 font-semibold text-white hover:bg-teal-800 disabled:opacity-60"
          >
            {build.isPending ? "Building…" : "Build Condition"}
          </button>
        </form>
        <p className="mt-3 text-xs text-slate-500">
          Every build is written to the audit ledger (AuditEvent) with a Provenance record. Synthetic patients
          only.
        </p>
      </section>

      <section aria-label="Result" className="min-w-0">
        {build.isError && (
          <p
            role="alert"
            className="mb-4 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200"
          >
            {build.error.message}
            {" · "}
            <Link to={`/map?code=${encodeURIComponent(form.namaste_code)}`} className="underline">
              see suggestions
            </Link>
          </p>
        )}
        <div className="mb-3">
          <PatientSelect />
        </div>
        {build.data && patient && checks.data && (
          <section aria-label="Reference medication checks" className="mb-4 space-y-2">
            <h2 className="text-sm font-semibold">
              Reference medications for this condition, checked against {patient.display}
            </h2>
            <AlertList result={checks.data} />
          </section>
        )}
        {build.data ? (
          <FHIRViewer resource={build.data} filename={`Condition-${build.data.id ?? "new"}.json`} />
        ) : (
          <div className="grid h-64 place-items-center rounded-2xl border border-dashed border-slate-300 text-sm text-slate-500 dark:border-slate-700">
            The FHIR Condition preview appears here.
          </div>
        )}
      </section>
    </div>
  );
}
