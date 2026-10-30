import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { Autocomplete } from "../components/Autocomplete";
import { TriCodeCard } from "../components/TriCodeCard";
import { mapTerm, type MapResult } from "../services/api";

const STATUS_STYLE: Record<MapResult["status"], string> = {
  suggested:
    "border-teal-300 bg-teal-50 text-teal-900 dark:border-teal-800 dark:bg-teal-950 dark:text-teal-100",
  review:
    "border-saffron-300 bg-saffron-50 text-saffron-900 dark:border-saffron-700 dark:bg-saffron-900/30 dark:text-saffron-100",
  no_match:
    "border-slate-300 bg-slate-50 text-slate-800 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200",
};
const SCRIPT_LABEL: Record<string, string> = {
  devanagari: "Devanagari",
  iast: "IAST",
  "harvard-kyoto": "Harvard-Kyoto",
  latin: "English / Hinglish",
};

function Skeleton() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading mapping">
      {[0, 1].map((i) => (
        <div key={i} className="h-48 animate-pulse rounded-2xl bg-slate-100 dark:bg-slate-900" />
      ))}
    </div>
  );
}

export default function MappingResult() {
  const [params] = useSearchParams();
  const q = params.get("q") ?? "";
  const code = params.get("code") ?? "";

  const result = useQuery({
    queryKey: ["map", q, code],
    queryFn: () => mapTerm(code ? { code, text: q || undefined } : { text: q }),
    enabled: Boolean(q || code),
    staleTime: 60_000,
  });

  return (
    <div className="space-y-6">
      <div className="max-w-3xl">
        <Autocomplete key={q || code} initial={q} />
      </div>

      {!q && !code && (
        <p className="text-slate-600 dark:text-slate-400">
          Type a diagnosis above, or go back to <Link to="/">search</Link>.
        </p>
      )}
      {result.isPending && (q || code) && <Skeleton />}
      {result.isError && (
        <p
          role="alert"
          className="rounded-xl border border-red-200 bg-red-50 p-4 text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200"
        >
          Could not map this term: {result.error.message}
        </p>
      )}

      {result.data && (
        <>
          <section aria-labelledby="result-heading" className="space-y-3">
            <h1 id="result-heading" className="text-2xl font-bold tracking-tight">
              {result.data.resolved ? (
                <>
                  {result.data.resolved.display}{" "}
                  {result.data.resolved.devanagari && (
                    <span className="font-normal text-slate-600 dark:text-slate-400" lang="sa">
                      {result.data.resolved.devanagari}{" "}
                    </span>
                  )}
                  <span className="font-mono text-base font-normal text-slate-500">
                    {result.data.resolved.code}
                  </span>
                </>
              ) : (
                <>“{result.data.query.text}”</>
              )}
            </h1>
            <p className="text-sm text-slate-600 dark:text-slate-400">
              Query “{result.data.query.text}” · script {SCRIPT_LABEL[result.data.query.script]} · normalised{" "}
              <code className="rounded bg-slate-100 px-1 dark:bg-slate-800">
                {result.data.query.normalised}
              </code>
              {result.data.resolved &&
                result.data.resolved.method !== "typo" &&
                ` · resolved via ${result.data.resolved.method} match on “${result.data.resolved.matched_term}”`}
            </p>
            {result.data.resolved?.method === "typo" && (
              <p
                role="note"
                className="rounded-lg bg-saffron-50 px-3 py-2 text-sm text-saffron-900 dark:bg-saffron-900/30 dark:text-saffron-100"
              >
                Spelling corrected: read “{result.data.query.text}” as “{result.data.resolved.matched_term}”.
                Check this is the term you meant before confirming.
              </p>
            )}
            <div
              role="status"
              className={`rounded-xl border px-4 py-3 text-sm font-medium ${STATUS_STYLE[result.data.status]}`}
            >
              {result.data.message}
              {result.data.suppressed_low > 0 &&
                ` · ${result.data.suppressed_low} low-confidence candidate(s) withheld`}
            </div>
          </section>

          {!result.data.resolved && result.data.alternatives.length > 0 && (
            <section aria-label="Did you mean" className="text-sm">
              <span className="text-slate-600 dark:text-slate-400">NAMASTE concepts matching “{q}”: </span>
              <span className="inline-flex flex-wrap gap-2">
                {result.data.alternatives.map((s) => (
                  <Link
                    key={s.code}
                    to={`/map?code=${encodeURIComponent(s.code)}&q=${encodeURIComponent(s.display)}`}
                    className="rounded-full bg-white px-3 py-1 font-medium text-teal-800 ring-1 ring-inset ring-teal-200 hover:bg-teal-50 dark:bg-slate-900 dark:text-teal-200 dark:ring-teal-800"
                  >
                    {s.display}
                    {s.devanagari && <span className="ml-1 text-slate-500"> {s.devanagari}</span>}
                  </Link>
                ))}
              </span>
            </section>
          )}

          <div className="space-y-4">
            {result.data.candidates.map((c, i) => (
              <TriCodeCard
                key={c.tm2.code}
                c={c}
                rank={i + 1}
                thresholds={result.data.thresholds}
                semanticMethod={result.data.semantic_method}
                namasteDevanagari={result.data.resolved?.devanagari}
              />
            ))}
          </div>

          <p className="text-xs text-slate-500">
            Thresholds T_HIGH {result.data.thresholds.high} / T_LOW {result.data.thresholds.low} (
            {result.data.thresholds.source}) · {result.data.model_version} ·{" "}
            {result.data.dataset_releases.join(" · ")} · SAMPLE data is illustrative, not official.
          </p>
        </>
      )}
    </div>
  );
}
