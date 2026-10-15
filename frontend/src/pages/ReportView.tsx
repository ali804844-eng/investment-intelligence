import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Printer } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { Badge, Button, Card, ErrorBox, KV, Notice, QualityBadge, Spinner, Table } from "../components/ui";
import { dt, money, num, pct } from "../format";
import { ConclusionBadge } from "./Dashboard";

const LABELS: Record<string, string> = {
  revenue_cagr_3y: "Revenue CAGR 3y", eps_cagr_3y: "EPS CAGR 3y", gross_margin: "Gross margin", operating_margin: "Operating margin",
  fcf_margin: "FCF margin", fcf_consistency: "FCF-positive years", roic_avg: "ROIC (avg)", net_debt_to_ebitda: "Net debt / EBITDA",
  interest_coverage: "Interest coverage", share_count_cagr_3y: "Share count CAGR", sbc_to_revenue: "SBC / revenue", mom_12_1: "Momentum 12-1",
  mom_6m: "Return 6m", vol_252d: "Volatility 1y", max_dd_1y: "Max drawdown 1y", adv_usd_20d: "$ volume/day", rsi14: "RSI(14)", dist_52w_high: "From 52w high",
};
const RATIO = new Set(["net_debt_to_ebitda", "interest_coverage", "rsi14"]);

