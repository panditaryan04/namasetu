import { useQuery } from "@tanstack/react-query";
import { getHealth } from "../services/api";

function Row({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-4 py-1.5">
      <dt className="text-slate-600 dark:text-slate-400">{label}</dt>
      <dd className="flex items-center gap-2 font-mono text-xs text-slate-900 dark:text-slate-100">
        {ok !== undefined && (
          <span aria-hidden="true" className={`h-2 w-2 rounded-full ${ok ? "bg-brand-500" : "bg-red-500"}`} />
        )}
        {value}
        {ok !== undefined && <span className="sr-only">{ok ? "(healthy)" : "(failing)"}</span>}
      </dd>
    </div>
  );
}

export function SystemStatus() {
  const { data, isPending, isError, error } = useQuery({ queryKey: ["health"], queryFn: getHealth });

  return (
    <section
      aria-labelledby="status-heading"
      className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900"
    >
      <h2 id="status-heading" className="text-sm font-semibold text-slate-900 dark:text-slate-100">
        System status
      </h2>
      {isPending && (
        <div className="mt-3 space-y-2" aria-busy="true">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-4 animate-pulse rounded bg-slate-100 dark:bg-slate-800" />
          ))}
        </div>
      )}
      {isError && (
        <p role="alert" className="mt-3 text-sm text-red-700 dark:text-red-300">
          Cannot reach the API: {error.message}
        </p>
      )}
      {data && (
        <dl className="mt-2 divide-y divide-slate-100 text-sm dark:divide-slate-800">
          <Row label="API" value={`v${data.version} · ${data.environment}`} ok={data.status === "ok"} />
          <Row label="Database" value={data.database.mode} ok={data.database.ok} />
          <Row label="Task queue" value={data.redis.mode} ok={data.redis.ok} />
          <Row label="Search index" value={data.index.loaded ? "loaded" : data.index.detail} />
          <Row label="Semantic" value={data.modes.SEMANTIC_MODE} />
          <Row label="ICD-11 API" value={data.modes.ICD11_MODE} />
          <Row label="ABHA auth" value={data.modes.ABHA_MODE} />
        </dl>
      )}
      {data && (
        <>
          <h3 className="mt-4 text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
            Dataset releases
          </h3>
          {Object.keys(data.dataset_versions).length === 0 ? (
            <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">No datasets ingested yet.</p>
          ) : (
            <dl className="mt-1 divide-y divide-slate-100 text-sm dark:divide-slate-800">
              {Object.entries(data.dataset_versions).map(([source, version]) => (
                <Row key={source} label={source} value={version} />
              ))}
            </dl>
          )}
          {data.modes.DATA_MODE === "sample" && (
            <p className="mt-3 rounded-lg bg-saffron-50 px-3 py-2 text-xs text-saffron-900 dark:bg-saffron-900/30 dark:text-saffron-100">
              SAMPLE — illustrative, not official. Codes prefixed <code>SAMPLE-</code> are not real NAMASTE or
              WHO codes.
            </p>
          )}
        </>
      )}
    </section>
  );
}
