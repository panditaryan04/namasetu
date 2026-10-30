import { useQuery } from "@tanstack/react-query";
import { patients } from "../services/api";
import { can } from "../services/auth";
import { setSelectedPatient, useSelectedPatient } from "../services/patientContext";
import { useAuth } from "../services/useAuth";

/** Pick a SYNTHETIC patient so medication views can check against their active salts (clinician / admin). */
export function PatientSelect() {
  const user = useAuth();
  const selected = useSelectedPatient();
  const allowed = can(user, "patient");
  const list = useQuery({ queryKey: ["patients"], queryFn: patients, enabled: allowed });
  if (!allowed) return null;
  return (
    <label className="flex items-center gap-2 text-sm">
      <span className="text-slate-600 dark:text-slate-400">Check against patient</span>
      <select
        value={selected?.id ?? ""}
        onChange={(e) => {
          const p = list.data?.find((x) => x.id === e.target.value);
          setSelectedPatient(p?.id ?? null, p?.display, p?.abha_number);
        }}
        className="rounded-lg border border-slate-300 px-2 py-1 dark:border-slate-700 dark:bg-slate-900"
      >
        <option value="">— none —</option>
        {list.data?.map((p) => (
          <option key={p.id} value={p.id}>
            {p.display}
          </option>
        ))}
      </select>
    </label>
  );
}
