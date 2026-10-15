import { createChart, ColorType, CrosshairMode, IChartApi, UTCTimestamp } from "lightweight-charts";
import { useEffect, useMemo, useRef } from "react";
import {
  CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis,
} from "recharts";
import { isNum, pct } from "../format";

export const SERIES = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)", "var(--s6)", "var(--s7)", "var(--s8)"];

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

type SeriesDef = { key: string; label: string; slot?: number; dashed?: boolean };

export function TimeSeriesChart({ data, series, height = 260, yFormat = (v: number) => pct(v, 0), refY }: {
  data: Record<string, unknown>[]; series: SeriesDef[]; height?: number; yFormat?: (v: number) => string; refY?: number;
}) {
  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="date" tick={{ fill: "var(--axis)", fontSize: 11 }} tickLine={false} axisLine={{ stroke: "var(--grid)" }}
            minTickGap={48} tickFormatter={(d: string) => (d || "").slice(0, 7)} />
          <YAxis tick={{ fill: "var(--axis)", fontSize: 11 }} tickLine={false} axisLine={false} width={52}
            tickFormatter={(v: number) => yFormat(v)} domain={["auto", "auto"]} />
          {refY !== undefined && <ReferenceLine y={refY} stroke="var(--axis)" strokeDasharray="3 3" />}
          <Tooltip
            contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 8, fontSize: 12, color: "var(--fg)" }}
            labelStyle={{ color: "var(--muted)" }}
            formatter={(v: unknown, name: string) => [isNum(v) ? yFormat(v) : "—", name]} />
          {series.length >= 2 && <Legend wrapperStyle={{ fontSize: 12, color: "var(--muted)" }} iconType="plainline" />}
          {series.map((s, i) => (
            <Line key={s.key} dataKey={s.key} name={s.label} stroke={SERIES[(s.slot ?? i) % SERIES.length]} strokeWidth={2}
              dot={false} isAnimationActive={false} strokeDasharray={s.dashed ? "5 4" : undefined} connectNulls />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function Spark({ values, width = 90, height = 26 }: { values: number[]; width?: number; height?: number }) {
  const pts = useMemo(() => {
    const v = values.filter(isNum);
    if (v.length < 2) return "";
    const lo = Math.min(...v), hi = Math.max(...v);
    const r = hi - lo || 1;
    return v.map((x, i) => `${(i / (v.length - 1)) * width},${height - 2 - ((x - lo) / r) * (height - 4)}`).join(" ");
  }, [values, width, height]);
  if (!pts) return <span className="text-muted text-xs">—</span>;
  const up = values[values.length - 1] >= values[0];
  return (
    <svg width={width} height={height} aria-hidden className="overflow-visible">
      <polyline points={pts} fill="none" stroke={up ? "var(--up)" : "var(--down)"} strokeWidth={1.5} />
    </svg>
  );
}

export function BarList({ data, format = (v: number) => pct(v, 1), max }: {
  data: { label: string; value: number }[]; format?: (v: number) => string; max?: number;
}) {
  const m = max ?? Math.max(...data.map((d) => Math.abs(d.value)), 1e-9);
  return (
    <div className="space-y-1.5">
      {data.map((d) => (
        <div key={d.label} className="grid grid-cols-[minmax(80px,140px)_1fr_auto] items-center gap-2 text-sm" title={`${d.label}: ${format(d.value)}`}>
          <span className="truncate text-muted">{d.label}</span>
          <div className="h-2.5 rounded-sm bg-panel2 overflow-hidden">
            <div className="h-full rounded-r-[4px]" style={{ width: `${Math.min(100, (Math.abs(d.value) / m) * 100)}%`, background: "var(--s1)" }} />
          </div>
          <span className="num text-xs w-14 text-right">{format(d.value)}</span>
        </div>
      ))}
    </div>
  );
}

export function CorrelationHeatmap({ symbols, matrix }: { symbols: string[]; matrix: (number | null)[][] }) {
  if (!symbols.length) return <div className="text-sm text-muted">Need at least two positions with a year of prices.</div>;
  const color = (v: number | null) => {
    if (v === null) return "var(--panel2)";
    const t = Math.min(1, Math.abs(v));
    const pole = v >= 0 ? "var(--div-pos)" : "var(--div-neg)";
    return `color-mix(in oklab, ${pole} ${Math.round(t * 100)}%, var(--div-mid))`;
  };
  return (
    <div className="overflow-x-auto">
      <table className="text-[0.65rem] num border-separate" style={{ borderSpacing: 2 }}>
        <thead>
          <tr><th />{symbols.map((s) => <th key={s} className="px-1 font-medium text-muted [writing-mode:vertical-rl] rotate-180 h-14">{s}</th>)}</tr>
        </thead>
        <tbody>
          {matrix.map((row, i) => (
            <tr key={symbols[i]}>
              <th className="pr-2 text-right font-medium text-muted">{symbols[i]}</th>
              {row.map((v, j) => (
                <td key={j} title={`${symbols[i]} / ${symbols[j]}: ${v === null ? "n/a" : v.toFixed(2)}`}
                  className="w-8 h-7 text-center rounded-[3px]" style={{ background: color(v), color: v !== null && Math.abs(v) > 0.6 ? "white" : "var(--fg)" }}>
                  {v === null ? "" : v.toFixed(1)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-2 flex items-center gap-2 text-xs text-muted">
        <span>−1</span>
        <div className="h-2 w-40 rounded" style={{ background: "linear-gradient(90deg, var(--div-neg), var(--div-mid), var(--div-pos))" }} />
        <span>+1</span><span className="ml-2">Daily return correlation, trailing 1y</span>
      </div>
    </div>
  );
}

export function CalibrationChart({ rows }: { rows: { bucket: string; n: number; avg_stated: number; realized: number }[] }) {
  if (!rows?.length) return <div className="text-sm text-muted">No evaluated predictions yet.</div>;
  const data = rows.map((r) => ({ x: r.avg_stated, y: r.realized, n: r.n, bucket: r.bucket }));
  return (
    <div style={{ height: 240 }}>
      <ResponsiveContainer>
        <ScatterChart margin={{ top: 8, right: 16, bottom: 16, left: 0 }}>
          <CartesianGrid stroke="var(--grid)" />
          <XAxis type="number" dataKey="x" domain={[0.4, 1]} name="Stated" tickFormatter={(v: number) => pct(v, 0)}
            tick={{ fill: "var(--axis)", fontSize: 11 }} label={{ value: "Stated probability", position: "insideBottom", offset: -8, fill: "var(--axis)", fontSize: 11 }} />
          <YAxis type="number" dataKey="y" domain={[0, 1]} name="Realized" tickFormatter={(v: number) => pct(v, 0)} tick={{ fill: "var(--axis)", fontSize: 11 }} width={44} />
          <ZAxis dataKey="n" range={[60, 400]} name="n" />
          <ReferenceLine segment={[{ x: 0.4, y: 0.4 }, { x: 1, y: 1 }]} stroke="var(--axis)" strokeDasharray="4 4" />
          <Tooltip contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 8, fontSize: 12 }}
            formatter={(v: unknown, n: string) => [n === "n" ? String(v) : pct(v as number, 0), n]} />
          <Scatter data={data} fill="var(--s1)" />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}

export type Bar = { time: string; open: number | null; high: number | null; low: number | null; close: number; volume: number | null };

export function PriceChart({ bars, overlays = [], height = 360, levels = [] }: {
  bars: Bar[]; overlays?: { label: string; values: (number | null)[]; slot: number }[]; height?: number;
  levels?: { price: number; label: string; tone: "up" | "down" | "neutral" }[];
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  useEffect(() => {
    if (!ref.current || !bars.length) return;
    const chart = createChart(ref.current, {
      height,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: cssVar("--axis"), fontSize: 11 },
      grid: { vertLines: { visible: false }, horzLines: { color: cssVar("--grid") } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
      crosshair: { mode: CrosshairMode.Normal },
      autoSize: true,
    });
    chartRef.current = chart;
    const t = (s: string) => (Date.parse(s + "T00:00:00Z") / 1000) as UTCTimestamp;
    const candle = chart.addCandlestickSeries({
      upColor: cssVar("--up"), downColor: cssVar("--down"), wickUpColor: cssVar("--up"), wickDownColor: cssVar("--down"), borderVisible: false,
    });
    candle.setData(bars.map((b) => ({ time: t(b.time), open: b.open ?? b.close, high: b.high ?? b.close, low: b.low ?? b.close, close: b.close })));
    const vol = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, color: cssVar("--grid") });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(bars.map((b) => ({ time: t(b.time), value: b.volume ?? 0 })));
    overlays.forEach((o) => {
      const ls = chart.addLineSeries({ color: cssVar(`--s${o.slot}`), lineWidth: 2, priceLineVisible: false, lastValueVisible: false, title: o.label });
      ls.setData(bars.map((b, i) => ({ time: t(b.time), value: o.values[i] })).filter((p) => isNum(p.value)) as { time: UTCTimestamp; value: number }[]);
    });
    levels.forEach((l) => candle.createPriceLine({
      price: l.price, title: l.label, lineWidth: 1, lineStyle: 2, axisLabelVisible: true,
      color: l.tone === "up" ? cssVar("--up") : l.tone === "down" ? cssVar("--down") : cssVar("--axis"),
    }));
    chart.timeScale().fitContent();
    return () => { chart.remove(); chartRef.current = null; };
  }, [bars, overlays, height, levels]);
  return <div ref={ref} style={{ height }} className="w-full" />;
}

export function smaSeries(values: number[], n: number): (number | null)[] {
  const out: (number | null)[] = [];
  let s = 0;
  values.forEach((v, i) => {
    s += v;
    if (i >= n) s -= values[i - n];
    out.push(i >= n - 1 ? s / n : null);
  });
  return out;
}
