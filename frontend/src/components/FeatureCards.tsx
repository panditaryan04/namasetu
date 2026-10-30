import { motion, type Variants } from "framer-motion";
import type { ReactNode } from "react";

const list: Variants = { hidden: {}, show: { transition: { staggerChildren: 0.12 } } };
const card: Variants = {
  hidden: { opacity: 0, y: 24 },
  show: { opacity: 1, y: 0, transition: { duration: 0.5, ease: "easeOut" } },
};

const glass =
  "rounded-3xl border border-med-border bg-white/75 shadow-glass-sm backdrop-blur-xl transition duration-200 hover:-translate-y-0.5 hover:shadow-glass dark:bg-slate-900/70";

function Icon({ children }: { children: ReactNode }) {
  return (
    <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-gradient-strong text-white shadow-glass-sm">
      <svg
        aria-hidden="true"
        viewBox="0 0 24 24"
        className="h-6 w-6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {children}
      </svg>
    </span>
  );
}

const STEPS = [
  {
    title: "Search in any script",
    text: "English, IAST, Devanagari or Hinglish input.",
    icon: (
      <>
        <path d="M4 5h9M8.5 5v2c0 4-2 7-4.5 8.5M6 10c1.5 2 3.5 3.5 6 4.5" />
        <path d="m13 20 3.5-8 3.5 8M14.2 17.5h4.6" />
      </>
    ),
  },
  {
    title: "Explainable matching",
    text: "See exactly why a TM2 code matched.",
    icon: (
      <>
        <circle cx="11" cy="11" r="6.5" />
        <path d="m20 20-4.4-4.4M8.5 11l1.8 1.8 3.4-3.6" />
      </>
    ),
  },
  {
    title: "FHIR R4 output",
    text: "Clinician-validated, interoperable records.",
    icon: (
      <>
        <path d="M8 4H6a2 2 0 0 0-2 2v3a2 2 0 0 1-2 2 2 2 0 0 1 2 2v3a2 2 0 0 0 2 2h2" />
        <path d="M16 4h2a2 2 0 0 1 2 2v3a2 2 0 0 0 2 2 2 2 0 0 0-2 2v3a2 2 0 0 1-2 2h-2" />
        <path d="m9.5 12 1.8 1.8 3.2-3.6" />
      </>
    ),
  },
];

const STANDARDS = [
  { name: "NAMASTE", text: "India's national Ayush morbidity codes: the starting diagnosis." },
  { name: "ICD-11 TM2", text: "WHO Traditional Medicine Module 2: the matching TM2 pattern." },
  { name: "ICD-11 Biomedicine", text: "WHO biomedical codes, linked only as related, never invented." },
  { name: "FHIR R4", text: "HL7 resources (CodeSystem, ConceptMap, Condition) for exchange." },
];

function SectionHeading({ id, children }: { id: string; children: ReactNode }) {
  return (
    <h2
      id={id}
      className="text-center font-display text-3xl font-bold tracking-tight text-med-heading sm:text-4xl"
    >
      {children}
    </h2>
  );
}

export function HowItWorks() {
  return (
    <section aria-labelledby="how-heading" className="py-16">
      <SectionHeading id="how-heading">How NamaSetu maps a diagnosis</SectionHeading>
      <motion.ul
        className="mt-10 grid gap-6 md:grid-cols-3"
        variants={list}
        initial="hidden"
        whileInView="show"
        viewport={{ once: true, amount: 0.3 }}
      >
        {STEPS.map((s, i) => (
          <motion.li key={s.title} variants={card} className={`${glass} p-6`}>
            <div className="flex items-center justify-between">
              <Icon>{s.icon}</Icon>
              <span
                className="font-display text-sm font-bold text-slate-500 dark:text-slate-400"
                aria-hidden="true"
              >
                0{i + 1}
              </span>
            </div>
            <h3 className="mt-5 font-display text-lg font-bold text-med-heading">{s.title}</h3>
            <p className="mt-1.5 text-med-body">{s.text}</p>
          </motion.li>
        ))}
      </motion.ul>
    </section>
  );
}

export function StandardsStrip() {
  return (
    <section aria-labelledby="standards-heading" className="pb-20 pt-8">
      <SectionHeading id="standards-heading">Built on trusted standards</SectionHeading>
      <motion.ul
        className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4"
        variants={list}
        initial="hidden"
        whileInView="show"
        viewport={{ once: true, amount: 0.3 }}
      >
        {STANDARDS.map((s) => (
          <motion.li key={s.name} variants={card} className={`${glass} p-5`}>
            <h3 className="inline-flex items-center gap-2 font-display font-bold text-med-heading">
              <span className="h-2 w-2 rounded-full bg-brand-gradient" aria-hidden="true" />
              {s.name}
            </h3>
            <p className="mt-2 text-sm text-med-body">{s.text}</p>
          </motion.li>
        ))}
      </motion.ul>
    </section>
  );
}
