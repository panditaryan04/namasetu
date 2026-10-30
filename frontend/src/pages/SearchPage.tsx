import { MotionConfig } from "framer-motion";
import { HowItWorks, StandardsStrip } from "../components/FeatureCards";
import { Hero } from "../components/Hero";

export default function SearchPage() {
  return (
    // reducedMotion="user": entrance animations are skipped when the OS asks for reduced motion.
    <MotionConfig reducedMotion="user">
      <div className="mx-auto max-w-6xl px-2">
        <Hero />
        <HowItWorks />
        <StandardsStrip />
      </div>
    </MotionConfig>
  );
}
