import type { Explanation } from "../services/api";

const STAGES: { key: keyof Explanation["scores"]; label: string; hint: string }[] = [
  { key: "exact", label: "Exact", hint: "Official crosswalk, or an identical normalised term" },
  { key: "lexical", label: "Lexical", hint: "Fuzzy token match (RapidFuzz) on folded keys" },
  { key: "ngram", label: "Char n-gram", hint: "TF-IDF cosine over character 3–4-grams" },
  { key: "transliteration", label: "Transliteration", hint: "Same word across scripts / spellings" },
  { key: "semantic", label: "Semantic", hint: "Meaning overlap (method shown below)" },
  { key: "hierarchy", label: "Hierarchy", hint: "Parent categories agree" },
];

function Chips({ items, tone = "slate" }: { items: string[]; tone?: "slate" | "teal" | "saffron" }) {
  const cls = {
    slate: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200",
    teal: "bg-teal-50 text-teal-800 dark:bg-teal-950 dark:text-teal-200",
    saffron: "bg-saffron-50 text-saffron-900 dark:bg-saffron-900/30 dark:text-saffron-100",
  }[tone];
  if (!items.length) return <span className="text-xs text-slate-500">none</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {items.map((t) => (
        <span key={t} className={`rounded-md px-1.5 py-0.5 font-mono text-xs ${cls}`}>
          {t}
        </span>
      ))}
    </span>
  );
}

/** "Why this match?" — every number and string here comes straight from the Explanation Object. */
export function EvidencePanel({ c, semanticMethod }: { c: Explanation; semanticMethod: string }) {
  const ev = c.evidence;
  return (
    <div className="mt-4 grid gap-5 border-t border-slate-200 pt-4 text-sm dark:border-slate-800 md:grid-cols-2">
      <section aria-label="Per-stage scores">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
          Per-stage scores
        </h3>
        <dl className="mt-2 space-y-1.5">
          {STAGES.map(({ key, label, hint }) => {
            const v = c.scores[key];
            const hit = ev.stages_hit.includes(key);
            return (
              <div key={key} className="grid grid-cols-[110px_1fr_44px] items-center gap-2" title={hint}>
                <dt className={hit ? "font-medium text-slate-900 dark:text-slate-100" : "text-slate-500"}>
                  {label}
                  {hit && <span className="sr-only"> (stage fired)</span>}
                </dt>
                <dd className="h-1.5 rounded-full bg-slate-200 dark:bg-slate-800" aria-hidden="true">
                  <div
                    className={`h-1.5 rounded-full ${hit ? "bg-teal-600" : "bg-slate-400"}`}
                    style={{ width: `${Math.round(v * 100)}%` }}
                  />
                </dd>
                <dd className="text-right font-mono text-xs">{v.toFixed(2)}</dd>
              </div>
            );
          })}
          <div className="grid grid-cols-[110px_1fr_44px] items-center gap-2 border-t border-slate-200 pt-1.5 dark:border-slate-800">
            <dt className="font-semibold">Final</dt>
            <dd className="text-xs text-slate-500">ranker blend {ev.baseline ? "· Stage-0 baseline" : ""}</dd>
            <dd className="text-right font-mono text-xs font-semibold">{c.scores.final.toFixed(2)}</dd>
          </div>
        </dl>
        <p className="mt-2 text-xs text-slate-500">
          Semantic method: <span className="font-mono">{semanticMethod}</span>
        </p>
      </section>

      <section aria-label="Evidence" className="space-y-3">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
          Evidence
        </h3>
        {ev.matched_terms.length > 0 && (
          <table className="w-full text-left text-xs">
            <caption className="sr-only">Matched terms</caption>
            <thead className="text-slate-500">
              <tr>
                <th className="pb-1 font-medium">Source</th>
                <th className="pb-1 font-medium">Target</th>
                <th className="pb-1 font-medium">Via</th>
              </tr>
            </thead>
            <tbody>
              {ev.matched_terms.map((m, i) => (
                <tr key={i} className="border-t border-slate-100 dark:border-slate-800">
                  <td className="py-1 pr-2">{m.source}</td>
                  <td className="py-1 pr-2">{m.target}</td>
                  <td className="py-1 font-mono text-[11px] text-slate-600 dark:text-slate-400">{m.via}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="grid grid-cols-[130px_1fr] gap-x-2 gap-y-1.5 text-xs">
          <span className="text-slate-500">Root terms</span>
          <Chips items={ev.matched_root_terms} tone="saffron" />
          <span className="text-slate-500">Shared concepts</span>
          <Chips items={ev.shared_concepts} tone="teal" />
          <span className="text-slate-500">Matched tokens</span>
          <Chips items={ev.matched_tokens} />
          <span className="text-slate-500">Entity type</span>
          <span>{ev.entity_type_check}</span>
          <span className="text-slate-500">Stages fired</span>
          <Chips items={ev.stages_hit} tone="teal" />
        </div>
      </section>

      <section aria-label="Reasoning" className="md:col-span-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
          Reasoning (template from evidence, no AI text)
        </h3>
        <p className="mt-1 text-slate-700 dark:text-slate-300">{c.reasoning}</p>
        <p className="mt-2 font-mono text-[11px] text-slate-500">
          {c.model_version} · {c.dataset_releases.join(" · ")}
        </p>
      </section>
    </div>
  );
}
