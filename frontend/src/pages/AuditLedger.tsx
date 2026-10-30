import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { auditEvents, verifyChain } from "../services/api";

const PAGE = 50;

export default function AuditLedger() {
  const [offset, setOffset] = useState(0);
  const [action, setAction] = useState("");
  const events = useQuery({
    queryKey: ["audit", offset, action],
    queryFn: () => auditEvents({ limit: PAGE, offset, action: action || undefined }),
  });
  const verify = useMutation({ mutationFn: verifyChain });

  return (
    <section aria-labelledby="audit-heading" className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="mr-auto">
          <h1 id="audit-heading" className="text-2xl font-bold tracking-tight">
            Audit ledger
          </h1>
          <p className="text-sm text-slate-600 dark:text-slate-400">
            Append-only and SHA-256 hash-chained. Also served as FHIR AuditEvent at /fhir/AuditEvent.
          </p>
        </div>
        <label className="text-sm">
          Action
          <select
            value={action}
            onChange={(e) => (setAction(e.target.value), setOffset(0))}
            className="ml-2 rounded-lg border border-slate-300 px-2 py-1.5 dark:border-slate-700 dark:bg-slate-900"
          >
            <option value="">all</option>
            {[
              "ingest",
              "crosswalk_import",
              "map",
              "propose",
              "approve",
              "reject",
              "publish",
              "fhir_export",
              "bundle_ingest",
              "login",
              "edit",
            ].map((a) => (
              <option key={a}>{a}</option>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={() => verify.mutate()}
          className="rounded-lg bg-teal-700 px-3 py-2 text-sm font-semibold text-white hover:bg-teal-800"
        >
          {verify.isPending ? "Verifying…" : "Verify chain"}
        </button>
      </div>
      {verify.data && (
        <p
          role="status"
          className={`rounded-xl px-4 py-3 text-sm font-medium ${verify.data.ok ? "bg-teal-50 text-teal-900 dark:bg-teal-950 dark:text-teal-100" : "bg-red-50 text-red-900 dark:bg-red-950 dark:text-red-100"}`}
        >
          {verify.data.ok
            ? `Chain intact: ${verify.data.checked} events verified · head ${verify.data.head_hash?.slice(0, 16)}…`
            : `Chain BROKEN at event #${verify.data.first_broken_id}: ${verify.data.reason}`}
        </p>
      )}
      <div className="overflow-x-auto rounded-2xl border border-slate-200 dark:border-slate-800">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Audit events</caption>
          <thead className="bg-slate-50 text-xs uppercase tracking-wider text-slate-500 dark:bg-slate-900">
            <tr>
              <th className="px-3 py-2">#</th>
              <th className="px-3 py-2">Time (UTC)</th>
              <th className="px-3 py-2">Actor</th>
              <th className="px-3 py-2">Action</th>
              <th className="px-3 py-2">Entity</th>
              <th className="px-3 py-2">Hash</th>
            </tr>
          </thead>
          <tbody>
            {events.data?.items.map((e) => (
              <tr key={e.id} className="border-t border-slate-100 dark:border-slate-800">
                <td className="px-3 py-1.5 font-mono text-xs">{e.id}</td>
                <td className="px-3 py-1.5 font-mono text-xs">{e.ts.replace("T", " ").slice(0, 19)}</td>
                <td className="px-3 py-1.5">
                  {e.actor} <span className="text-xs text-slate-500">({e.role})</span>
                </td>
                <td className="px-3 py-1.5">
                  {e.action}
                  {e.purpose_of_use && (
                    <span className="ml-1 text-xs text-slate-500">[{e.purpose_of_use}]</span>
                  )}
                </td>
                <td className="px-3 py-1.5">
                  {e.entity_type}/{e.entity_id}
                </td>
                <td className="px-3 py-1.5 font-mono text-xs" title={`prev ${e.prev_hash}`}>
                  {e.hash.slice(0, 12)}…
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex items-center gap-3 text-sm">
        <button
          type="button"
          disabled={offset === 0}
          onClick={() => setOffset((o) => Math.max(0, o - PAGE))}
          className="rounded-lg px-3 py-1.5 ring-1 ring-inset ring-slate-300 disabled:opacity-50 dark:ring-slate-600"
        >
          Newer
        </button>
        <button
          type="button"
          disabled={!events.data || offset + PAGE >= events.data.total}
          onClick={() => setOffset((o) => o + PAGE)}
          className="rounded-lg px-3 py-1.5 ring-1 ring-inset ring-slate-300 disabled:opacity-50 dark:ring-slate-600"
        >
          Older
        </button>
        <span className="text-slate-500">{events.data?.total ?? 0} events</span>
      </div>
    </section>
  );
}
