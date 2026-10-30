import { useState } from "react";
import { validateResource, type OperationOutcome } from "../services/api";

const SEVERITY_STYLE: Record<string, string> = {
  fatal: "text-red-700 dark:text-red-300",
  error: "text-red-700 dark:text-red-300",
  warning: "text-saffron-800 dark:text-saffron-200",
  information: "text-brand-800 dark:text-brand-200",
};

/** Pretty-printed FHIR JSON with Copy / Download / Validate (server-side $validate). */
export function FHIRViewer({ resource, filename }: { resource: unknown; filename: string }) {
  const [copied, setCopied] = useState(false);
  const [outcome, setOutcome] = useState<OperationOutcome | null>(null);
  const [validating, setValidating] = useState(false);
  const json = JSON.stringify(resource, null, 2);

  const copy = async () => {
    await navigator.clipboard.writeText(json);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  const download = () => {
    const url = URL.createObjectURL(new Blob([json], { type: "application/fhir+json" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };
  const validate = async () => {
    setValidating(true);
    try {
      setOutcome(await validateResource(resource));
    } finally {
      setValidating(false);
    }
  };

  const btn =
    "rounded-lg px-3 py-1.5 text-sm font-medium ring-1 ring-inset ring-slate-300 hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-600 dark:ring-slate-600 dark:hover:bg-slate-800";
  return (
    <section
      aria-label="FHIR JSON"
      className="rounded-2xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900"
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 px-4 py-2 dark:border-slate-800">
        <span className="mr-auto font-mono text-xs text-slate-500">application/fhir+json</span>
        <button type="button" onClick={copy} className={btn}>
          {copied ? "Copied" : "Copy"}
        </button>
        <button type="button" onClick={download} className={btn}>
          Download
        </button>
        <button type="button" onClick={validate} disabled={validating} className={btn}>
          {validating ? "Validating…" : "Validate"}
        </button>
      </div>
      {outcome && (
        <ul
          role="status"
          className="space-y-1 border-b border-slate-200 px-4 py-2 text-sm dark:border-slate-800"
        >
          {outcome.issue.map((i, n) => (
            <li key={n} className={SEVERITY_STYLE[i.severity]}>
              <span className="font-semibold">{i.severity}</span>: {i.diagnostics}
            </li>
          ))}
        </ul>
      )}
      {/* Scrollable regions must be keyboard-focusable (axe: scrollable-region-focusable). */}
      <pre
        aria-label="Resource JSON"
        className="max-h-[32rem] overflow-auto p-4 text-xs leading-relaxed"
        tabIndex={0}
      >
        <code>{json}</code>
      </pre>
    </section>
  );
}
