import type { Band } from "../services/api";

const BAND_COLOR: Record<Band, string> = {
  high: "bg-brand-600",
  medium: "bg-saffron-500",
  low: "bg-red-500",
};

/** Confidence with the calibrated T_LOW / T_HIGH gate positions marked on the track. */
export function ConfidenceBar({
  value,
  band,
  thresholds,
}: {
  value: number;
  band: Band;
  thresholds: { high: number; low: number };
}) {
  const pct = Math.round(value * 100);
  return (
    <div>
      <div className="flex items-baseline justify-between text-xs text-slate-600 dark:text-slate-400">
        <span>Confidence</span>
        <span className="font-mono text-sm font-semibold text-slate-900 dark:text-slate-100">{pct}%</span>
      </div>
      <div
        role="meter"
        aria-label="Confidence"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-valuetext={`${pct}% (${band} band)`}
        className="relative mt-1 h-2 rounded-full bg-slate-200 dark:bg-slate-800"
      >
        <div className={`h-2 rounded-full ${BAND_COLOR[band]}`} style={{ width: `${pct}%` }} />
        {[thresholds.low, thresholds.high].map((t) => (
          <span
            key={t}
            aria-hidden="true"
            title={`threshold ${Math.round(t * 100)}%`}
            className="absolute -top-1 h-4 w-0.5 bg-slate-500 dark:bg-slate-400"
            style={{ left: `${t * 100}%` }}
          />
        ))}
      </div>
    </div>
  );
}
