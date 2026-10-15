export const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function pct(v: unknown, digits = 1, signed = false): string {
  if (!isNum(v)) return "—";
  const s = (v * 100).toFixed(digits) + "%";
  return signed && v > 0 ? "+" + s : s;
}

export function num(v: unknown, digits = 2): string {
  if (!isNum(v)) return "—";
  return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function money(v: unknown, ccy?: string, digits = 2): string {
  if (!isNum(v)) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e12) return `${(v / 1e12).toFixed(2)}T${ccy ? " " + ccy : ""}`;
  if (abs >= 1e9) return `${(v / 1e9).toFixed(2)}B${ccy ? " " + ccy : ""}`;
  if (abs >= 1e6) return `${(v / 1e6).toFixed(2)}M${ccy ? " " + ccy : ""}`;
  return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits }) + (ccy ? " " + ccy : "");
}

export function fmtFeature(v: unknown, fmt?: string): string {
  if (!isNum(v)) return "—";
  switch (fmt) {
    case "pct": return pct(v);
    case "x": return num(v, 1) + "x";
    case "money": return money(v, undefined, 0);
    case "bool": return v ? "Yes" : "No";
    default: return num(v, 2);
  }
}

export function dt(s?: string | null, withTime = false): string {
  if (!s) return "—";
  const d = new Date(s);
  if (isNaN(d.getTime())) return s;
  return withTime ? d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : d.toLocaleDateString(undefined, { dateStyle: "medium" });
}

export function ago(s?: string | null): string {
  if (!s) return "never";
  const ms = Date.now() - new Date(s).getTime();
  const m = Math.round(ms / 60000);
  if (Math.abs(m) < 1) return "just now";
  if (m < 0) {
    const f = -m;
    return f < 60 ? `in ${f}m` : f < 1440 ? `in ${Math.round(f / 60)}h` : `in ${Math.round(f / 1440)}d`;
  }
  return m < 60 ? `${m}m ago` : m < 1440 ? `${Math.round(m / 60)}h ago` : `${Math.round(m / 1440)}d ago`;
}

export const signClass = (v: unknown) => (!isNum(v) ? "text-muted" : v > 0 ? "text-up" : v < 0 ? "text-down" : "text-fg");
