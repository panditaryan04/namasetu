import { motion, type Variants } from "framer-motion";
import { SearchBox } from "./SearchBox";

// Badge -> H1 -> subtitle -> search card, 0.1s apart.
const container: Variants = { hidden: {}, show: { transition: { staggerChildren: 0.1 } } };
const item: Variants = {
  hidden: { opacity: 0, y: 16 },
  show: { opacity: 1, y: 0, transition: { duration: 0.5, ease: "easeOut" } },
};

export function Hero() {
  return (
    <motion.section
      aria-labelledby="hero-heading"
      className="mx-auto max-w-4xl py-16 text-center sm:py-24"
      variants={container}
      initial="hidden"
      animate="show"
    >
      <motion.p
        variants={item}
        className="inline-flex items-center gap-2 rounded-full border border-med-border bg-white/80 px-4 py-1.5 text-xs font-semibold uppercase tracking-[0.18em] text-med-primary-dark shadow-glass-sm backdrop-blur dark:bg-slate-900/70 dark:text-sky-300"
      >
        <span className="relative flex h-2 w-2" aria-hidden="true">
          <span className="absolute inline-flex h-full w-full rounded-full bg-med-accent opacity-75 motion-safe:animate-ping" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-med-accent" />
        </span>
        NAMASTE · ICD-11 TM2 · ICD-11 Biomedicine
      </motion.p>

      <motion.h1
        id="hero-heading"
        variants={item}
        className="mt-6 font-display text-4xl font-extrabold tracking-tight text-med-heading sm:text-5xl md:text-6xl"
      >
        One diagnosis,{" "}
        <span className="bg-brand-gradient-text bg-clip-text text-transparent">three code systems</span>,
        every step explained.
      </motion.h1>

      <motion.p variants={item} className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-med-body">
        Search an Ayush diagnosis in English, IAST, Devanagari or Hinglish. NamaSetu finds the matching ICD-11
        Traditional Medicine Module 2 code and shows why it matched, then emits FHIR R4 that a clinician has
        validated.
      </motion.p>

      <motion.div variants={item} className="mt-10">
        <SearchBox />
      </motion.div>
    </motion.section>
  );
}
