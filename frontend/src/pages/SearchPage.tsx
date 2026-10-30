import { MotionConfig } from "framer-motion";
import { Suspense, lazy } from "react";
import { HowItWorks, StandardsStrip } from "../components/FeatureCards";
import { Hero } from "../components/Hero";

// three.js loads in its own chunk after the page content, so it never delays the search box.
const ParticleBackground = lazy(() => import("../components/ParticleBackground"));

export default function SearchPage() {
  return (
    // reducedMotion="user": entrance animations are skipped when the OS asks for reduced motion.
    <MotionConfig reducedMotion="user">
      <Suspense fallback={null}>
        <ParticleBackground />
      </Suspense>
      <div className="mx-auto max-w-6xl px-2">
        <Hero />
        <HowItWorks />
        <StandardsStrip />
      </div>
    </MotionConfig>
  );
}
