import { DISCLAIMER } from "../constants";

export default function About() {
  return (
    <article className="prose-sm max-w-3xl space-y-4 text-slate-700 dark:text-slate-300">
      <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">About NamaSetu</h1>
      <p>
        NamaSetu bridges India&apos;s NAMASTE Ayush terminology with WHO ICD-11: Chapter 26 Traditional
        Medicine Module 2, plus biomedical ICD-11 codes where WHO publishes a link or a clinician approves
        one. It is built for Problem Statement SIH25026 (Ministry of Ayush).
      </p>
      <ul className="list-disc space-y-1 pl-5">
        <li>NamaSetu never invents a code. Every code it returns exists in an ingested dataset.</li>
        <li>Every match shows its evidence: per-stage scores, matched terms and the hierarchy path.</li>
        <li>Nothing reaches FHIR output until a clinician has approved it.</li>
        <li>All bundled data is labelled SAMPLE: it is illustrative, not official.</li>
      </ul>
      <p
        role="note"
        className="rounded-xl border border-saffron-300 bg-saffron-50 p-4 text-saffron-900 dark:border-saffron-700 dark:bg-saffron-900/30 dark:text-saffron-100"
      >
        {DISCLAIMER}
      </p>
    </article>
  );
}
