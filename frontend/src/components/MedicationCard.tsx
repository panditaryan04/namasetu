import { useState } from "react";
import { Link } from "react-router-dom";
import type { CompositionItem, LinkInfo, MedBadge, MedicationView } from "../services/api";

export const REFERENCE_ONLY = "Reference only — not a prescription. Consult a qualified clinician.";

export function ReferenceBanner() {
  return (
    <p
      role="note"
      className="rounded-xl border border-saffron-300 bg-saffron-50 px-4 py-2 text-sm font-semibold text-saffron-900 dark:border-saffron-700 dark:bg-saffron-900/30 dark:text-saffron-100"
    >
      ⚕ {REFERENCE_ONLY}
    </p>
  );
}

const BADGE_STYLE: Record<MedBadge["severity"], string> = {
  warning: "bg-red-50 text-red-800 ring-red-200 dark:bg-red-950 dark:text-red-200 dark:ring-red-900",
  caution:
    "bg-saffron-50 text-saffron-900 ring-saffron-300 dark:bg-saffron-900/30 dark:text-saffron-100 dark:ring-saffron-700",
  info: "bg-slate-100 text-slate-700 ring-slate-200 dark:bg-slate-800 dark:text-slate-200 dark:ring-slate-700",
};

export function Badges({ badges }: { badges: MedBadge[] }) {
  return (
    <span className="flex flex-wrap gap-1">
      {badges.map((b) => (
        <span
          key={b.code}
          className={`rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset ${BADGE_STYLE[b.severity]}`}
        >
          {b.label}
        </span>
      ))}
    </span>
  );
}

export function SaltChip({ c }: { c: CompositionItem }) {
  return (
    <Link
      to={`/salts/${c.ingredient_id}`}
      className="inline-flex items-center gap-1 rounded-full bg-brand-50 px-2.5 py-1 text-xs font-semibold text-brand-900 ring-1 ring-inset ring-brand-200 hover:bg-brand-100 dark:bg-brand-950 dark:text-brand-100 dark:ring-brand-800"
      title={c.printed_name !== c.canonical_name ? `Printed as ${c.printed_name}` : undefined}
    >
      {c.canonical_name}
      {c.strength && <span className="font-mono font-normal">{c.strength}</span>}
    </Link>
  );
}

export function CompositionTable({ items }: { items: CompositionItem[] }) {
  return (
    <table className="mt-2 w-full text-left text-xs">
      <caption className="sr-only">Composition as printed in the source</caption>
      <thead className="text-slate-500">
        <tr>
          <th className="py-1 pr-2 font-medium">Ingredient</th>
          <th className="py-1 pr-2 font-medium">Botanical name</th>
          <th className="py-1 pr-2 font-medium">Part used</th>
          <th className="py-1 font-medium">Proportion</th>
        </tr>
      </thead>
      <tbody>
        {items.map((c) => (
          <tr
            key={`${c.ingredient_id}-${c.printed_name}`}
            className="border-t border-slate-100 dark:border-slate-800"
          >
            <td className="py-1 pr-2">
              <Link
                to={`/salts/${c.ingredient_id}`}
                className="font-medium underline-offset-2 hover:underline"
              >
                {c.printed_name}
              </Link>
              {c.metal_mineral && (
                <span
                  className="ml-1 text-saffron-700 dark:text-saffron-300"
                  title="Processed metal / mineral"
                >
                  ◆
                </span>
              )}
            </td>
            <td className="py-1 pr-2 italic">{c.botanical_name ?? "—"}</td>
            <td className="py-1 pr-2">{c.part_used ?? "—"}</td>
            <td className="py-1">{c.proportion ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function MedicationCard({
  m,
  link,
  action,
}: {
  m: MedicationView;
  link?: LinkInfo;
  action?: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const ayush = m.kind !== "biomedical";
  return (
    <div className="rounded-xl border border-slate-200 p-3 dark:border-slate-800">
      <div className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="font-semibold">
            {m.name}
            {m.name_devanagari && (
              <span className="ml-2 font-normal text-slate-600 dark:text-slate-400" lang="sa">
                {m.name_devanagari}
              </span>
            )}
            {m.name_iast && m.name_iast !== m.name && (
              <span className="ml-2 text-sm font-normal italic text-slate-500" lang="sa-Latn">
                {m.name_iast}
              </span>
            )}
          </p>
          <p className="text-xs text-slate-600 dark:text-slate-400">
            {m.dosage_form}
            {m.route && ` · ${m.route}`}
            {m.therapeutic_class && ` · ${m.therapeutic_class}`} · <span className="font-mono">{m.code}</span>
          </p>
        </div>
        {action}
      </div>
      <div className="mt-2">
        <Badges badges={m.badges} />
      </div>
      {!ayush ? (
        <div className="mt-2 flex flex-wrap gap-1.5" aria-label="Salts">
          {m.composition.map((c) => (
            <SaltChip key={c.ingredient_id} c={c} />
          ))}
        </div>
      ) : (
        <div className="mt-2">
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
            className="text-sm font-medium text-brand-800 underline dark:text-brand-300"
          >
            {open
              ? "Hide composition"
              : `Composition (${m.composition.length} ingredient${m.composition.length === 1 ? "" : "s"})`}
          </button>
          {open && <CompositionTable items={m.composition} />}
        </div>
      )}
      {m.brands.length > 0 && (
        <p className="mt-2 text-xs text-slate-600 dark:text-slate-400">
          Brand examples: {m.brands.join(", ")}
        </p>
      )}
      {m.caution_note && (
        <p className="mt-1 text-xs text-saffron-800 dark:text-saffron-200">Source note: {m.caution_note}</p>
      )}
      <p className="mt-2 text-[11px] text-slate-500">
        Source: {m.source.title}
        {m.source.edition && `, ${m.source.edition}`}
        {m.source.page && `, p. ${m.source.page}`}
        {link && (
          <>
            {" · "}
            <span
              className={
                link.status_label === "approved"
                  ? "font-semibold text-brand-700 dark:text-brand-300"
                  : "font-semibold"
              }
            >
              {link.status_label}
            </span>
            {link.reviewer && link.status_label === "approved" && ` by ${link.reviewer}`}
          </>
        )}
      </p>
    </div>
  );
}
