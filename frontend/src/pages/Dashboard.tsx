import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ChartCard, CountColumns, DailyLines, DataTable, StatTile, StatusMark } from "../components/charts";
import {
  createExportJob,
  dashboard,
  downloadFile,
  exportJobs,
  type ExportFormat,
  type ExportKind,
} from "../services/api";
import { pct } from "../services/format";

const BAND_COLORS = ["var(--ord-3)", "var(--ord-2)", "var(--ord-1)"]; // high → low, ordinal ramp

function Exports() {
  const qc = useQueryClient();
  const [kind, setKind] = useState<ExportKind>("mappings");
  const [format, setFormat] = useState<ExportFormat>("csv");
  // Jobs run on the Celery worker: keep polling while any of them is still queued.
  const jobs = useQuery({
    queryKey: ["export-jobs"],
    queryFn: exportJobs,
    refetchInterval: (q) => (q.state.data?.some((j) => j.status === "queued") ? 1000 : false),
  });
  const create = useMutation({
    mutationFn: () => createExportJob(kind, format),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["export-jobs"] }),
  });
  const select = "rounded-lg border border-slate-300 px-2 py-1 dark:border-slate-700 dark:bg-slate-950";
  return (
    <section
      aria-labelledby="exp-h"
      className="rounded-2xl border border-slate-200 p-4 dark:border-slate-800"
    >
      <h2 id="exp-h" className="font-semibold">
        Exports
      </h2>
      <p className="text-xs text-slate-500">
        Each file carries the generation date, the dataset releases and the prototype disclaimer in its
        header.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-2 text-sm">
        <label>
          <span className="block text-xs text-slate-500">What</span>
          <select value={kind} onChange={(e) => setKind(e.target.value as ExportKind)} className={select}>
            <option value="mappings">Mappings</option>
            <option value="decisions">Review decisions</option>
            <option value="audit">Audit ledger</option>
          </select>
        </label>
        <label>
          <span className="block text-xs text-slate-500">Format</span>
          <select
            value={format}
            onChange={(e) => setFormat(e.target.value as ExportFormat)}
            className={select}
          >
            <option value="csv">CSV</option>
            <option value="json">JSON</option>
            <option value="pdf">PDF</option>
          </select>
        </label>
        <button
          type="button"
          disabled={create.isPending}
          onClick={() => create.mutate()}
          className="rounded-lg bg-brand-700 px-3 py-1.5 font-semibold text-white disabled:opacity-50"
        >
          Create export
        </button>
        {create.isError && (
          <span role="alert" className="text-red-700 dark:text-red-400">
            {(create.error as Error).message}
          </span>
        )}
      </div>
      {jobs.data && jobs.data.length > 0 && (
        <ul className="mt-3 space-y-1 text-sm" aria-label="Your exports">
          {jobs.data.slice(0, 8).map((j) => (
            <li key={j.id} className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs">{j.filename ?? `${j.kind}.${j.format}`}</span>
              <span className="text-xs text-slate-500">
                {j.status} · {j.rows} rows · {(j.size / 1024).toFixed(1)} KB
              </span>
              {j.download_url && (
                <button
                  type="button"
                  onClick={() => downloadFile(j.download_url!, j.filename ?? "export")}
                  className="text-xs font-semibold text-brand-700 underline dark:text-brand-300"
                >
                  Download
                </button>
              )}
              {j.error && <span className="text-xs text-red-700 dark:text-red-400">{j.error}</span>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default function DashboardPage() {
  const d = useQuery({ queryKey: ["dashboard"], queryFn: dashboard });
  if (d.isPending) return <div className="h-48 animate-pulse rounded-2xl bg-slate-100 dark:bg-slate-900" />;
  if (!d.data) return <p role="alert">Dashboard unavailable.</p>;
  const { kpis, band_distribution: bands, queue, mapping_gap: gap, latest_eval: ev } = d.data;
  const bandRows = [
    { name: "High", value: bands.high },
    { name: "Medium (review)", value: bands.medium },
    { name: "Low (no match)", value: bands.low },
  ];
  const gapRows = gap.bars.map((b) => ({ name: b.label, value: b.value }));
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold tracking-tight">Dashboard</h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Computed from the loaded datasets ({d.data.dataset_releases.join(", ")})
          {d.data.is_sample && " — SAMPLE, illustrative, not official"}.
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <StatTile
          label="NAMASTE codes ingested"
          value={kpis.namaste_codes}
          detail={`${kpis.tm2_codes} TM2 · ${kpis.biomedicine_codes} biomedicine`}
        />
        <StatTile
          label="Mapped (approved)"
          value={kpis.mapped_approved}
          detail={pct(kpis.mapped_approved / Math.max(kpis.namaste_codes, 1))}
        />
        <StatTile
          label="Pending review"
          value={kpis.pending_review}
          detail={
            queue.median_hours_to_decision != null
              ? `median ${queue.median_hours_to_decision} h to decision`
              : "no decisions yet"
          }
        />
        <StatTile label="No safe match" value={kpis.no_match} detail="top suggestion below T_LOW" />
        <StatTile
          label="Approved biomedical links"
          value={kpis.approved_biomedical_links}
          detail={`${kpis.pending_biomedical_links} pending`}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <ChartCard
          title="The mapping gap"
          note={`${gap.label}. Source: ${gap.source}.`}
          chart={<CountColumns data={gapRows} />}
          table={
            <DataTable
              caption="Mapping gap (official figures as cited)"
              rows={gapRows}
              columns={[
                { key: "n", label: "Figure", value: (x) => x.name },
                { key: "v", label: "Value", value: (x) => x.value.toLocaleString("en-IN"), numeric: true },
              ]}
            />
          }
        />
        <ChartCard
          title="Confidence bands"
          note={d.data.band_note}
          chart={<CountColumns data={bandRows} colors={BAND_COLORS} />}
          table={
            <DataTable
              caption="Confidence bands"
              rows={bandRows}
              columns={[
                { key: "n", label: "Band", value: (x) => x.name },
                { key: "v", label: "NAMASTE concepts", value: (x) => x.value, numeric: true },
              ]}
            />
          }
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <ChartCard
          title="Review queue throughput (last 14 days)"
          note={
            Object.entries(queue.by_status)
              .map(([k, v]) => `${v} ${k}`)
              .join(" · ") || "Queue empty"
          }
          chart={<DailyLines data={queue.throughput} />}
          table={
            <DataTable
              caption="Review queue throughput"
              rows={queue.throughput}
              columns={[
                { key: "d", label: "Date", value: (x) => x.date },
                { key: "q", label: "Queued", value: (x) => x.queued, numeric: true },
                { key: "c", label: "Decided", value: (x) => x.decided, numeric: true },
              ]}
            />
          }
        />
        <section
          aria-labelledby="ev-h"
          className="rounded-2xl border border-slate-200 p-4 dark:border-slate-800"
        >
          <h2 id="ev-h" className="font-semibold">
            Latest evaluation
          </h2>
          {ev ? (
            <>
              <p className="text-xs text-slate-500">
                Run #{ev.id} · {new Date(ev.created_at).toLocaleString()} · {ev.summary.test_queries} TEST
                queries
              </p>
              <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
                <div>
                  <dt className="text-xs text-slate-500">Hybrid top-1</dt>
                  <dd className="text-xl font-bold tabular-nums">{pct(ev.summary.hybrid_top1)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">High-band precision</dt>
                  <dd className="text-xl font-bold tabular-nums">{pct(ev.summary.high_band_precision)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">Safety checks</dt>
                  <dd>
                    <StatusMark ok={ev.safety_ok} /> ({ev.summary.safety_failures} failing)
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">FHIR errors</dt>
                  <dd className="text-xl font-bold tabular-nums">
                    {ev.summary.fhir_errors} / {ev.summary.fhir_resources}
                  </dd>
                </div>
              </dl>
              <Link
                to="/evaluation"
                className="mt-3 inline-block text-sm font-semibold text-brand-700 underline dark:text-brand-300"
              >
                Full evaluation →
              </Link>
            </>
          ) : (
            <p className="mt-2 text-sm text-slate-500">No evaluation run stored yet.</p>
          )}
        </section>
      </div>

      <Exports />
    </div>
  );
}
