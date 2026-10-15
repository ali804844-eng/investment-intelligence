import { useMutation } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { TimeSeriesChart } from "../components/charts";
import { Button, Card, Empty, ErrorBox, Input, Notice, QualityBadge, Spinner, Table, Tabs } from "../components/ui";
import { money, num, pct } from "../format";

const PCT_METRICS = new Set(["gross_margin", "operating_margin", "net_margin", "fcf_margin", "roic", "roe", "roa", "effective_tax_rate", "capex_to_revenue",
  "sbc_to_revenue", "payout_ratio", "fcf_payout_ratio", "accruals_ratio", "fcf_conversion"]);

function fmtMetric(k: string, v: unknown) {
  if (typeof v !== "number") return "—";
  if (PCT_METRICS.has(k)) return pct(v);
  if (["debt_to_equity", "net_debt_to_ebitda", "interest_coverage", "current_ratio"].includes(k)) return num(v, 2);
  if (k === "eps_diluted") return num(v, 2);
  return money(v, undefined, 0);
}

export default function Lab() {
  const [sp] = useSearchParams();
  const [tab, setTab] = useState("compare");
  const [symbols, setSymbols] = useState(sp.get("symbol") || "NWND, LITW, HUML");
  const syms = symbols.split(/[ ,]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Financial research lab</h1>
        <p className="text-sm text-muted">All figures come from stored statements; every ratio and formula is computed by audited code, never by the language model.</p>
      </div>
      <div className="max-w-xl"><Input label="Companies (comma separated)" value={symbols} onChange={(e) => setSymbols(e.target.value)} /></div>
      <Tabs value={tab} onChange={setTab} tabs={[{ key: "compare", label: "Compare statements & ratios" }, { key: "formula", label: "Custom formula" }, { key: "export", label: "Export" }]} />
      {tab === "compare" && <Compare syms={syms} />}
      {tab === "formula" && <Formula syms={syms} />}
      {tab === "export" && (
        <Card title="Export statements (CSV)">
          <div className="flex flex-wrap gap-2">{syms.map((s) => <a key={s} href={`/api/lab/export/${s}.csv`}><Button><Download className="h-4 w-4" />{s}.csv</Button></a>)}</div>
          <p className="text-xs text-muted mt-2">Exports include a header line naming the sources and whether the data is SAMPLE. Charts can be saved via your browser (right-click → save image / print to PDF).</p>
        </Card>
      )}
    </div>
  );
}

const DEFAULT_METRICS = "revenue, operating_income, net_income, fcf, operating_margin, roic, net_debt_to_ebitda, sbc_to_revenue";

