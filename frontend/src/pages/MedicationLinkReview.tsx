import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { MedicationCard, ReferenceBanner } from "../components/MedicationCard";
import { decideMedicationLink, medicationLinks } from "../services/api";
import { can } from "../services/auth";
import { useAuth } from "../services/useAuth";

/** Review tab for condition -> medication links: only cited links; approve, or reject with a comment. */
export default function MedicationLinkReview() {
  const user = useAuth();
  const qc = useQueryClient();
  const [status, setStatus] = useState("pending");
  const [comments, setComments] = useState<Record<number, string>>({});
  const links = useQuery({ queryKey: ["med-links", status], queryFn: () => medicationLinks(status) });
  const decide = useMutation({
    mutationFn: (v: { id: number; action: "approve" | "reject" }) =>
      decideMedicationLink(v.id, v.action, comments[v.id]),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["med-links"] }),
  });
  const reviewer = can(user, "review");
  return (
    <section aria-label="Medication link review" className="space-y-4">
      <div className="flex items-center gap-3">
        <p className="mr-auto text-sm text-slate-600 dark:text-slate-400">
          Condition → reference medication links from cited guidelines. Nothing appears in medication panels
          until approved.
        </p>
        <label className="text-sm">
          Status{" "}
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="rounded-lg border border-slate-300 px-2 py-1.5 dark:border-slate-700 dark:bg-slate-900"
          >
            {["pending", "approved", "rejected", "all"].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
      </div>
      <ReferenceBanner />
      {decide.isError && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-300">
          {decide.error.message}
        </p>
      )}
      {links.data?.length === 0 && <p className="text-sm text-slate-500">Nothing here.</p>}
      <ul className="space-y-3">
        {links.data?.map(({ link, condition, medication }) => (
          <li key={link.id} className="rounded-2xl border border-slate-200 p-4 dark:border-slate-800">
            <p className="text-sm">
              <span className="font-semibold">{condition.display ?? condition.code}</span>{" "}
              <span className="font-mono text-xs text-slate-500">
                {condition.system} {condition.code}
              </span>{" "}
              → {medication.name} <span className="text-xs">({link.status_label})</span>
            </p>
            <div className="mt-2">
              <MedicationCard m={medication} link={link} />
            </div>
            {link.status === "pending" && (
              <div className="mt-3 flex flex-wrap items-end gap-2">
                <label className="min-w-[16rem] flex-1 text-sm">
                  Comment <span className="text-slate-500">(mandatory to reject)</span>
                  <input
                    value={comments[link.id] ?? ""}
                    onChange={(e) => setComments((c) => ({ ...c, [link.id]: e.target.value }))}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 dark:border-slate-700 dark:bg-slate-950"
                  />
                </label>
                <button
                  type="button"
                  disabled={!reviewer || decide.isPending}
                  onClick={() => decide.mutate({ id: link.id, action: "approve" })}
                  className="rounded-lg bg-teal-700 px-3 py-2 text-sm font-semibold text-white hover:bg-teal-800 disabled:opacity-50"
                >
                  Approve link
                </button>
                <button
                  type="button"
                  disabled={!reviewer || decide.isPending}
                  onClick={() => decide.mutate({ id: link.id, action: "reject" })}
                  className="rounded-lg bg-red-600 px-3 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-50"
                >
                  Reject
                </button>
              </div>
            )}
            {link.status !== "pending" && link.note && (
              <p className="mt-2 text-xs text-slate-500">Note: {link.note}</p>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
