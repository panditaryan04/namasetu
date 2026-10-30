import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { MedicationCard, ReferenceBanner } from "../components/MedicationCard";
import { checkMedications, medication, medicationSearch } from "../services/api";

function useDebounced(value: string, ms: number) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function Selected({ code, onAdd, added }: { code: string; onAdd: () => void; added: boolean }) {
  const m = useQuery({ queryKey: ["medication", code], queryFn: () => medication(code) });
  if (!m.data) return <div className="h-32 animate-pulse rounded-xl bg-slate-100 dark:bg-slate-900" />;
  return (
    <MedicationCard
      m={m.data}
      action={
        <button
          type="button"
          onClick={onAdd}
          disabled={added}
          className="rounded-lg px-3 py-1.5 text-sm font-semibold ring-1 ring-inset ring-teal-300 hover:bg-teal-50 disabled:opacity-60 dark:ring-teal-700 dark:hover:bg-teal-950"
        >
          {added ? "In check list" : "Add to check list"}
        </button>
      }
    />
  );
}

export default function MedicationSearch() {
  const [params, setParams] = useSearchParams();
  const [text, setText] = useState(params.get("q") ?? "");
  const q = useDebounced(text.trim(), 200);
  const [selected, setSelected] = useState<string | null>(null);
  const [list, setList] = useState<string[]>([]);
  const results = useQuery({
    queryKey: ["med-search", q],
    queryFn: () => medicationSearch(q),
    enabled: q.length >= 2,
  });
  const check = useMutation({ mutationFn: checkMedications });

  useEffect(() => {
    if (q) setParams({ q }, { replace: true });
  }, [q, setParams]);
  useEffect(() => {
    if (results.data?.brand.found && results.data.brand.medication_code)
      setSelected(results.data.brand.medication_code);
  }, [results.data]);
  useEffect(() => {
    if (list.length >= 2) check.mutate(list);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list]);

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-bold tracking-tight">What&apos;s inside this medicine?</h1>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          Type a brand, salt, generic, Ayush formulation or herb, in English, IAST, Devanagari or Hinglish.
        </p>
      </header>
      <ReferenceBanner />
      <label className="block">
        <span className="sr-only">Medicine, salt or herb</span>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="e.g. Combiflam · Dolo 650 · हरिद्रा · turmeric · acetaminophen · Sitopaladi"
          className="w-full rounded-2xl border border-slate-300 bg-white px-5 py-3.5 text-lg shadow-sm focus:border-teal-600 focus:outline-none focus:ring-4 focus:ring-teal-600/15 dark:border-slate-700 dark:bg-slate-900"
        />
      </label>

      {results.data && (
        <div className="grid gap-6 lg:grid-cols-[1fr_1.4fr]">
          <section aria-label="Results" className="space-y-2">
            <p role="status" className="text-sm">
              {results.data.brand.found ? (
                <>
                  Brand <strong>{results.data.brand.brand}</strong> ({results.data.brand.match} match)
                  {results.data.brand.strength_note && (
                    <span className="block text-saffron-800 dark:text-saffron-200">
                      ⚠ {results.data.brand.strength_note}
                    </span>
                  )}
                </>
              ) : (
                <span className="text-slate-600 dark:text-slate-400">
                  {results.data.brand.message} (brands are never guessed)
                </span>
              )}
            </p>
            <ul className="space-y-1">
              {results.data.hits.length === 0 && (
                <li className="text-sm text-slate-500">No match in the loaded datasets.</li>
              )}
              {results.data.hits.map((h) => (
                <li key={`${h.type}-${h.ref}`}>
                  {h.type === "ingredient" ? (
                    <Link
                      to={`/salts/${h.ref}`}
                      className="block rounded-xl px-3 py-2 text-sm ring-1 ring-inset ring-slate-200 hover:bg-slate-50 dark:ring-slate-700 dark:hover:bg-slate-900"
                    >
                      <span className="font-medium">{h.name}</span>{" "}
                      <span className="text-xs text-slate-500">
                        ({h.kind}) · matched “{h.matched}”
                      </span>
                    </Link>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setSelected(h.ref)}
                      aria-pressed={selected === h.ref}
                      className={`block w-full rounded-xl px-3 py-2 text-left text-sm ring-1 ring-inset ${selected === h.ref ? "bg-teal-50 ring-teal-300 dark:bg-teal-950 dark:ring-teal-800" : "ring-slate-200 hover:bg-slate-50 dark:ring-slate-700 dark:hover:bg-slate-900"}`}
                    >
                      <span className="font-medium">{h.name}</span>{" "}
                      <span className="text-xs text-slate-500">({h.type})</span>
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </section>
          <section aria-label="Breakdown">
            {selected ? (
              <Selected
                code={selected}
                added={list.includes(selected)}
                onAdd={() => setList((l) => [...l, selected])}
              />
            ) : (
              <p className="text-sm text-slate-500">Select a product to see its salts or composition.</p>
            )}
          </section>
        </div>
      )}

      {list.length > 0 && (
        <section
          aria-labelledby="check-h"
          className="rounded-2xl border border-slate-200 p-4 dark:border-slate-800"
        >
          <div className="flex items-center gap-3">
            <h2 id="check-h" className="mr-auto font-semibold">
              Check list ({list.length})
            </h2>
            <button type="button" onClick={() => setList([])} className="text-sm underline">
              Clear
            </button>
          </div>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">{list.join(" · ")}</p>
          {check.data && (
            <div className="mt-3 space-y-2" role="status">
              {check.data.duplicates.length === 0 && (
                <p className="text-sm">No duplicate salt among the selected products.</p>
              )}
              {check.data.duplicates.map((d) => (
                <p
                  key={d.ingredient_id}
                  className={`rounded-lg px-3 py-2 text-sm ${d.severity === "warning" ? "bg-red-50 font-semibold text-red-900 dark:bg-red-950 dark:text-red-100" : "bg-slate-100 dark:bg-slate-800"}`}
                >
                  {d.severity === "warning" ? "⚠ " : "ℹ "}
                  {d.message}
                </p>
              ))}
              {check.data.prescription_only.length > 0 && (
                <p className="text-sm">
                  Prescription-only (as stated by source): {check.data.prescription_only.join(", ")}
                </p>
              )}
              <p className="text-xs text-slate-500">{check.data.interaction_check}</p>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
