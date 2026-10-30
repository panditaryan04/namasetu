export const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 1000) / 10}%`);
export const num = (v: number | null | undefined, digits = 3) =>
  v == null ? "—" : String(Number(v.toFixed(digits)));
