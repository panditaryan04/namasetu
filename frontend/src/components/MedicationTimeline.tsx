import { useState } from "react";
import type { TimelineItem } from "../services/api";

const DAY = 86_400_000;
const BAR: Record<string, string> = {
  active: "bg-brand-600",
  stopped: "bg-slate-400 dark:bg-slate-600",
  unknown: "bg-saffron-500",
};

function ms(d: string | null, fallback: number) {
  return d ? new Date(`${d}T00:00:00Z`).getTime() : fallback;
}

/** Gantt-style active periods. Each bar expands to the salts / herbs / constituents it contributes. */
export function MedicationTimeline({ items, asOf }: { items: TimelineItem[]; asOf: string }) {
  const [open, setOpen] = useState<number | null>(null);
  const now = ms(asOf, Date.now());
  if (items.length === 0) return <p className="text-sm text-slate-500">No medication history recorded.</p>;
  const start = Math.min(...items.map((i) => ms(i.period_start, now))) - 15 * DAY;
  const span = Math.max(now - start, DAY);
  const years = [];
  for (let y = new Date(start).getUTCFullYear() + 1; y <= new Date(now).getUTCFullYear(); y++) years.push(y);
  const pct = (t: number) => `${Math.min(100, Math.max(0, ((t - start) / span) * 100))}%`;
  return (
    <div className="space-y-1" aria-label="Medication timeline">
      <div className="relative ml-[40%] h-5 text-[10px] text-slate-500 sm:ml-[34%]" aria-hidden="true">
        {years.map((y) => (
          <span key={y} className="absolute -translate-x-1/2" style={{ left: pct(Date.UTC(y, 0, 1)) }}>
            {y}
          </span>
        ))}
        <span className="absolute right-0 font-semibold text-brand-700 dark:text-brand-300">
          as of {asOf}
        </span>
      </div>
      {items.map((i) => {
        const left = ms(i.period_start, start);
        const right = i.status === "stopped" && !i.period_end ? left + DAY : ms(i.period_end, now);
        return (
          <div key={i.id}>
            <button
              type="button"
              aria-expanded={open === i.id}
              onClick={() => setOpen(open === i.id ? null : i.id)}
              className="grid w-full grid-cols-[40%_1fr] items-center gap-2 rounded-lg px-1 py-1 text-left hover:bg-slate-50 dark:hover:bg-slate-900 sm:grid-cols-[34%_1fr]"
            >
              <span className="min-w-0 text-sm">
                <span className="block truncate font-medium">{i.resolution.label}</span>
                <span className="block truncate text-xs text-slate-500">
                  “{i.text}” · {i.status}
                  {i.resolution.kind === "unresolved" && " · ⚠ please verify"}
                </span>
              </span>
              <span className="relative h-3 rounded bg-slate-100 dark:bg-slate-800">
                <span
                  className={`absolute top-0 h-3 rounded ${BAR[i.status] ?? BAR.unknown}`}
                  style={{ left: pct(left), width: `calc(${pct(right)} - ${pct(left)})`, minWidth: "4px" }}
                  title={`${i.period_start ?? "?"} → ${i.period_end ?? (i.status === "active" ? "ongoing" : "?")}`}
                />
              </span>
            </button>
            {open === i.id && (
              <div className="mb-2 ml-2 rounded-lg bg-slate-50 p-2 text-xs dark:bg-slate-900">
                <p>
                  Resolved as <strong>{i.resolution.kind}</strong>
                  {i.resolution.note && ` — ${i.resolution.note}`} · {i.period_start ?? "?"} →{" "}
                  {i.period_end ?? (i.status === "active" ? "ongoing" : "?")} · {i.source_system}
                </p>
                {i.agents.length > 0 ? (
                  <ul className="mt-1 space-y-0.5">
                    {i.agents.map((a) => (
                      <li key={`${a.type}-${a.name}-${a.path}`}>
                        <span className="rounded bg-white px-1 font-mono text-[10px] dark:bg-slate-800">
                          {a.type}
                        </span>{" "}
                        {a.path}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-1 text-slate-500">No resolved salts or constituents.</p>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
