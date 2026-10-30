import { useId, useState, type ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import { num, pct } from "../services/format";

// Colours are CSS roles (see index.css .viz-root): categorical slots in fixed order, never cycled.
export const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)"] as const;
const AXIS = { stroke: "var(--viz-grid)", tick: { fill: "var(--viz-text-2)", fontSize: 12 } };
const TOOLTIP = {
  contentStyle: {
    background: "var(--viz-surface)",
    border: "1px solid var(--viz-grid)",
    borderRadius: 8,
    color: "var(--viz-text)",
    fontSize: 12,
  },
  labelStyle: { color: "var(--viz-text)", fontWeight: 600 },
  itemStyle: { color: "var(--viz-text)" },
};

export interface Column<T> {
  key: string;
  label: string;
  value: (row: T) => ReactNode;
  numeric?: boolean;
}

export function DataTable<T>({
  rows,
  columns,
  caption,
}: {
  rows: T[];
  columns: Column<T>[];
  caption: string;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-slate-200 text-left text-xs text-slate-500 dark:border-slate-700">
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                className={`px-2 py-1 font-medium ${c.numeric ? "text-right" : ""}`}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-slate-100 last:border-0 dark:border-slate-800">
              {columns.map((c) => (
                <td key={c.key} className={`px-2 py-1 ${c.numeric ? "text-right tabular-nums" : ""}`}>
                  {c.value(r)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Card with a title, a short note and a Chart / Table switch (the table is the accessible view). */
export function ChartCard({
  title,
  note,
  chart,
  table,
  defaultView = "chart",
}: {
  title: string;
  note?: ReactNode;
  chart: ReactNode;
  table: ReactNode;
  defaultView?: "chart" | "table";
}) {
  const [view, setView] = useState(defaultView);
  const id = useId();
  return (
    <section
      aria-labelledby={id}
      className="viz-root rounded-2xl border border-slate-200 p-4 dark:border-slate-800"
    >
      <div className="mb-2 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 id={id} className="font-semibold">
            {title}
          </h2>
          {note && <p className="text-xs text-slate-500 dark:text-slate-400">{note}</p>}
        </div>
        <div
          role="group"
          aria-label={`${title}: view`}
          className="flex rounded-lg ring-1 ring-slate-200 dark:ring-slate-700"
        >
          {(["chart", "table"] as const).map((v) => (
            <button
              key={v}
              type="button"
              aria-pressed={view === v}
              onClick={() => setView(v)}
              className={`px-2 py-0.5 text-xs capitalize first:rounded-l-lg last:rounded-r-lg ${view === v ? "bg-slate-800 text-white dark:bg-slate-200 dark:text-slate-900" : "text-slate-600 dark:text-slate-300"}`}
            >
              {v}
            </button>
          ))}
        </div>
      </div>
      {view === "chart" ? chart : table}
    </section>
  );
}

export interface BarSeries {
  key: string;
  label: string;
}

/**
 * Horizontal grouped bars on a 0–1 scale (rates). Up to three series, slots 1–3 in order; the first series
 * carries direct value labels, the legend names every series.
 */
export function RateBars<T extends { name: string }>({
  data,
  series,
  height,
}: {
  data: T[];
  series: BarSeries[];
  height?: number;
}) {
  const h = height ?? Math.max(140, data.length * (series.length * 14 + 18) + 50);
  return (
    <div style={{ height: h }} role="img" aria-label={`Bar chart: ${series.map((s) => s.label).join(", ")}`}>
      <div aria-hidden="true" className="h-full">
        <ResponsiveContainer>
          <BarChart
            data={data}
            layout="vertical"
            margin={{ top: 4, right: 40, left: 0, bottom: 0 }}
            barGap={2}
          >
            <CartesianGrid horizontal={false} stroke="var(--viz-grid)" strokeDasharray="2 4" />
            <XAxis type="number" domain={[0, 1]} tickFormatter={(v: number) => `${v * 100}%`} {...AXIS} />
            <YAxis type="category" dataKey="name" width={170} {...AXIS} tickLine={false} />
            <Tooltip
              {...TOOLTIP}
              cursor={{ fill: "var(--viz-grid)", opacity: 0.4 }}
              formatter={(v: number) => pct(v)}
            />
            {series.length > 1 && (
              <Legend
                verticalAlign="top"
                align="left"
                iconType="circle"
                wrapperStyle={{ fontSize: 12, paddingBottom: 4 }}
              />
            )}
            {series.map((s, i) => (
              <Bar
                key={s.key}
                dataKey={s.key}
                name={s.label}
                fill={SERIES[i]}
                radius={[0, 4, 4, 0]}
                maxBarSize={12}
              >
                {i === 0 && (
                  <LabelList
                    dataKey={s.key}
                    position="right"
                    formatter={(v: number) => pct(v)}
                    fill="var(--viz-text-2)"
                    fontSize={11}
                  />
                )}
              </Bar>
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/** Columns with a value label on each (few categories); colour per bar from `colors` or slot 1. */
export function CountColumns({
  data,
  colors,
  height = 200,
}: {
  data: { name: string; value: number }[];
  colors?: string[];
  height?: number;
}) {
  return (
    <div
      style={{ height }}
      role="img"
      aria-label={`Column chart: ${data.map((d) => `${d.name} ${d.value}`).join(", ")}`}
    >
      <div aria-hidden="true" className="h-full">
        <ResponsiveContainer>
          <BarChart data={data} margin={{ top: 20, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid vertical={false} stroke="var(--viz-grid)" strokeDasharray="2 4" />
            <XAxis dataKey="name" {...AXIS} tickLine={false} interval={0} />
            <YAxis allowDecimals={false} {...AXIS} width={48} />
            <Tooltip {...TOOLTIP} cursor={{ fill: "var(--viz-grid)", opacity: 0.4 }} />
            <Bar dataKey="value" name="Count" radius={[4, 4, 0, 0]} maxBarSize={24}>
              {data.map((d, i) => (
                <Cell key={d.name} fill={colors?.[i] ?? SERIES[0]} />
              ))}
              <LabelList dataKey="value" position="top" fill="var(--viz-text-2)" fontSize={11} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/** Reliability diagram: mean predicted vs observed per confidence bin; the diagonal is perfect calibration. */
export function ReliabilityChart({
  bins,
}: {
  bins: { mean_predicted: number; observed: number; count: number; lower: number; upper: number }[];
}) {
  const data = bins.filter((b) => b.count > 0);
  return (
    <div style={{ height: 260 }} role="img" aria-label="Reliability diagram">
      <div aria-hidden="true" className="h-full">
        <ResponsiveContainer>
          <ScatterChart margin={{ top: 8, right: 16, left: 0, bottom: 16 }}>
            <CartesianGrid stroke="var(--viz-grid)" strokeDasharray="2 4" />
            <XAxis
              type="number"
              dataKey="mean_predicted"
              name="Mean predicted"
              domain={[0, 1]}
              {...AXIS}
              label={{
                value: "Mean predicted confidence",
                position: "insideBottom",
                offset: -8,
                fill: "var(--viz-text-2)",
                fontSize: 12,
              }}
            />
            <YAxis
              type="number"
              dataKey="observed"
              name="Observed"
              domain={[0, 1]}
              {...AXIS}
              width={48}
              label={{
                value: "Observed correct",
                angle: -90,
                position: "insideLeft",
                fill: "var(--viz-text-2)",
                fontSize: 12,
              }}
            />
            <ZAxis type="number" dataKey="count" range={[64, 400]} name="Pairs" />
            <ReferenceLine
              segment={[
                { x: 0, y: 0 },
                { x: 1, y: 1 },
              ]}
              stroke="var(--viz-text-2)"
              strokeDasharray="4 4"
            />
            <Tooltip
              {...TOOLTIP}
              cursor={{ strokeDasharray: "3 3" }}
              formatter={(v: number, name: string) => (name === "Pairs" ? v : num(v))}
            />
            <Scatter data={data} fill={SERIES[0]} stroke="var(--viz-surface)" strokeWidth={2} />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/** Two lines over days (queued vs decided), crosshair tooltip. */
export function DailyLines({ data }: { data: { date: string; queued: number; decided: number }[] }) {
  return (
    <div style={{ height: 220 }} role="img" aria-label="Line chart: review items queued and decided per day">
      <div aria-hidden="true" className="h-full">
        <ResponsiveContainer>
          <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid vertical={false} stroke="var(--viz-grid)" strokeDasharray="2 4" />
            <XAxis dataKey="date" tickFormatter={(d: string) => d.slice(5)} {...AXIS} minTickGap={16} />
            <YAxis allowDecimals={false} {...AXIS} width={40} />
            <Tooltip {...TOOLTIP} cursor={{ stroke: "var(--viz-text-2)", strokeDasharray: "3 3" }} />
            <Legend
              verticalAlign="top"
              align="left"
              iconType="circle"
              wrapperStyle={{ fontSize: 12, paddingBottom: 12 }}
            />
            <Line
              type="linear"
              dataKey="queued"
              name="Queued"
              stroke={SERIES[0]}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, stroke: "var(--viz-surface)", strokeWidth: 2 }}
            />
            <Line
              type="linear"
              dataKey="decided"
              name="Decided"
              stroke={SERIES[1]}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, stroke: "var(--viz-surface)", strokeWidth: 2 }}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export function StatTile({ label, value, detail }: { label: string; value: ReactNode; detail?: ReactNode }) {
  return (
    <div className="rounded-2xl border border-slate-200 p-4 dark:border-slate-800">
      <p className="text-xs text-slate-500 dark:text-slate-400">{label}</p>
      <p className="mt-1 text-2xl font-bold tabular-nums tracking-tight">{value}</p>
      {detail && <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{detail}</p>}
    </div>
  );
}

/** Pass / fail with icon + word (status never by colour alone). */
export function StatusMark({ ok }: { ok: boolean }) {
  return ok ? (
    <span className="inline-flex items-center gap-1 font-medium text-green-700 dark:text-green-400">
      <span aria-hidden="true">✓</span> pass
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 font-medium text-red-700 dark:text-red-400">
      <span aria-hidden="true">✕</span> fail
    </span>
  );
}