function Compare({ syms }: { syms: string[] }) {
  const [metrics, setMetrics] = useState(DEFAULT_METRICS);
  const [years, setYears] = useState("6");
  const m = useMutation({ mutationFn: () => api.post("/api/lab/compare", { symbols: syms, metrics: metrics.split(/[ ,]+/).filter(Boolean), years: Number(years) || 5 }) });
  return (
    <div className="space-y-4">
      <Card title="Selection">
        <div className="grid gap-2 md:grid-cols-[1fr_120px_auto] items-end">
          <Input label="Metrics (reported line items or derived ratios)" value={metrics} onChange={(e) => setMetrics(e.target.value)} />
          <Input label="Years" value={years} onChange={(e) => setYears(e.target.value)} />
          <Button variant="primary" onClick={() => m.mutate()} disabled={m.isPending}>Compare</Button>
        </div>
        {m.data && <p className="text-xs text-muted mt-2">Reported: {m.data.available_metrics.reported.join(", ")}. Derived: {m.data.available_metrics.derived.join(", ")}.</p>}
      </Card>
      {m.isPending && <Spinner />}
      <ErrorBox error={m.error} />
      {m.data && Object.keys(m.data.results).length > 0 && (() => {
        const res = m.data.results;
        const first = Object.keys(Object.values<any>(res)[0].values);
        return (
          <div className="grid gap-4 xl:grid-cols-2">
            {first.map((metric) => {
              const periods = Array.from(new Set(Object.values<any>(res).flatMap((r) => r.periods))).sort();
              const rows = periods.map((p) => ({ date: p, ...Object.fromEntries(Object.entries<any>(res).map(([s, r]) => [s, r.values[metric]?.[p] ?? null])) }));
              const hasData = rows.some((r: any) => Object.keys(res).some((s) => typeof r[s] === "number"));
              return (
                <Card key={metric} title={metric.replace(/_/g, " ")}>
                  {hasData ? (
                    <>
                      <TimeSeriesChart height={180} data={rows} yFormat={(v) => fmtMetric(metric, v)} series={Object.keys(res).slice(0, 8).map((s, i) => ({ key: s, label: s, slot: i }))} />
                      <Table dense head={["Period", ...Object.keys(res)]} rows={rows.map((r: any) => [r.date.slice(0, 4), ...Object.keys(res).map((s) => <span className="num">{fmtMetric(metric, r[s])}</span>)])} />
                    </>
                  ) : <Empty>Not available for the selected companies.</Empty>}
                </Card>
              );
            })}
            <Card title="Data quality & consistency" className="xl:col-span-2">
              {Object.entries<any>(res).map(([s, r]) => (
                <div key={s} className="text-sm mb-2"><b className="num">{s}</b> {r.is_sample && <QualityBadge q="sample" />} <span className="text-muted">({r.currency})</span>:{" "}
                  {r.checks.filter((c: any) => !c.ok).length ? r.checks.filter((c: any) => !c.ok).map((c: any) => `${c.period} ${c.check}`).join("; ") : "all consistency checks pass"}</div>
              ))}
              {new Set(Object.values<any>(res).map((x) => x.currency)).size > 1 && <Notice>Companies report in different currencies; absolute amounts are not directly comparable.</Notice>}
            </Card>
          </div>
        );
      })()}
    </div>
  );
}

function Formula({ syms }: { syms: string[] }) {
  const [f, setF] = useState("(operating_cash_flow - capex - sbc) / revenue");
  const m = useMutation({ mutationFn: () => api.post("/api/lab/formula", { symbols: syms, formula: f, years: 8 }) });
  return (
    <div className="space-y-4">
      <Card title="Formula" subtitle="Arithmetic (+ − × ÷ **, abs/min/max/sqrt/log) over reported metrics and derived ratios. Evaluated safely server-side.">
        <div className="flex gap-2 items-end">
          <div className="flex-1"><Input value={f} onChange={(e) => setF(e.target.value)} className="num" /></div>
          <Button variant="primary" onClick={() => m.mutate()} disabled={m.isPending}>Evaluate</Button>
        </div>
        <p className="text-xs text-muted mt-2">Examples: <code>(operating_cash_flow - capex) / net_income</code> · <code>total_liabilities / total_assets</code> · <code>buybacks / (fcf + 1)</code></p>
      </Card>
      <ErrorBox error={m.error} />
      {m.data && (() => {
        const periods = Array.from(new Set(Object.values<any>(m.data.results).flatMap((r) => Object.keys(r.values)))).sort();
        const rows = periods.map((p) => ({ date: p, ...Object.fromEntries(Object.entries<any>(m.data.results).map(([s, r]) => [s, r.values[p] ?? null])) }));
        return (
          <Card title={m.data.formula} subtitle={`Variables: ${m.data.variables.join(", ")}`}>
            <TimeSeriesChart height={220} yFormat={(v) => num(v, 3)} data={rows} series={Object.keys(m.data.results).map((s, i) => ({ key: s, label: s, slot: i }))} />
            <Table dense head={["Period", ...Object.keys(m.data.results)]} rows={rows.map((r: any) => [r.date.slice(0, 4), ...Object.keys(m.data.results).map((s) => <span className="num">{typeof r[s] === "number" ? num(r[s], 4) : "—"}</span>)])} />
            <p className="text-xs text-muted mt-2">{m.data.note}</p>
          </Card>
        );
      })()}
    </div>
  );
}
