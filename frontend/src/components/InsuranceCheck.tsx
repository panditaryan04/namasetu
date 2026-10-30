import type { Explanation } from "../services/api";

/** Whether an approved biomedical ICD-11 code exists. It supports TPA claim workflows; insurers decide claims. */
export function InsuranceCheck({ bio }: { bio: Explanation["biomedicine"] }) {
  const approved = bio.status === "approved" && bio.code;
  return (
    <p
      className={`inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs font-medium ${
        approved
          ? "bg-teal-50 text-teal-800 dark:bg-teal-950 dark:text-teal-200"
          : "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300"
      }`}
      title={
        approved
          ? "An approved biomedical code supports cashless (TPA) claim workflows. It does not guarantee adjudication: insurers decide claims."
          : "Without an approved biomedical code, claim workflows need a clinician-added link."
      }
    >
      <span aria-hidden="true">{approved ? "✓" : "—"}</span>
      {approved ? "Biomedical code available for claims workflow" : "No approved biomedical code"}
    </p>
  );
}
