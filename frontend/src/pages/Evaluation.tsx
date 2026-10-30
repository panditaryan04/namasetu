import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  ChartCard,
  DataTable,
  RateBars,
  ReliabilityChart,
  StatTile,
  StatusMark,
  type Column,
} from "../components/charts";
import { ApiError, evalTask, latestEvalRun, startEvalRun, type RankMetrics } from "../services/api";
import { num, pct } from "../services/format";
import { can } from "../services/auth";
import { useAuth } from "../services/useAuth";

type Row = RankMetrics & { name: string };
const rows = (m: Record<string, RankMetrics>): Row[] =>
  Object.entries(m).map(([name, v]) => ({ name, ...v }));
const ci = (v: RankMetrics["top1_ci95"]) => (v ? `${pct(v[0])}–${pct(v[1])}` : "—");

const RANK_COLUMNS: Column<Row>[] = [
  { key: "name", label: "System", value: (r) => r.name },
  { key: "n", label: "n", value: (r) => r.queries, numeric: true },
  { key: "top1", label: "Top-1", value: (r) => pct(r.top1), numeric: true },
  { key: "ci", label: "Top-1 95% CI", value: (r) => ci(r.top1_ci95), numeric: true },
  { key: "top5", label: "Top-5", value: (r) => pct(r.top5), numeric: true },
  { key: "mrr", label: "MRR", value: (r) => num(r.mrr), numeric: true },
];
const SHOWN_COLUMN: Column<Row> = {
  key: "shown",
  label: "Correct & shown",
  value: (r) => pct(r.top1_shown),
  numeric: true,
};
const GATE_COLUMNS: Column<Row>[] = [
  { key: "hbp", label: "High-band precision", value: (r) => pct(r.high_band_precision), numeric: true },
  { key: "nm", label: "No match", value: (r) => pct(r.no_match_rate), numeric: true },
  SHOWN_COLUMN,
];

function RunButton() {
  const qc = useQueryClient();
  const [taskId, setTaskId] = useState<string | null>(null);
  const start = useMutation({
    mutationFn: startEvalRun,
    onSuccess: (r) =>
      r.eval_run_id ? qc.invalidateQueries({ queryKey: ["eval-latest"] }) : setTaskId(r.task_id),
  });
  const task = useQuery({
    queryKey: ["eval-task", taskId],
    queryFn: () => evalTask(taskId!),
    enabled: Boolean(taskId),
    refetchInterval: (q) => (q.state.data?.eval_run_id || q.state.data?.state === "FAILURE" ? false : 3000),
  });
  useEffect(() => {
    if (task.data?.eval_run_id) {
      setTaskId(null);
      qc.invalidateQueries({ queryKey: ["eval-latest"] });
    }
  }, [task.data, qc]);
  const running = start.isPending || Boolean(taskId);
  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        disabled={running}
        onClick={() => start.mutate()}
        className="rounded-lg bg-brand-700 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"
      >
        {running ? "Running evaluation…" : "Run evaluation"}
      </button>
      {start.isError && (
        <span role="alert" className="text-sm text-red-700 dark:text-red-400">
          {(start.error as Error).message}
        </span>
      )}
    </div>
  );
}

function Failures({ systems }: { systems: Record<string, RankMetrics> }) {
  const names = Object.keys(systems);
  const [sel, setSel] = useState(names[names.length - 1] ?? "");
  const list = systems[sel]?.failures ?? [];
  return (
    <section
      aria-labelledby="fail-h"
      className="rounded-2xl border border-slate-200 p-4 dark:border-slate-800"
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 id="fail-h" className="font-semibold">
          Failure drill-down
        </h2>
        <label className="text-sm">
          <span className="mr-2 text-slate-500">Source</span>
          <select
            value={sel}
            onChange={(e) => setSel(e.target.value)}
            className="rounded-lg border border-slate-300 px-2 py-1 dark:border-slate-700 dark:bg-slate-950"
          >
            {names.map((n) => (
              <option key={n}>{n}</option>
            ))}
          </select>
        </label>
      </div>
      {list.length === 0 ? (
        <p className="text-sm text-slate-500">No failures: every query ranked the gold code first.</p>
      ) : (
        <DataTable
          caption={`Failures of ${sel}`}
          rows={list}
          columns={[
            { key: "q", label: "Query", value: (f) => f.query },
            { key: "g", label: "Gold", value: (f) => <code>{f.gold}</code> },
            { key: "r", label: "Rank", value: (f) => f.rank ?? "not ranked", numeric: true },
            { key: "t", label: "Top candidates", value: (f) => f.top.join(", ") || "none" },
          ]}
        />
      )}
    </section>
  );
}

