import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { mappingDetail, mappings } from "../services/api";

export default function Mappings() {
  const [params, setParams] = useSearchParams();
  const selected = Number(params.get("id")) || null;
  const list = useQuery({ queryKey: ["mappings"], queryFn: () => mappings({ limit: 500 }) });
  const detail = useQuery({
    queryKey: ["mapping", selected],
    queryFn: () => mappingDetail(selected!),
    enabled: selected !== null,
  });

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_420px]">
      <section aria-labelledby="map-heading">
        <h1 id="map-heading" className="text-2xl font-bold tracking-tight">
          Approved Mapping Store
        </h1>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          Only clinician-approved or official mappings. These feed the FHIR ConceptMap, $translate and
          Condition output.
        </p>
        <div className="mt-4 overflow-x-auto rounded-2xl border border-slate-200 dark:border-slate-800">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Approved mappings</caption>
            <thead className="bg-slate-50 text-xs uppercase tracking-wider text-slate-500 dark:bg-slate-900">
              <tr>
                <th className="px-3 py-2">NAMASTE</th>
                <th className="px-3 py-2">ICD-11 TM2</th>
                <th className="px-3 py-2">Rel.</th>
                <th className="px-3 py-2">Ver.</th>
                <th className="px-3 py-2">Reviewer</th>
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((m) => (
                <tr
                  key={m.id}
                  className={`border-t border-slate-100 dark:border-slate-800 ${m.id === selected ? "bg-teal-50 dark:bg-teal-950" : ""}`}
                >
                  <td className="px-3 py-2">
                    <button
                      type="button"
                      className="text-left font-medium underline-offset-2 hover:underline"
                      onClick={() => setParams({ id: String(m.id) })}
                    >
                      {m.source_display}
                    </button>
                    <span className="block font-mono text-xs text-slate-500">{m.source_code}</span>
                  </td>
                  <td className="px-3 py-2">
                    {m.target_display}
                    <span className="block font-mono text-xs text-slate-500">{m.target_code}</span>
                  </td>
                  <td className="px-3 py-2">{m.relationship}</td>
                  <td className="px-3 py-2">v{m.version}</td>
                  <td className="px-3 py-2">
                    {m.reviewer}
                    {m.needs_review && (
                      <span className="ml-1 rounded bg-saffron-100 px-1 text-xs text-saffron-900">
                        re-review
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs text-slate-500">{list.data?.total ?? 0} approved mapping(s)</p>
      </section>

      <aside aria-label="Provenance timeline" className="min-w-0">
        {detail.data ? (
          <div className="rounded-2xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900">
            <h2 className="font-semibold">
              {detail.data.source_display} → {detail.data.target_display}
            </h2>
            <p className="text-sm text-slate-600 dark:text-slate-400">
              {detail.data.relationship} · v{detail.data.version} · {detail.data.ranker_version ?? "official"}{" "}
              · {detail.data.dataset_releases.join(", ")}
            </p>
            <h3 className="mt-4 text-xs font-semibold uppercase tracking-wider text-slate-500">Versions</h3>
            <ul className="mt-1 text-sm">
              {detail.data.history.map((h) => (
                <li key={h.id}>
                  v{h.version} · {h.target_code} ({h.relationship}) · {h.status} · {h.reviewer}
                </li>
              ))}
            </ul>
            <h3 className="mt-4 text-xs font-semibold uppercase tracking-wider text-slate-500">
              Provenance timeline
            </h3>
            <ol className="mt-2 space-y-2 border-l-2 border-teal-200 pl-4 dark:border-teal-900">
              {detail.data.timeline.map((t, i) => (
                <li key={i} className="text-sm">
                  <span className="font-mono text-xs text-slate-500">
                    {t.ts.replace("T", " ").slice(0, 19)}
                  </span>
                  <span className="block">
                    <span className="font-medium">{t.action}</span> by {t.actor}{" "}
                    <span className="rounded bg-slate-100 px-1 text-[11px] dark:bg-slate-800">{t.kind}</span>
                  </span>
                  <span className="block text-xs text-slate-600 dark:text-slate-400">{t.detail}</span>
                </li>
              ))}
            </ol>
          </div>
        ) : (
          <div className="grid h-48 place-items-center rounded-2xl border border-dashed border-slate-300 text-sm text-slate-500 dark:border-slate-700">
            Select a mapping to see its versions and provenance.
          </div>
        )}
      </aside>
    </div>
  );
}
