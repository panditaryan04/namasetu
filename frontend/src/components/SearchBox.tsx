import { useRef, useState, type KeyboardEvent } from "react";
import { Autocomplete } from "./Autocomplete";

const EXAMPLES = [
  { q: "आमवात", hint: "Devanagari", lang: "sa" },
  { q: "Āmavāta", hint: "IAST", lang: "sa-Latn" },
  { q: "Amla Pitta", hint: "spaced compound", lang: "sa-Latn" },
  { q: "aamvaat", hint: "Hinglish", lang: "hi-Latn" },
  { q: "Vataja Jvara", hint: "maps to a wider TM2 code", lang: "sa-Latn" },
  { q: "pitta", hint: "partial root term", lang: "sa-Latn" },
];

const DISCIPLINES = [
  { id: "ayurveda", label: "Ayurveda", available: true },
  { id: "siddha", label: "Siddha", available: false },
  { id: "unani", label: "Unani", available: false },
] as const;

const focusRing =
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-med-primary-dark";

/** The main interaction: discipline tabs, the hero search box and "Try" chips that fill it. */
export function SearchBox() {
  const [discipline, setDiscipline] = useState<string>("ayurveda");
  const [text, setText] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  const fill = (q: string) => {
    setText(q);
    inputRef.current?.focus();
  };

  // Roving focus across the tabs with the arrow keys (WAI-ARIA tabs pattern).
  const onTabKey = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const next = (i + (e.key === "ArrowRight" ? 1 : -1) + DISCIPLINES.length) % DISCIPLINES.length;
    tabRefs.current[next]?.focus();
  };

  return (
    <div className="rounded-3xl border border-med-border bg-white/80 p-5 text-left shadow-glass backdrop-blur-xl dark:bg-slate-900/70 sm:p-8">
      <div
        role="tablist"
        aria-label="Discipline"
        className="inline-flex flex-wrap gap-1 rounded-2xl border border-med-border bg-sky-50/70 p-1 dark:bg-slate-800/60"
      >
        {DISCIPLINES.map((d, i) => {
          const selected = discipline === d.id;
          return (
            <button
              key={d.id}
              ref={(el) => (tabRefs.current[i] = el)}
              id={`tab-${d.id}`}
              role="tab"
              type="button"
              aria-selected={selected}
              aria-controls="discipline-panel"
              aria-disabled={!d.available || undefined}
              tabIndex={selected ? 0 : -1}
              onClick={() => d.available && setDiscipline(d.id)}
              onKeyDown={(e) => onTabKey(e, i)}
              className={`flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold transition duration-200 ${focusRing} ${
                selected
                  ? "bg-med-primary-dark text-white shadow-glass-sm"
                  : d.available
                    ? "text-med-body hover:bg-white dark:hover:bg-slate-700"
                    : "cursor-not-allowed text-slate-500 dark:text-slate-400"
              }`}
            >
              {d.label}
              {!d.available && (
                <span className="rounded-full bg-white px-2 py-0.5 text-[11px] font-medium text-slate-600 ring-1 ring-med-border dark:bg-slate-900 dark:text-slate-300">
                  Coming soon
                </span>
              )}
            </button>
          );
        })}
      </div>

      <div id="discipline-panel" role="tabpanel" aria-labelledby={`tab-${discipline}`} className="mt-5">
        <Autocomplete variant="hero" value={text} onValueChange={setText} inputRef={inputRef} />
        <div className="mt-1 flex flex-wrap items-center gap-2 text-sm">
          <span className="font-medium text-med-body">Try:</span>
          {EXAMPLES.map((ex) => (
            <button
              key={ex.q}
              type="button"
              onClick={() => fill(ex.q)}
              title={ex.hint}
              aria-label={`Fill the search with ${ex.q} (${ex.hint})`}
              className={`rounded-full border border-med-border bg-white px-3.5 py-1.5 text-med-heading transition duration-200 hover:-translate-y-0.5 hover:border-sky-300 hover:bg-sky-50 hover:shadow-glass-sm dark:bg-slate-900 dark:hover:bg-slate-800 ${focusRing}`}
            >
              <span lang={ex.lang}>{ex.q}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