export default function Evaluation() {
  const user = useAuth();
  const run = useQuery({ queryKey: ["eval-latest"], queryFn: latestEvalRun, retry: false });
  const missing = run.error instanceof ApiError && run.error.status === 404;

  const header = (
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Evaluation</h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Every number on this page comes from a stored evaluation run of the code against the gold sets.
        </p>
      </div>
      {can(user, "admin") && <RunButton />}
    </header>
  );
  if (run.isPending) return <div className="h-48 animate-pulse rounded-2xl bg-slate-100 dark:bg-slate-900" />;
  if (missing || !run.data?.results)
    return (
      <div className="space-y-4">
        {header}
        <p role="status" className="rounded-xl bg-slate-50 p-4 text-sm dark:bg-slate-900">
          No evaluation run is stored yet.{" "}
          {can(user, "admin") ? "Use “Run evaluation”." : "An admin can start one, or run `make eval-db`."}
        </p>
      </div>
    );

  const r = run.data.results;
  const s = run.data.summary;
  const systems = rows(r.intercomparison.systems);
  const ablation = rows(r.ablation);
  const robust = rows(r.robustness);
  const cal = r.calibration;
  const checks = Object.entries(r.safety.checks);
  const hybrid = r.intercomparison.systems.Hybrid;

  return (
    <div className="space-y-6">
      {header}
      <p className="text-xs text-slate-500">
        Run #{run.data.id} · {new Date(run.data.created_at).toLocaleString()} by {run.data.created_by} · model{" "}
        <code>{run.data.model_version}</code> · semantic stage <code>{s.semantic_method}</code> · releases{" "}
        {run.data.dataset_releases.join(", ")} · {run.data.duration_s}s
      </p>
      {s.is_sample !== false && (
        <p className="rounded-xl bg-saffron-50 p-3 text-sm text-saffron-900 dark:bg-saffron-900/30 dark:text-saffron-100">
          SAMPLE — illustrative, not official. {r.split.test} held-out TEST queries ({r.split.train} train).
          The gold sets were written by the team, so these figures show the pipeline works end to end; they
          are not independent accuracy. Read the 95% intervals.
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          label="Hybrid top-1 (TEST)"
          value={pct(s.hybrid_top1)}
          detail={`95% CI ${ci(hybrid?.top1_ci95)}`}
        />
        <StatTile
          label="Exact-match baseline top-1"
          value={pct(s.exact_top1)}
          detail={`${s.test_queries} queries`}
        />
        <StatTile
          label="Safety checks failing"
          value={`${s.safety_failures} / ${checks.length}`}
          detail={<StatusMark ok={run.data.safety_ok} />}
        />
        <StatTile
          label="FHIR resources validated"
          value={s.fhir_resources}
          detail={`${s.fhir_errors} errors`}
        />
      </div>

      <ChartCard
        title="Intercomparison (TEST split)"
        note="Stage-0 crosswalk baseline off for every system; the ranker never saw these rows."
        chart={
          <RateBars
            data={systems}
            series={[
              { key: "top1", label: "Top-1" },
              { key: "top5", label: "Top-5" },
              { key: "mrr", label: "MRR" },
            ]}
          />
        }
        table={<DataTable caption="Intercomparison" rows={systems} columns={RANK_COLUMNS} />}
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <ChartCard
          title="Ablation (ranker retrained without each stage)"
          note="Top-1 and “correct & shown” (gold ranked first and in the high or medium band)."
          chart={
            <RateBars
              data={ablation}
              series={[
                { key: "top1", label: "Top-1" },
                { key: "top1_shown", label: "Correct & shown" },
              ]}
            />
          }
          table={
            <DataTable
              caption="Ablation"
              rows={ablation}
              columns={[...RANK_COLUMNS.slice(0, 4), ...GATE_COLUMNS]}
            />
          }
        />
        <ChartCard
          title="Robustness by script (TEST split)"
          note="Free-text queries incl. resolution to the NAMASTE concept; typos are seeded 1–2 character edits."
          chart={
            <RateBars
              data={robust}
              series={[
                { key: "top1", label: "Top-1" },
                { key: "top1_shown", label: "Correct & shown" },
              ]}
            />
          }
          table={
            <DataTable
              caption="Robustness by script"
              rows={robust}
              columns={[
                ...RANK_COLUMNS.slice(0, 4),
                {
                  key: "res",
                  label: "Resolved to NAMASTE",
                  value: (x) => pct(x.resolved_to_correct_namaste),
                  numeric: true,
                },
                SHOWN_COLUMN,
              ]}
            />
          }
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <ChartCard
          title="Calibration (reliability, TEST pairs)"
          note={`T_HIGH ${cal.thresholds.high} · T_LOW ${cal.thresholds.low} · ECE ${num(cal.test_expected_calibration_error)} · ${cal.test_pairs} pairs. Dot size = pairs in the bin; dashed line = perfect calibration.`}
          chart={<ReliabilityChart bins={cal.test_reliability} />}
          table={
            <DataTable
              caption="Reliability bins"
              rows={cal.test_reliability.filter((b) => b.count > 0)}
              columns={[
                { key: "b", label: "Bin", value: (b) => `${b.lower.toFixed(1)}–${b.upper.toFixed(1)}` },
                { key: "c", label: "Pairs", value: (b) => b.count, numeric: true },
                { key: "p", label: "Mean predicted", value: (b) => num(b.mean_predicted), numeric: true },
                { key: "o", label: "Observed", value: (b) => num(b.observed), numeric: true },
              ]}
            />
          }
        />
        <section
          aria-labelledby="safety-h"
          className="rounded-2xl border border-slate-200 p-4 dark:border-slate-800"
        >
          <h2 id="safety-h" className="font-semibold">
            Safety checks (each must be 0)
          </h2>
          <p className="mb-2 text-xs text-slate-500">
            {Object.entries(r.safety.scope)
              .map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`)
              .join(" · ")}
          </p>
          <DataTable
            caption="Safety checks"
            rows={checks}
            columns={[
              { key: "c", label: "Check", value: ([k]) => k.replace(/_/g, " ") },
              { key: "n", label: "Failures", value: ([, v]) => v, numeric: true },
              { key: "s", label: "Status", value: ([, v]) => <StatusMark ok={v === 0} /> },
            ]}
          />
        </section>
      </div>

      <section
        aria-labelledby="fhir-h"
        className="rounded-2xl border border-slate-200 p-4 dark:border-slate-800"
      >
        <h2 id="fhir-h" className="font-semibold">
          FHIR conformance (in-process structural + binding validation)
        </h2>
        <DataTable
          caption="FHIR conformance"
          rows={Object.entries(r.safety.fhir_conformance)}
          columns={[
            { key: "t", label: "Resource", value: ([k]) => k },
            { key: "n", label: "Count", value: ([, v]) => v.resources, numeric: true },
            { key: "e", label: "Errors", value: ([, v]) => v.errors, numeric: true },
            { key: "w", label: "Warnings", value: ([, v]) => v.warnings, numeric: true },
          ]}
        />
      </section>

      <section
        aria-labelledby="med-h"
        className="rounded-2xl border border-slate-200 p-4 dark:border-slate-800"
      >
        <h2 id="med-h" className="font-semibold">
          Medications (reference only)
        </h2>
        <div className="mt-2 grid gap-6 lg:grid-cols-3">
          <DataTable
            caption="Brand resolution"
            rows={Object.entries(r.medication.brands).filter(
              ([, v]) => typeof v === "object" && "cases" in v,
            )}
            columns={[
              { key: "k", label: "Brand resolution", value: ([k]) => k },
              { key: "n", label: "Cases", value: ([, v]) => v.cases, numeric: true },
              {
                key: "a",
                label: "Result",
                value: ([, v]) =>
                  v.false_resolutions != null ? `${v.false_resolutions} false` : pct(v.accuracy),
                numeric: true,
              },
            ]}
          />
          <DataTable
            caption="Ingredient names"
            rows={Object.entries(r.medication.ingredient_names.groups)}
            columns={[
              { key: "k", label: "Ingredient names", value: ([k]) => k },
              { key: "n", label: "Cases", value: ([, v]) => v.cases, numeric: true },
              { key: "a", label: "Accuracy", value: ([, v]) => pct(v.accuracy), numeric: true },
            ]}
          />
          <div className="space-y-3">
            <DataTable
              caption="Interaction alerts"
              rows={Object.entries(r.medication.interactions.by_kind)}
              columns={[
                { key: "k", label: "Alerts", value: ([k]) => k },
                { key: "tp", label: "TP", value: ([, v]) => v.true_positives, numeric: true },
                { key: "fp", label: "FP", value: ([, v]) => v.false_positives, numeric: true },
                { key: "fn", label: "FN", value: ([, v]) => v.false_negatives, numeric: true },
                { key: "p", label: "Precision", value: ([, v]) => pct(v.precision), numeric: true },
                { key: "r", label: "Recall", value: ([, v]) => pct(v.recall), numeric: true },
              ]}
            />
            <p className="text-sm">
              Free-text medication lines resolved: {r.medication.line_resolution.correct}/
              {r.medication.line_resolution.cases}
            </p>
          </div>
        </div>
      </section>

      <Failures systems={{ ...r.intercomparison.systems, ...prefix(r.robustness, "Robustness: ") }} />
    </div>
  );
}

function prefix(m: Record<string, RankMetrics>, p: string): Record<string, RankMetrics> {
  return Object.fromEntries(Object.entries(m).map(([k, v]) => [p + k, v]));
}
