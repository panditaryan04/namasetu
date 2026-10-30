import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  adminUsers,
  backfillReview,
  getThresholds,
  putThresholds,
  releases,
  resetThresholds,
  startIngest,
  taskStatus,
  type TaskOut,
} from "../services/api";

const card = "rounded-2xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900";
const field =
  "mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950";
const btn =
  "rounded-lg bg-teal-700 px-3 py-2 text-sm font-semibold text-white hover:bg-teal-800 disabled:opacity-50";

function Thresholds() {
  const qc = useQueryClient();
  const t = useQuery({ queryKey: ["thresholds"], queryFn: getThresholds });
  const [high, setHigh] = useState("");
  const [low, setLow] = useState("");
  const [reason, setReason] = useState("");
  const save = useMutation({
    mutationFn: () => putThresholds({ high: Number(high), low: Number(low), reason }),
    onSuccess: () => qc.invalidateQueries(),
  });
  const reset = useMutation({ mutationFn: resetThresholds, onSuccess: () => qc.invalidateQueries() });
  return (
    <section className={card} aria-labelledby="th-h">
      <h2 id="th-h" className="font-semibold">
        Confidence gate
      </h2>
      {t.data && (
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          Active: T_HIGH {t.data.high} / T_LOW {t.data.low} ({t.data.source}). Calibrated:{" "}
          {t.data.calibrated.high} / {t.data.calibrated.low}.
        </p>
      )}
      <form className="mt-3 grid grid-cols-2 gap-3" onSubmit={(e) => (e.preventDefault(), save.mutate())}>
        <label className="text-sm">
          T_HIGH
          <input
            className={field}
            type="number"
            step="0.01"
            min="0.5"
            max="1"
            required
            value={high}
            onChange={(e) => setHigh(e.target.value)}
          />
        </label>
        <label className="text-sm">
          T_LOW
          <input
            className={field}
            type="number"
            step="0.01"
            min="0"
            max="1"
            required
            value={low}
            onChange={(e) => setLow(e.target.value)}
          />
        </label>
        <label className="col-span-2 text-sm">
          Reason (audited)
          <input
            className={field}
            required
            minLength={3}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </label>
        <div className="col-span-2 flex gap-2">
          <button className={btn} disabled={save.isPending}>
            Override
          </button>
          <button
            type="button"
            onClick={() => reset.mutate()}
            className="rounded-lg px-3 py-2 text-sm ring-1 ring-inset ring-slate-300 dark:ring-slate-600"
          >
            Reset to calibrated
          </button>
        </div>
        {save.isError && (
          <p role="alert" className="col-span-2 text-sm text-red-700">
            {save.error.message}
          </p>
        )}
      </form>
    </section>
  );
}

function Ingest() {
  const qc = useQueryClient();
  const [source, setSource] = useState<"namaste" | "icd11" | "synonyms">("synonyms");
  const [file, setFile] = useState<File | undefined>();
  const [version, setVersion] = useState("");
  const [task, setTask] = useState<TaskOut | null>(null);
  const start = useMutation({
    mutationFn: () => startIngest(source, file, version || undefined),
    onSuccess: setTask,
  });
  useEffect(() => {
    if (!task || ["SUCCESS", "FAILURE"].includes(task.state)) {
      if (task?.state === "SUCCESS") qc.invalidateQueries({ queryKey: ["releases"] });
      return;
    }
    const t = setTimeout(() => taskStatus(task.task_id).then(setTask), 1000);
    return () => clearTimeout(t);
  }, [task, qc]);
  return (
    <section className={card} aria-labelledby="in-h">
      <h2 id="in-h" className="font-semibold">
        Ingest dataset
      </h2>
      <form className="mt-3 space-y-3" onSubmit={(e) => (e.preventDefault(), start.mutate())}>
        <label className="block text-sm">
          Source
          <select
            className={field}
            value={source}
            onChange={(e) => setSource(e.target.value as typeof source)}
          >
            <option value="synonyms">Synonyms (YAML)</option>
            <option value="namaste">NAMASTE export (CSV/XLSX)</option>
            <option value="icd11">WHO ICD-11 (LIVE API or MOCK sample)</option>
          </select>
        </label>
        {source !== "icd11" && (
          <label className="block text-sm">
            File
            <input
              className={field}
              type="file"
              accept={source === "synonyms" ? ".yaml,.yml" : ".csv,.xlsx"}
              onChange={(e) => setFile(e.target.files?.[0])}
            />
          </label>
        )}
        {source === "namaste" && (
          <label className="block text-sm">
            Release version
            <input
              className={field}
              required
              value={version}
              onChange={(e) => setVersion(e.target.value)}
              placeholder="e.g. 2026-10"
            />
          </label>
        )}
        <button className={btn} disabled={start.isPending}>
          Start ingest
        </button>
      </form>
      {start.isError && (
        <p role="alert" className="mt-2 text-sm text-red-700">
          {start.error.message}
        </p>
      )}
      {task && (
        <p role="status" className="mt-3 text-sm">
          Task {task.task_id.slice(0, 8)}…: <strong>{task.state}</strong>
          {task.meta && ` · ${JSON.stringify(task.meta)}`}
          {task.result && ` · ${String(task.result.release)} ${String(task.result.status)}`}
        </p>
      )}
    </section>
  );
}

export default function Admin() {
  const qc = useQueryClient();
  const rel = useQuery({ queryKey: ["releases"], queryFn: releases });
  const users = useQuery({ queryKey: ["users"], queryFn: adminUsers });
  const backfill = useMutation({
    mutationFn: backfillReview,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["review"] }),
  });
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight">Admin</h1>
      <div className="grid gap-6 lg:grid-cols-2">
        <Thresholds />
        <Ingest />
      </div>
      <section className={card} aria-labelledby="rel-h">
        <div className="flex items-center gap-3">
          <h2 id="rel-h" className="mr-auto font-semibold">
            Dataset releases
          </h2>
          <button
            type="button"
            className={btn}
            onClick={() => backfill.mutate()}
            disabled={backfill.isPending}
          >
            Queue unmapped codes for review
          </button>
          {backfill.data && <span className="text-sm">queued {backfill.data.queued}</span>}
        </div>
        <ul className="mt-3 divide-y divide-slate-100 text-sm dark:divide-slate-800">
          {rel.data?.items.map((r) => (
            <li key={r.id} className="flex flex-wrap gap-x-4 py-2">
              <span className="font-mono">{r.tag}</span>
              <span>{r.row_count} rows</span>
              <span className="text-slate-500">
                {Object.entries(r.diff_summary)
                  .filter(([k]) => k !== "previous")
                  .map(([k, v]) => `${k} ${v}`)
                  .join(" · ")}
              </span>
              <span className="ml-auto text-xs text-slate-500">
                {r.ingested_by} · {r.ingested_at.slice(0, 19).replace("T", " ")}
              </span>
            </li>
          ))}
        </ul>
      </section>
      <section className={card} aria-labelledby="u-h">
        <h2 id="u-h" className="font-semibold">
          Users
        </h2>
        <ul className="mt-2 grid gap-2 text-sm sm:grid-cols-2">
          {users.data?.map((u) => (
            <li key={u.username} className="rounded-lg bg-slate-50 px-3 py-2 dark:bg-slate-950">
              <span className="font-medium">{u.display_name}</span> · {u.role}
              {u.abha_address && <span className="block text-xs text-slate-500">ABHA {u.abha_address}</span>}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
