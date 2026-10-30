import { useQuery } from "@tanstack/react-query";
import { getHealth } from "../services/api";

const chip =
  "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset";

/** Header badge showing LIVE / MOCK and SAMPLE DATA, straight from GET /health. */
export function ModeBadge() {
  const { data, isPending, isError } = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    refetchInterval: 30_000,
  });

  if (isPending) {
    return (
      <span className={`${chip} animate-pulse bg-slate-100 text-slate-500 ring-slate-200 dark:bg-slate-800`}>
        Checking…
      </span>
    );
  }
  if (isError) {
    return (
      <span
        role="status"
        className={`${chip} bg-red-50 text-red-800 ring-red-200 dark:bg-red-950 dark:text-red-200`}
      >
        API offline
      </span>
    );
  }

  const live = data.modes.ICD11_MODE === "live";
  return (
    <span className="flex flex-wrap items-center gap-1.5" role="status" aria-label="Runtime mode">
      <span
        className={`${chip} ${
          live
            ? "bg-teal-50 text-teal-800 ring-teal-200 dark:bg-teal-950 dark:text-teal-200"
            : "bg-saffron-50 text-saffron-800 ring-saffron-200 dark:bg-saffron-900/40 dark:text-saffron-200"
        }`}
        title={`ICD-11: ${data.modes.ICD11_MODE}, ABHA: ${data.modes.ABHA_MODE}`}
      >
        {live ? "LIVE" : "MOCK"}
      </span>
      {data.modes.DATA_MODE === "sample" && (
        <span
          className={`${chip} bg-slate-100 text-slate-700 ring-slate-300 dark:bg-slate-800 dark:text-slate-200`}
          title="SAMPLE — illustrative, not official"
        >
          SAMPLE DATA
        </span>
      )}
      {data.status === "degraded" && (
        <span className={`${chip} bg-red-50 text-red-800 ring-red-200`}>Degraded</span>
      )}
    </span>
  );
}
