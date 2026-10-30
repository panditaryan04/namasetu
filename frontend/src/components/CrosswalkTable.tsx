import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { conceptMap, type ConceptMapTarget } from "../services/api";

/** Plain-language names for FHIR ConceptMap relationships. */
const RELATION: Record<string, string> = {
  equivalent: "Equivalent",
  equal: "Equivalent",
  wider: "Broader",
  narrower: "Narrower",
  relatedto: "Related to (not equivalent)",
  "related-to": "Related to (not equivalent)",
  inexact: "Related to (not equivalent)",
};
const rel = (t?: ConceptMapTarget) => {
  const r = t?.equivalence ?? t?.relationship ?? "";
  return RELATION[r] ?? r;
};

export interface CrosswalkRow {
  namaste: { code: string; display: string };
  tm2: { code: string; display: string; relation: string };
  bio: { code: string; display: string; relation: string } | null;
}

/** Joins the two published ConceptMaps: NAMASTE -> ICD-11 TM2, then TM2 -> ICD-11 biomedicine. */
function useCrosswalk() {
  const toTm2 = useQuery({
    queryKey: ["conceptmap", "namaste-to-tm2"],
    queryFn: () => conceptMap("namaste-to-tm2"),
  });
  const toBio = useQuery({
    queryKey: ["conceptmap", "tm2-to-biomedicine"],
    queryFn: () => conceptMap("tm2-to-biomedicine"),
  });
  const rows = useMemo<CrosswalkRow[]>(() => {
    const bio = new Map<string, ConceptMapTarget>();
    for (const g of toBio.data?.group ?? [])
      for (const e of g.element ?? []) if (e.target?.[0]) bio.set(e.code, e.target[0]);
    const out: CrosswalkRow[] = [];
    for (const g of toTm2.data?.group ?? [])
      for (const e of g.element ?? []) {
        const t = e.target?.[0];
        if (!t) continue;
        const b = bio.get(t.code);
        out.push({
          namaste: { code: e.code, display: e.display ?? "" },
          tm2: { code: t.code, display: t.display ?? "", relation: rel(t) },
          bio: b ? { code: b.code, display: b.display ?? "", relation: rel(b) } : null,
        });
      }
    return out;
  }, [toTm2.data, toBio.data]);
  return {
    rows,
    isLoading: toTm2.isLoading || toBio.isLoading,
    error: toTm2.error ?? toBio.error,
    versions: [toTm2.data?.version, toBio.data?.version].filter(Boolean) as string[],
  };
}

const code = "font-mono text-[13px] font-semibold";

export function CrosswalkTable({
  onPick,
  selected,
}: {
  onPick?: (row: CrosswalkRow) => void;
  selected?: string;
}) {
  const { rows, isLoading, error, versions } = useCrosswalk();
  const [q, setQ] = useState("");
  const shown = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return rows;
    return rows.filter((r) =>
      [r.namaste.code, r.namaste.display, r.tm2.code, r.tm2.display, r.bio?.code, r.bio?.display]
        .join(" ")
        .toLowerCase()
        .includes(s),
    );
  }, [rows, q]);
  const withBio = rows.filter((r) => r.bio).length;

  if (error) return <p role="alert">Could not load the crosswalk: {(error as Error).message}</p>;
  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <p className="text-sm text-slate-600 dark:text-slate-400" role="status">
          {isLoading
            ? "Loading the published ConceptMaps…"
            : `${rows.length} NAMASTE terms mapped to WHO ICD-11 TM2; ${withBio} of them also linked to an ICD-11 biomedicine code.`}
        </p>
        <label className="text-sm font-medium">
          Filter
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Code or term, e.g. Amavata or TM2"
            className="ml-2 w-64 rounded-lg border border-slate-300 bg-white px-3 py-1.5 dark:border-slate-700 dark:bg-slate-950"
          />
        </label>
      </div>
      <div className="mt-3 overflow-x-auto rounded-xl ring-1 ring-slate-200 dark:ring-slate-800">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">AYUSH (NAMASTE) codes and their WHO ICD-11 equivalents</caption>
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-600 dark:bg-slate-900 dark:text-slate-400">
            <tr>
              <th className="px-3 py-2">AYUSH · NAMASTE</th>
              <th className="px-3 py-2">Relation</th>
              <th className="px-3 py-2">WHO ICD-11 · TM2 (Chapter 26)</th>
              <th className="px-3 py-2">Relation</th>
              <th className="px-3 py-2">WHO ICD-11 · Biomedicine</th>
              {onPick && (
                <th className="px-3 py-2">
                  <span className="sr-only">Action</span>
                </th>
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {shown.map((r) => (
              <tr
                key={r.namaste.code}
                className={selected === r.namaste.code ? "bg-brand-50 dark:bg-brand-950/50" : undefined}
              >
                <td className="px-3 py-2">
                  <span className={`${code} text-emerald-800 dark:text-emerald-300`}>{r.namaste.code}</span>
                  <span className="block">{r.namaste.display}</span>
                </td>
                <td className="px-3 py-2 text-xs">→ {r.tm2.relation}</td>
                <td className="px-3 py-2">
                  <span className={`${code} text-sky-800 dark:text-sky-300`}>{r.tm2.code}</span>
                  <span className="block">{r.tm2.display}</span>
                </td>
                <td className="px-3 py-2 text-xs">{r.bio ? `→ ${r.bio.relation}` : ""}</td>
                <td className="px-3 py-2">
                  {r.bio ? (
                    <>
                      <span className={`${code} text-amber-800 dark:text-amber-300`}>{r.bio.code}</span>
                      <span className="block">{r.bio.display}</span>
                    </>
                  ) : (
                    <span className="text-xs text-slate-600 dark:text-slate-400">
                      No approved biomedicine link
                    </span>
                  )}
                </td>
                {onPick && (
                  <td className="px-3 py-2 text-right">
                    <button
                      type="button"
                      onClick={() => onPick(r)}
                      className="rounded-lg px-2.5 py-1 text-xs font-semibold text-brand-800 ring-1 ring-inset ring-brand-200 hover:bg-brand-50 dark:text-brand-200 dark:ring-brand-800 dark:hover:bg-brand-950"
                    >
                      Use<span className="sr-only"> {r.namaste.display}</span>
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!isLoading && !shown.length && <p className="mt-2 text-sm">No mapping matches “{q}”.</p>}
      <p className="mt-2 text-xs text-slate-600 dark:text-slate-400">
        Source: the FHIR ConceptMaps NamaSetu publishes (approved mappings only)
        {versions.length ? ` · versions ${versions.join(", ")}` : ""}. Biomedicine links come from the WHO
        index or a clinician and are “related to”, not equivalent. Codes starting with SAMPLE- are
        illustrative, not official.
      </p>
    </div>
  );
}
