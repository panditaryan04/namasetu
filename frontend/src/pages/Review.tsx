import { lazy, Suspense } from "react";
import { useSearchParams } from "react-router-dom";

const ReviewQueue = lazy(() => import("./ReviewQueue"));
const MedicationLinkReview = lazy(() => import("./MedicationLinkReview"));

export default function Review() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "medications" ? "medications" : "mappings";
  const btn = (id: string, label: string) => (
    <button
      type="button"
      role="tab"
      aria-selected={tab === id}
      onClick={() => setParams(id === "mappings" ? {} : { tab: id })}
      className={`rounded-md px-3 py-1.5 text-sm font-medium ${tab === id ? "bg-white shadow dark:bg-slate-950" : "text-slate-600 dark:text-slate-300"}`}
    >
      {label}
    </button>
  );
  return (
    <div className="space-y-4">
      <div
        role="tablist"
        aria-label="Review type"
        className="inline-flex rounded-lg bg-slate-100 p-1 dark:bg-slate-800"
      >
        {btn("mappings", "Mappings")}
        {btn("medications", "Medication links")}
      </div>
      <Suspense fallback={<div className="h-40 animate-pulse rounded-2xl bg-slate-100 dark:bg-slate-900" />}>
        {tab === "mappings" ? <ReviewQueue /> : <MedicationLinkReview />}
      </Suspense>
    </div>
  );
}
