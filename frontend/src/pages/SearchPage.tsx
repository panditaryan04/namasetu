import { useState } from "react";
import { Link } from "react-router-dom";
import { Autocomplete } from "../components/Autocomplete";

const EXAMPLES = [
  { q: "आमवात", hint: "Devanagari", lang: "sa" },
  { q: "Āmavāta", hint: "IAST", lang: "sa-Latn" },
  { q: "Amla Pitta", hint: "spaced compound", lang: "sa-Latn" },
  { q: "aamvaat", hint: "Hinglish", lang: "hi-Latn" },
  { q: "Vataja Jvara", hint: "maps to a wider TM2 code", lang: "sa-Latn" },
  { q: "pitta", hint: "partial root term", lang: "sa-Latn" },
];

const disciplines = [
  { id: "ayurveda", label: "Ayurveda", available: true },
  { id: "siddha", label: "Siddha", available: false },
  { id: "unani", label: "Unani", available: false },
] as const;

export default function SearchPage() {
  const [discipline, setDiscipline] = useState<string>("ayurveda");

  return (
    <div className="mx-auto max-w-3xl">
      <section aria-labelledby="hero-heading">
        <p className="text-sm font-semibold uppercase tracking-wider text-saffron-700 dark:text-saffron-400">
          NAMASTE · ICD-11 TM2 · ICD-11 Biomedicine
        </p>
        <h1 id="hero-heading" className="mt-2 text-3xl font-bold tracking-tight sm:text-4xl">
          One diagnosis, three code systems, every step explained.
        </h1>
        <p className="mt-3 max-w-2xl text-slate-600 dark:text-slate-300">
          Search an Ayush diagnosis in English, IAST, Devanagari or Hinglish. NamaSetu finds the matching
          ICD-11 Traditional Medicine Module 2 code and shows why it matched, then emits FHIR R4 that a
          clinician has validated.
        </p>

        <div role="tablist" aria-label="Discipline" className="mt-6 flex gap-2">
          {disciplines.map((d) => (
            <button
              key={d.id}
              role="tab"
              type="button"
              aria-selected={discipline === d.id}
              disabled={!d.available}
              onClick={() => setDiscipline(d.id)}
              className={`rounded-full px-4 py-1.5 text-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal-600 disabled:cursor-not-allowed disabled:opacity-60 ${
                discipline === d.id
                  ? "bg-teal-700 text-white"
                  : "bg-white text-slate-700 ring-1 ring-slate-200 dark:bg-slate-900 dark:text-slate-200 dark:ring-slate-700"
              }`}
            >
              {d.label}
              {!d.available && <span className="ml-1 text-xs">(coming soon)</span>}
            </button>
          ))}
        </div>

        <div className="mt-4">
          <Autocomplete />
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
          <span className="text-slate-600 dark:text-slate-400">Try:</span>
          {EXAMPLES.map((ex) => (
            <Link
              key={ex.q}
              to={`/map?q=${encodeURIComponent(ex.q)}`}
              className="rounded-full bg-white px-3 py-1 ring-1 ring-inset ring-slate-200 hover:ring-teal-400 dark:bg-slate-900 dark:ring-slate-700"
              title={ex.hint}
            >
              <span lang={ex.lang}>{ex.q}</span>
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}
