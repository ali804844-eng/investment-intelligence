import clsx from "clsx";
import { AlertTriangle, Loader2, X } from "lucide-react";
import { ReactNode, useEffect, useState } from "react";

export function Card({ title, actions, children, className, subtitle, pad = true }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; subtitle?: ReactNode; pad?: boolean;
}) {
  return (
    <section className={clsx("rounded-xl border border-line bg-panel shadow-sm min-w-0", className)}>
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 border-b border-line px-4 py-2.5">
          <div className="min-w-0">
            {title && <h2 className="text-[0.8rem] font-semibold uppercase tracking-wide text-muted">{title}</h2>}
            {subtitle && <div className="text-xs text-muted mt-0.5">{subtitle}</div>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={clsx(pad && "p-4")}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, sub, tone, hint }: { label: string; value: ReactNode; sub?: ReactNode; tone?: string; hint?: string }) {
  return (
    <div className="min-w-0" title={hint}>
      <div className="text-[0.7rem] uppercase tracking-wider text-muted">{label}</div>
      <div className={clsx("num text-lg font-semibold leading-tight mt-0.5 truncate", tone)}>{value}</div>
      {sub && <div className="text-xs text-muted mt-0.5 truncate">{sub}</div>}
    </div>
  );
}

const QUALITY: Record<string, { label: string; cls: string; title: string }> = {
  sample: { label: "SAMPLE", cls: "bg-fuchsia-500/15 text-fuchsia-500 border-fuchsia-500/40", title: "Synthetic demonstration data — not market information" },
  live: { label: "LIVE", cls: "bg-emerald-500/15 text-emerald-500 border-emerald-500/40", title: "Real-time data" },
  delayed: { label: "DELAYED", cls: "bg-amber-500/15 text-amber-500 border-amber-500/40", title: "Delayed quotes" },
  eod: { label: "EOD", cls: "bg-sky-500/15 text-sky-500 border-sky-500/40", title: "End-of-day close" },
  reported: { label: "REPORTED", cls: "bg-sky-500/15 text-sky-500 border-sky-500/40", title: "As reported in filings" },
  reference: { label: "REF", cls: "bg-sky-500/15 text-sky-500 border-sky-500/40", title: "Reference rates" },
  derived: { label: "DERIVED", cls: "bg-slate-500/15 text-muted border-line", title: "Computed from reported figures" },
  manual: { label: "MANUAL", cls: "bg-slate-500/15 text-muted border-line", title: "Manually entered" },
  stale: { label: "STALE", cls: "bg-red-500/15 text-down border-red-500/40", title: "Data older than freshness threshold" },
  missing: { label: "MISSING", cls: "bg-red-500/15 text-down border-red-500/40", title: "No data" },
  unavailable: { label: "N/A", cls: "bg-red-500/15 text-down border-red-500/40", title: "Unavailable" },
  hypothetical: { label: "HYPOTHETICAL", cls: "bg-violet-500/15 text-violet-400 border-violet-500/40", title: "Simulated / paper results" },
  actual: { label: "ACTUAL", cls: "bg-emerald-500/15 text-emerald-500 border-emerald-500/40", title: "Actual account (manually recorded)" },
};

export function QualityBadge({ q, fresh }: { q?: string | null; fresh?: { status?: string } | null }) {
  const key = fresh && fresh.status && fresh.status !== "fresh" ? fresh.status : (q || "unavailable");
  const m = QUALITY[key] || { label: key.toUpperCase(), cls: "bg-slate-500/15 text-muted border-line", title: key };
  return <span title={m.title} className={clsx("inline-flex items-center rounded border px-1.5 py-[1px] text-[0.62rem] font-semibold tracking-wider whitespace-nowrap", m.cls)}>{m.label}</span>;
}

export function Badge({ children, tone = "neutral", title }: { children: ReactNode; tone?: "neutral" | "up" | "down" | "warn" | "accent"; title?: string }) {
  const cls = {
    neutral: "bg-panel2 text-muted border-line",
    up: "bg-emerald-500/10 text-up border-emerald-500/30",
    down: "bg-red-500/10 text-down border-red-500/30",
    warn: "bg-amber-500/10 text-warn border-amber-500/30",
    accent: "bg-sky-500/10 text-accent border-sky-500/30",
  }[tone];
  return <span title={title} className={clsx("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[0.7rem] font-medium whitespace-nowrap", cls)}>{children}</span>;
}

export function Button({ children, onClick, variant = "default", disabled, type = "button", size = "md", title, className }: {
  children: ReactNode; onClick?: () => void; variant?: "default" | "primary" | "danger" | "ghost"; disabled?: boolean;
  type?: "button" | "submit"; size?: "sm" | "md"; title?: string; className?: string;
}) {
  const v = {
    default: "border-line bg-panel2 hover:bg-line/60 text-fg",
    primary: "border-transparent bg-accent text-white dark:text-slate-950 hover:opacity-90",
    danger: "border-red-500/40 bg-red-500/10 text-down hover:bg-red-500/20",
    ghost: "border-transparent hover:bg-panel2 text-muted hover:text-fg",
  }[variant];
  return (
    <button type={type} title={title} disabled={disabled} onClick={onClick}
      className={clsx("inline-flex items-center justify-center gap-1.5 rounded-lg border font-medium transition disabled:opacity-50 disabled:cursor-not-allowed",
        size === "sm" ? "px-2 py-1 text-xs" : "px-3 py-1.5 text-sm", v, className)}>
      {children}
    </button>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement> & { label?: string }) {
  const { label, className, ...rest } = props;
  const el = <input {...rest} className={clsx("w-full rounded-lg border border-line bg-panel2 px-2.5 py-1.5 text-sm outline-none focus:border-accent", className)} />;
  return label ? <label className="block text-xs text-muted space-y-1"><span>{label}</span>{el}</label> : el;
}

export function Select({ label, value, onChange, options, className }: {
  label?: string; value: string; onChange: (v: string) => void; options: { value: string; label: string }[]; className?: string;
}) {
  const el = (
    <select value={value} onChange={(e) => onChange(e.target.value)}
      className={clsx("w-full rounded-lg border border-line bg-panel2 px-2 py-1.5 text-sm outline-none focus:border-accent", className)}>
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
  return label ? <label className="block text-xs text-muted space-y-1"><span>{label}</span>{el}</label> : el;
}

export function Tabs({ tabs, value, onChange }: { tabs: { key: string; label: ReactNode }[]; value: string; onChange: (k: string) => void }) {
  return (
    <div className="flex gap-1 overflow-x-auto border-b border-line">
      {tabs.map((t) => (
        <button key={t.key} onClick={() => onChange(t.key)}
          className={clsx("whitespace-nowrap px-3 py-2 text-sm border-b-2 -mb-px transition",
            value === t.key ? "border-accent text-fg font-medium" : "border-transparent text-muted hover:text-fg")}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return <div className="flex items-center gap-2 text-sm text-muted p-4"><Loader2 className="h-4 w-4 animate-spin" />{label || "Loading…"}</div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return <div className="flex items-start gap-2 rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm text-down"><AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" /><span>{msg}</span></div>;
}

export function Notice({ children, tone = "warn" }: { children: ReactNode; tone?: "warn" | "info" | "sample" }) {
  const cls = tone === "sample" ? "border-fuchsia-500/40 bg-fuchsia-500/10" : tone === "info" ? "border-sky-500/40 bg-sky-500/10" : "border-amber-500/40 bg-amber-500/10";
  return <div className={clsx("rounded-lg border px-3 py-2 text-sm", cls)}>{children}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="p-6 text-center text-sm text-muted">{children}</div>;
}

export function Table({ head, rows, dense, className }: { head: ReactNode[]; rows: ReactNode[][]; dense?: boolean; className?: string }) {
  return (
    <div className={clsx("overflow-x-auto", className)}>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-[0.7rem] uppercase tracking-wider text-muted border-b border-line">
            {head.map((h, i) => <th key={i} className={clsx("font-medium whitespace-nowrap", dense ? "px-2 py-1.5" : "px-3 py-2")}>{h}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-line/60 hover:bg-panel2/60">
              {r.map((c, j) => <td key={j} className={clsx("whitespace-nowrap", dense ? "px-2 py-1" : "px-3 py-2")}>{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Modal({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  useEffect(() => {
    const f = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  }, [onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div role="dialog" aria-label={title} className="w-full max-w-lg rounded-xl border border-line bg-panel shadow-xl max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-line px-4 py-3">
          <h3 className="font-semibold">{title}</h3>
          <button onClick={onClose} aria-label="Close" className="text-muted hover:text-fg"><X className="h-4 w-4" /></button>
        </div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

export function useLocal<T>(key: string, initial: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => {
    try {
      const s = localStorage.getItem(key);
      return s ? (JSON.parse(s) as T) : initial;
    } catch {
      return initial;
    }
  });
  const set = (nv: T) => {
    setV(nv);
    try {
      localStorage.setItem(key, JSON.stringify(nv));
    } catch {
      /* storage unavailable */
    }
  };
  return [v, set];
}

export function KV({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
      {items.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="num text-right truncate">{v}</dd>
        </div>
      ))}
    </dl>
  );
}