export default function ReportView() {
  const { id } = useParams();
  const q = useQuery({ queryKey: ["report", id], queryFn: () => api.get(`/api/research/reports/${id}`) });
  if (q.isLoading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const r = q.data;
  const s = r.sections;
  const v = s.valuation;
  const fmt = (k: string, x: any) => (k === "adv_usd_20d" ? money(x, undefined, 0) : RATIO.has(k) ? num(x, 1) : pct(x));
  return (
    <article className="space-y-4 max-w-6xl">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-xl font-semibold">{r.title}</h1><ConclusionBadge c={r.conclusion} /><QualityBadge q={r.data_quality} />
          </div>
          <p className="text-sm text-muted">Report #{r.id} · <Link to={`/research/${r.symbol}`} className="text-accent">{r.symbol}</Link> {r.name} · {r.horizon}-term · generated {dt(r.created_at, true)} by {r.created_by} · {r.model_version} · narrative: {r.narrative_by}</p>
        </div>
        <Button size="sm" onClick={() => window.print()}><Printer className="h-3.5 w-3.5" />Print / PDF</Button>
      </div>
      {r.is_sample && <Notice tone="sample">This report is about a fictional SAMPLE company. It demonstrates the research workflow only.</Notice>}
      <Card title="Executive summary"><p className="text-sm">{s.executive_summary}</p>
        {s.narrative && <div className="mt-3 border-t border-line pt-3"><div className="text-xs text-muted mb-1">AI narrative ({r.narrative_by}) — written only from the computed data in this report</div><p className="text-sm whitespace-pre-wrap">{s.narrative}</p></div>}
      </Card>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Security & data timestamps">
          <KV items={[["Symbol", s.identification.symbol], ["Exchange", s.identification.exchange], ["Currency", s.identification.currency], ["Sector", s.identification.sector || "—"],
            ["Price", num(s.identification.price)], ["Price as of", dt(s.identification.price_as_of, true)], ["Price quality", s.identification.price_quality],
            ["Statements filed", s.identification.statements_latest_filed || "—"]]} />
        </Card>
        <Card title="Investment thesis" className="lg:col-span-2"><ul className="list-disc pl-5 text-sm space-y-1">{s.thesis.map((t: string) => <li key={t}>{t}</li>)}</ul></Card>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Fundamental evidence"><KV items={Object.entries(s.evidence.fundamental).map(([k, x]) => [LABELS[k] || k, fmt(k, x)])} /></Card>
        <Card title="Quantitative evidence"><KV items={Object.entries(s.evidence.quantitative).map(([k, x]) => [LABELS[k] || k, fmt(k, x)])} />
          {s.evidence.consistency_checks_failed?.length > 0 && <p className="text-xs text-warn mt-2">{s.evidence.consistency_checks_failed.length} data consistency check(s) failed.</p>}</Card>
      </div>
      <Card title="Valuation & scenarios">
        {v?.available ? (
          <div className="grid gap-4 lg:grid-cols-3 text-sm">
            <div>
              <KV items={[["Base DCF / share", num(v.dcf.value_per_share)], ["Price", num(v.price)], ["Margin of safety", pct(v.margin_of_safety?.margin_of_safety, 0)],
                ["Bear", num(v.scenarios.scenarios.bear.value_per_share)], ["Base", num(v.scenarios.scenarios.base.value_per_share)], ["Bull", num(v.scenarios.scenarios.bull.value_per_share)],
                ["Peer-median value", num(v.comparables?.relative_valuation?.median_value_per_share)], ["Terminal share of EV", pct(v.dcf.terminal_share_of_ev, 0)]]} />
            </div>
            <div className="lg:col-span-2">
              <Table dense head={["Assumption", "Value", "Basis"]} rows={v.assumptions.map((a: any) => [a.name, a.fmt === "x" ? num(a.value, 2) : a.fmt === "money" ? money(a.value, undefined, 0) : pct(a.value, 2), <span className="text-xs text-muted whitespace-normal">{a.basis}</span>])} />
            </div>
          </div>
        ) : <p className="text-sm text-muted">Valuation unavailable: {(v?.reasons || []).join("; ")}</p>}
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Market & macro context"><p className="text-sm"><b>{s.market_context.regime}</b> — {(s.market_context.explanation || []).join(" ")}</p><p className="text-xs text-muted mt-1">{s.market_context.caveat}</p></Card>
        <Card title="Portfolio fit">
          {s.portfolio_fit ? <KV items={[["Portfolio", s.portfolio_fit.portfolio], ["Already held", pct(s.portfolio_fit.already_held_weight)], ["Sector weight now", pct(s.portfolio_fit.sector_weight_now)],
            ["Max new weight (limits)", pct(s.portfolio_fit.suggested_max_new_weight)]]} /> : <p className="text-sm text-muted">No portfolio selected for fit analysis.</p>}
          {s.portfolio_fit && <p className="text-xs text-muted mt-1">{s.portfolio_fit.note}</p>}
        </Card>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Major risks">
          <ul className="space-y-1 text-sm">{s.risks.length ? s.risks.map((x: any) => <li key={x.code}><Badge tone={x.severity === "high" ? "down" : x.severity === "medium" ? "warn" : "neutral"}>{x.severity}</Badge> {x.message}</li>) : <li className="text-muted">No rule-based risk flags.</li>}</ul>
        </Card>
        <Card title="Counterarguments (bear case)"><ul className="list-disc pl-5 text-sm space-y-1">{s.counterarguments.map((c: string) => <li key={c}>{c}</li>)}</ul></Card>
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Horizon & invalidation"><p className="text-sm">Expected holding period: <b>{s.horizon.expected_holding}</b></p><ul className="list-disc pl-5 text-sm mt-2">{s.horizon.invalidation.map((x: string) => <li key={x}>{x}</li>)}</ul></Card>
        <Card title="Historical strategy tests">
          {s.strategy_tests?.metrics ? <KV items={[["Backtest", `#${s.strategy_tests.backtest_id}`], ["CAGR", pct(s.strategy_tests.metrics.cagr)], ["Sharpe", num(s.strategy_tests.metrics.sharpe)],
            ["Excess vs benchmark", pct(s.strategy_tests.metrics.excess_cagr)], ["Data", s.strategy_tests.data_quality]]} /> : <p className="text-sm text-muted">{s.strategy_tests?.note}</p>}
        </Card>
        <Card title="Confidence & uncertainty">
          <KV items={[["Evidence strength", s.confidence.evidence_strength], ["DCF vs comps disagreement", pct(s.confidence.valuation_method_disagreement, 0)],
            ["Calibrated?", s.confidence.calibrated ? `yes (hit rate ${pct(s.confidence.hit_rate, 0)}, n=${s.confidence.n_evaluated})` : "no"]]} />
          {!s.confidence.calibrated && <p className="text-xs text-muted mt-1">{s.confidence.explanation}</p>}
        </Card>
      </div>
      {s.short_term_setups?.length > 0 && <Card title="Short-term setups"><ul className="text-sm list-disc pl-5">{s.short_term_setups.map((x: any) => <li key={x.setup}>{x.title}: entry {num(x.entry_reference)}, stop {num(x.invalidation.stop)}, target {num(x.exits[0].price)} — {x.evidence_label}</li>)}</ul></Card>}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Data limitations"><ul className="list-disc pl-5 text-sm text-muted">{s.data_limitations.length ? s.data_limitations.map((x: string) => <li key={x}>{x}</li>) : <li>None identified.</li>}</ul></Card>
        <Card title="Reconsider the thesis if"><ul className="list-disc pl-5 text-sm">{s.reconsider_if.map((x: string) => <li key={x}>{x}</li>)}</ul></Card>
      </div>
      <Card title="Sources" pad={false}>
        <Table dense head={["Type", "Source", "Date / as of", "Detail"]} rows={r.sources.map((x: any) => [x.type, x.source, x.as_of || x.date || x.latest_filed || "—",
          x.url ? <a href={x.url} target="_blank" rel="noopener noreferrer" className="text-accent inline-flex items-center gap-1">{x.title || x.form || "link"}<ExternalLink className="h-3 w-3" />{x.untrusted && " (untrusted)"}</a> : (x.form || x.quality || "")])} />
      </Card>
      <Card title="Calculations">
        <div className="space-y-3 text-sm">{r.calculations.map((c: any) => (
          <div key={c.name}><b>{c.name}</b><div className="num text-xs text-muted">{c.formula}</div>
            <pre className="mt-1 overflow-x-auto rounded bg-panel2 p-2 text-xs num">{JSON.stringify(c.output, null, 1).slice(0, 1200)}</pre></div>
        ))}</div>
      </Card>
      <p className="text-xs text-muted">This report is an analytical estimate under stated assumptions, not investment advice. No conclusion implies a guaranteed outcome.</p>
    </article>
  );
}
