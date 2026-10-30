import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { acknowledgeAlert, type Alert } from "../services/api";

const STYLE: Record<Alert["severity"], string> = {
  major: "border-red-300 bg-red-50 text-red-950 dark:border-red-800 dark:bg-red-950/60 dark:text-red-50",
  moderate:
    "border-saffron-300 bg-saffron-50 text-saffron-950 dark:border-saffron-700 dark:bg-saffron-900/30 dark:text-saffron-50",
  minor:
    "border-slate-300 bg-slate-50 text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100",
};
const KIND: Record<Alert["kind"], string> = {
  duplicate: "Duplicate salt",
  "herb-drug": "Herb–drug interaction",
  "drug-drug": "Drug–drug interaction",
};

/** One alert: severity colour, both agents, the trigger path, evidence, citation and the clinician decision. */
export function InteractionAlert({ a, canDecide = true }: { a: Alert; canDecide?: boolean }) {
  const qc = useQueryClient();
  const [reason, setReason] = useState("");
  const decide = useMutation({
    mutationFn: (action: "acknowledge" | "override") => acknowledgeAlert(a.id!, action, reason),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["interactions"] }),
  });
  const current = decide.data ?? a;
  return (
    <div
      role="alert"
      aria-label={`${a.severity} ${KIND[a.kind]}`}
      className={`rounded-xl border p-3 text-sm ${STYLE[a.severity]}`}
    >
      <header className="flex flex-wrap items-center gap-2">
        <span className="rounded px-1.5 py-0.5 text-[11px] font-bold uppercase ring-1 ring-inset ring-current">
          {a.severity}
        </span>
        <span className="font-semibold">{KIND[a.kind]}:</span>
        <span className="font-semibold">
          {a.agents[0] === a.agents[1] ? a.agents[0] : a.agents.join(" × ")}
        </span>
        {a.evidence_level && <span className="text-xs opacity-80">evidence: {a.evidence_level}</span>}
      </header>
      <p className="mt-1">{a.mechanism}</p>
      <p className="mt-1 font-mono text-[11px] opacity-90">{a.path}</p>
      <p className="mt-1 text-xs">
        With <strong>{a.other_medication}</strong>
        {a.other_last_active && ` — ${a.other_last_active}`}
      </p>
      {a.management_note && <p className="mt-1 text-xs">Source note: {a.management_note}</p>}
      <p className="mt-1 text-[11px] opacity-80">
        Source: {a.citation}
        {a.dataset && ` · ${a.dataset}`}
      </p>
      {current.status !== "open" ? (
        <p className="mt-2 text-xs font-semibold">
          {current.status} by {current.decided_by}: “{current.decision_reason}”
        </p>
      ) : (
        canDecide &&
        a.id !== null && (
          <div className="mt-2 flex flex-wrap items-end gap-2">
            <label className="min-w-[12rem] flex-1 text-xs">
              Reason (required)
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                className="mt-0.5 w-full rounded-md border border-current/30 bg-white/70 px-2 py-1 text-sm text-slate-900 dark:bg-slate-950/60 dark:text-slate-100"
              />
            </label>
            <button
              type="button"
              disabled={reason.trim().length < 3 || decide.isPending}
              onClick={() => decide.mutate("acknowledge")}
              className="rounded-md bg-slate-900 px-2.5 py-1.5 text-xs font-semibold text-white disabled:opacity-50 dark:bg-white dark:text-slate-900"
            >
              Acknowledge
            </button>
            <button
              type="button"
              disabled={reason.trim().length < 3 || decide.isPending}
              onClick={() => decide.mutate("override")}
              className="rounded-md px-2.5 py-1.5 text-xs font-semibold ring-1 ring-inset ring-current disabled:opacity-50"
            >
              Override
            </button>
          </div>
        )
      )}
      {decide.isError && <p className="mt-1 text-xs">{decide.error.message}</p>}
    </div>
  );
}

export function AlertList({
  result,
  canDecide,
}: {
  result: { alerts: Alert[]; message: string };
  canDecide?: boolean;
}) {
  return (
    <div className="space-y-2">
      <p className="text-xs text-slate-600 dark:text-slate-400" role="status">
        {result.message}
      </p>
      {result.alerts.map((a, i) => (
        <InteractionAlert key={a.id ?? `${a.path}-${i}`} a={a} canDecide={canDecide} />
      ))}
    </div>
  );
}
