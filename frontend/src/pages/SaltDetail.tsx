import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { MedicationCard, ReferenceBanner } from "../components/MedicationCard";
import { ingredient } from "../services/api";

const KIND_LABEL: Record<string, string> = {
  salt: "Salt (biomedical active ingredient)",
  herb: "Herb",
  mineral: "Processed metal / mineral",
  other: "Other ingredient",
};

export default function SaltDetail() {
  const { id } = useParams();
  const d = useQuery({
    queryKey: ["ingredient", id],
    queryFn: () => ingredient(Number(id)),
    enabled: Boolean(Number(id)),
  });
  if (d.isPending) return <div className="h-40 animate-pulse rounded-2xl bg-slate-100 dark:bg-slate-900" />;
  if (d.isError || !d.data) return <p role="alert">Ingredient not found.</p>;
  const i = d.data;
  return (
    <div className="space-y-5">
      <header>
        <p className="text-xs uppercase tracking-wider text-slate-500">{KIND_LABEL[i.kind] ?? i.kind}</p>
        <h1 className="text-2xl font-bold tracking-tight">
          {i.canonical_name}
          {i.devanagari && (
            <span className="ml-2 font-normal text-slate-600 dark:text-slate-400" lang="sa">
              {i.devanagari}
            </span>
          )}
        </h1>
        {i.botanical_name && <p className="italic text-slate-700 dark:text-slate-300">{i.botanical_name}</p>}
        {i.metal_mineral && (
          <p className="mt-2 rounded-lg bg-saffron-50 px-3 py-2 text-sm text-saffron-900 dark:bg-saffron-900/30 dark:text-saffron-100">
            Processed metal / mineral preparation: use only under qualified supervision.
          </p>
        )}
      </header>
      <ReferenceBanner />
      <section aria-labelledby="syn-h">
        <h2 id="syn-h" className="text-sm font-semibold uppercase tracking-wider text-slate-500">
          Also known as
        </h2>
        <ul className="mt-1 flex flex-wrap gap-1.5 text-sm">
          {[i.iast, ...i.synonyms.map((s) => s.term)].filter(Boolean).map((t) => (
            <li key={t} className="rounded-full bg-slate-100 px-2.5 py-0.5 dark:bg-slate-800">
              {t}
            </li>
          ))}
        </ul>
      </section>
      <section aria-labelledby="med-h">
        <h2 id="med-h" className="text-sm font-semibold uppercase tracking-wider text-slate-500">
          Medications containing it ({i.medications.length})
        </h2>
        <div className="mt-2 grid gap-2 lg:grid-cols-2">
          {i.medications.map((m) => (
            <MedicationCard key={m.code} m={m} />
          ))}
        </div>
      </section>
      <section aria-labelledby="cond-h">
        <h2 id="cond-h" className="text-sm font-semibold uppercase tracking-wider text-slate-500">
          Conditions those medications are linked to (approved links)
        </h2>
        <ul className="mt-1 space-y-1 text-sm">
          {i.conditions.length === 0 && <li className="text-slate-500">None.</li>}
          {i.conditions.map((c) => (
            <li key={`${c.system}-${c.code}`}>
              {c.system === "namaste" ? (
                <Link
                  to={`/map?code=${encodeURIComponent(c.code)}`}
                  className="underline-offset-2 hover:underline"
                >
                  {c.display}
                </Link>
              ) : (
                c.display
              )}{" "}
              <span className="font-mono text-xs text-slate-500">
                {c.system} {c.code}
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
