import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Download, ExternalLink, RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { PriceChart, smaSeries } from "../components/charts";
import { Badge, Button, Card, Empty, ErrorBox, Input, KV, Notice, QualityBadge, Select, Spinner, Stat, Table, Tabs } from "../components/ui";
import { dt, fmtFeature, money, num, pct, signClass } from "../format";
import { ConclusionBadge } from "./Dashboard";
import { SetupCard } from "./Trading";

export default function SecurityPage() {
  const { symbol = "" } = useParams();
  const [tab, setTab] = useState("overview");
  const ov = useQuery({ queryKey: ["overview", symbol], queryFn: () => api.get(`/api/securities/${symbol}/overview`) });
  const qc = useQueryClient();
  const nav = useNavigate();
  const refresh = useMutation({ mutationFn: () => api.post(`/api/securities/${symbol}/refresh`), onSuccess: () => qc.invalidateQueries() });
  const report = useMutation({ mutationFn: (horizon: string) => api.post("/api/research/reports", { symbol, horizon }), onSuccess: (r) => nav(`/research/reports/${r.id}`) });
  const wl = useQuery({ queryKey: ["watchlists"], queryFn: () => api.get("/api/watchlists") });
  const addWl = useMutation({ mutationFn: () => api.post(`/api/watchlists/${wl.data[0].id}/items`, { symbol }), onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlists"] }) });
  if (ov.isLoading) return <Spinner />;
  if (ov.error) return <ErrorBox error={ov.error} />;
  const o = ov.data;
  const s = o.security;
  const q = o.quote;
  const onWl = wl.data?.some((w: any) => w.items.some((i: any) => i.symbol === s.symbol));
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-xl font-semibold"><span className="num">{s.symbol}</span> <span className="text-muted font-normal">{s.name}</span></h1>
            {s.is_sample && <QualityBadge q="sample" />}
          </div>
          <div className="text-sm text-muted">{[s.exchange, s.asset_class, s.sector, s.industry, s.country, s.currency].filter(Boolean).join(" · ")}</div>
          {s.is_sample && <p className="text-xs text-muted mt-1">{s.description}</p>}
        </div>
        <div className="flex items-end gap-4">
          <div className="text-right">
            <div className="num text-2xl font-semibold">{num(q.price)} <span className="text-sm text-muted">{s.currency}</span></div>
            <div className={`num text-sm ${signClass(q.change_pct)}`}>{pct(q.change_pct, 2, true)}</div>
            <div className="text-[0.7rem] text-muted flex items-center gap-1 justify-end">{q.source} · {dt(q.as_of, true)} <QualityBadge q={q.quality} fresh={q.freshness} /></div>
          </div>
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" onClick={() => report.mutate("long")} disabled={report.isPending}>{report.isPending ? "Generating report…" : "Long-term report"}</Button>
        <Button onClick={() => report.mutate("short")} disabled={report.isPending}>Short-term report</Button>
        {wl.data?.[0] && <Button onClick={() => addWl.mutate()} disabled={onWl || addWl.isPending}>{onWl ? "On watchlist" : "Add to watchlist"}</Button>}
        <Button onClick={() => refresh.mutate()} disabled={refresh.isPending} title="Re-fetch from configured providers"><RefreshCw className="h-3.5 w-3.5" />{refresh.isPending ? "Refreshing…" : "Refresh data"}</Button>
        <Link to={`/lab?symbol=${s.symbol}`}><Button>Open in Financial Lab</Button></Link>
      </div>
      <ErrorBox error={report.error || addWl.error || refresh.error} />
      {refresh.data && <Notice tone="info">Refresh: {Object.entries(refresh.data).map(([k, v]: any) => `${k}: ${v.status}${v.error ? ` (${v.error})` : ""}`).join(" · ")}</Notice>}
      <Tabs value={tab} onChange={setTab} tabs={[
        { key: "overview", label: "Overview" }, { key: "financials", label: "Financials" }, { key: "valuation", label: "Valuation" },
        { key: "trading", label: `Short-term${o.setups.length ? ` (${o.setups.length})` : ""}` }, { key: "filings", label: "Filings & news" }, { key: "reports", label: "Reports & predictions" },
      ]} />
      {tab === "overview" && <OverviewTab o={o} />}
      {tab === "financials" && <Financials symbol={s.symbol} />}
      {tab === "valuation" && <Valuation symbol={s.symbol} isEquity={s.asset_class === "equity"} />}
      {tab === "trading" && <TradingTab o={o} symbol={s.symbol} />}
      {tab === "filings" && <FilingsNews o={o} />}
      {tab === "reports" && <ReportsTab symbol={s.symbol} />}
    </div>
  );
}

function useBars(symbol: string, days = 900) {
  return useQuery({ queryKey: ["bars", symbol, days], queryFn: () => api.get(`/api/securities/${symbol}/bars?days=${days}`) });
}

function OverviewTab({ o }: { o: any }) {
  const bars = useBars(o.security.symbol);
  const overlays = useMemo(() => {
    const c = (bars.data?.bars || []).map((b: any) => b.close);
    return [{ label: "SMA50", values: smaSeries(c, 50), slot: 1 }, { label: "SMA200", values: smaSeries(c, 200), slot: 2 }];
  }, [bars.data]);
  const groups: Record<string, [string, any][]> = {};
  Object.entries(o.features).forEach(([k, v]: any) => { (groups[v.group] ||= []).push([k, v]); });
  return (
    <div className="space-y-4">
      <Card title="Price" subtitle={bars.data && <span>Daily bars · {bars.data.source} · SMA50 / SMA200 overlays <QualityBadge q={bars.data.quality} /></span>} pad={false}>
        {bars.isLoading ? <Spinner /> : bars.data?.bars?.length ? <div className="p-2"><PriceChart bars={bars.data.bars} overlays={overlays} /></div> : <Empty>No price history.</Empty>}
      </Card>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {Object.entries(groups).filter(([g]) => g !== "data").map(([g, items]) => (
          <Card key={g} title={g}>
            <KV items={items.map(([, v]) => [v.label, fmtFeature(v.value, v.fmt)])} />
          </Card>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Risk flags" subtitle="Rule-based, from the deterministic engine">
          {o.risk_flags.length ? <ul className="space-y-1.5 text-sm">{o.risk_flags.map((f: any) => <li key={f.code} className="flex gap-2"><Badge tone={f.severity === "high" ? "down" : f.severity === "medium" ? "warn" : "neutral"}>{f.severity}</Badge>{f.message}</li>)}</ul> : <Empty>No flags.</Empty>}
        </Card>
        <Card title="Upcoming events">
          {o.events.length ? <Table dense head={["Date", "Event", "In", "Source"]} rows={o.events.map((e: any) => [dt(e.date), e.kind, `${e.days_until}d`, <span>{e.source} <QualityBadge q={e.quality} /></span>])} /> : <Empty>No events from configured sources.</Empty>}
        </Card>
      </div>
    </div>
  );
}

function Financials({ symbol }: { symbol: string }) {
  const q = useQuery({ queryKey: ["statements", symbol], queryFn: () => api.get(`/api/securities/${symbol}/statements`) });
  const [stmt, setStmt] = useState("income");
  if (q.isLoading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data;
  if (!Object.keys(d.statements).length) return <Card><Empty>No financial statements stored. For US companies configure SEC_USER_AGENT (SEC EDGAR, free); statements are never estimated or invented.</Empty></Card>;
  const periods: string[] = d.analysis.periods.slice(-8);
  const metrics = Object.entries(d.metrics).filter(([k, m]: any) => m.statement === stmt && d.statements[k]);
  const ratioKeys = ["gross_margin", "operating_margin", "net_margin", "fcf", "fcf_margin", "roic", "roe", "net_debt_to_ebitda", "interest_coverage", "current_ratio", "sbc_to_revenue", "payout_ratio", "accruals_ratio"];
  const failed = d.analysis.checks.filter((c: any) => !c.ok);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Select value={stmt} onChange={setStmt} className="w-48" options={[{ value: "income", label: "Income statement" }, { value: "balance", label: "Balance sheet" }, { value: "cashflow", label: "Cash flow" }]} />
        <span className="text-muted">Sources: {d.sources.join(", ")} · latest filing {d.latest_filed} · units {d.currency}</span>
        {d.qualities.map((x: string) => <QualityBadge key={x} q={x} />)}
        <a href={`/api/lab/export/${symbol}.csv`} className="ml-auto"><Button size="sm"><Download className="h-3.5 w-3.5" />CSV</Button></a>
      </div>
      <Card pad={false}>
        <Table dense head={["Line item", ...periods.map((p) => p.slice(0, 4))]} rows={metrics.map(([k, m]: any) => [
          m.label, ...periods.map((p) => {
            const v = d.statements[k][p];
            const pv = d.provenance[`${k}|${p}`];
            return <span className="num" title={pv ? `${pv.source} · ${pv.ref || ""} · filed ${pv.filed} · ${pv.quality}` : "not reported"}>{v === undefined ? "—" : k === "eps_diluted" ? num(v) : money(v, undefined, 0)}</span>;
          }),
        ])} />
        <p className="px-3 py-2 text-xs text-muted">Hover a value for its filing reference. Missing cells were not reported under mapped tags.</p>
      </Card>
      <Card title="Ratios & quality (derived in code)" pad={false}>
        <Table dense head={["Ratio", ...periods.map((p) => p.slice(0, 4))]} rows={ratioKeys.map((k) => [
          k.replace(/_/g, " "), ...periods.map((p) => { const v = d.analysis.by_period[p]?.[k]; return <span className="num">{k === "fcf" ? money(v, undefined, 0) : ["net_debt_to_ebitda", "interest_coverage", "current_ratio"].includes(k) ? num(v, 1) : pct(v)}</span>; }),
        ])} />
      </Card>
      <Card title="Consistency checks" subtitle={`${failed.length} failed of ${d.analysis.checks.length}`}>
        {failed.length ? <ul className="text-sm space-y-1">{failed.map((c: any, i: number) => <li key={i} className="flex gap-2"><AlertTriangle className="h-4 w-4 text-warn shrink-0" />{c.period}: {c.check} — {c.detail}</li>)}</ul> : <p className="text-sm text-muted">All checks passed.</p>}
      </Card>
    </div>
  );
}

function Valuation({ symbol, isEquity }: { symbol: string; isEquity: boolean }) {
  const [ov, setOv] = useState<Record<string, string>>({});
  const body = Object.fromEntries(Object.entries(ov).filter(([, v]) => v !== "").map(([k, v]) => [k, k === "years" ? parseInt(v) : Number(v)]));
  const q = useQuery({ queryKey: ["valuation", symbol, JSON.stringify(body)], queryFn: () => api.post(`/api/securities/${symbol}/valuation`, body), enabled: isEquity });
  if (!isEquity) return <Card><Empty>Intrinsic-value models apply to operating companies only.</Empty></Card>;
  if (q.isLoading) return <Spinner label="Running valuation models…" />;
  if (q.error) return <ErrorBox error={q.error} />;
  const v = q.data;
  if (!v.available) return <Card title="Valuation unavailable"><ul className="text-sm list-disc pl-5">{v.reasons.map((r: string) => <li key={r}>{r}</li>)}</ul></Card>;
  const field = (k: string, label: string, ph: string) => <Input label={label} placeholder={ph} value={ov[k] || ""} onChange={(e) => setOv({ ...ov, [k]: e.target.value })} />;
  const mos = v.margin_of_safety?.margin_of_safety;
  return (
    <div className="space-y-4">
      <div className="grid gap-3 grid-cols-2 md:grid-cols-4">
        <Card><Stat label="DCF value / share" value={num(v.dcf.value_per_share)} sub={`${v.currency} · base case`} /></Card>
        <Card><Stat label="Price" value={num(v.price)} sub={<span>as of {dt(v.price_as_of)} <QualityBadge q={v.price_quality} /></span>} /></Card>
        <Card><Stat label="Margin of safety" value={pct(mos, 0)} tone={signClass(mos)} sub={`(Intrinsic − Price) / Intrinsic · upside ${pct(v.margin_of_safety?.upside, 0, true)}`} /></Card>
        <Card><Stat label="Prob.-weighted value" value={num(v.scenarios.probability_weighted_value)} sub="25/50/25 subjective weights" /></Card>
      </div>
      <Notice>Intrinsic value is an estimate under the assumptions below — not an objective number. Change inputs to see how sensitive it is.</Notice>
      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="Assumptions & basis" className="xl:col-span-2">
          <Table dense head={["Input", "Value", "Basis"]} rows={v.assumptions.map((a: any) => [a.name, <span className="num">{a.fmt === "x" ? num(a.value, 2) : a.fmt === "money" ? money(a.value, undefined, 0) : pct(a.value, 2)}</span>, <span className="text-xs text-muted whitespace-normal">{a.basis}</span>])} />
          <div className="mt-3 grid gap-2 grid-cols-2 md:grid-cols-5">
            {field("discount_rate", "Discount rate", "e.g. 0.09")}{field("terminal_growth", "Terminal growth", "e.g. 0.025")}
            {field("initial_growth", "Initial growth", "e.g. 0.08")}{field("years", "Explicit years", "5")}{field("equity_risk_premium", "ERP", "0.05")}
          </div>
          <p className="text-xs text-muted mt-2 num">{v.dcf.formula}</p>
          {v.dcf.warnings.map((w: string) => <p key={w} className="text-xs text-warn mt-1">⚠ {w}</p>)}
        </Card>
        <Card title="Scenarios">
          <Table dense head={["Case", "Value/share", "vs price"]} rows={Object.entries(v.scenarios.scenarios).map(([k, s]: any) => [k, <span className="num">{num(s.value_per_share)}</span>, <span className={`num ${signClass(s.value_per_share / v.price - 1)}`}>{pct(s.value_per_share / v.price - 1, 0, true)}</span>])} />
          <p className="text-xs text-muted mt-2">{v.scenarios.note}</p>
          <h4 className="text-sm font-medium mt-3">DCF detail</h4>
          <KV items={[["Enterprise value", money(v.dcf.enterprise_value)], ["Net debt", money(v.dcf.inputs.net_debt)], ["Equity value", money(v.dcf.equity_value)],
            ["PV terminal / EV", pct(v.dcf.terminal_share_of_ev, 0)], ["Shares", money(v.dcf.inputs.shares_outstanding, undefined, 0)]]} />
        </Card>
      </div>
      <Card title="Sensitivity: value per share" subtitle="Rows: discount rate · Columns: terminal growth">
        <Table dense head={["r \\ g", ...v.sensitivity.terminal_growths.map((g: number) => pct(g, 1))]} rows={v.sensitivity.values.map((row: (number | null)[], i: number) => [
          <b className="num">{pct(v.sensitivity.discount_rates[i], 1)}</b>,
          ...row.map((x) => <span className={`num ${x === null ? "text-muted" : x > v.price ? "text-up" : "text-down"}`}>{x === null ? "n/a" : num(x)}</span>),
        ])} />
      </Card>
      <Card title="Comparable companies" subtitle={v.comparables.note}>
        {v.comparables.peers?.length ? (
          <>
            <Table dense head={["Symbol", "P/E", "EV/EBITDA", "P/S", "FCF yield", "ROIC", "Rev CAGR 3y"]} rows={[...(v.comparables.target ? [v.comparables.target] : []), ...v.comparables.peers].map((p: any) => [
              <span className={`num ${p.is_target ? "font-bold text-accent" : ""}`}>{p.symbol}{p.is_target ? " (target)" : ""}</span>, num(p.pe, 1), num(p.ev_ebitda, 1), num(p.ps, 1), pct(p.fcf_yield), pct(p.roic_avg), pct(p.revenue_cagr_3y),
            ])} />
            {v.comparables.relative_valuation && (
              <div className="mt-2 text-sm">Peer-median implied values: {Object.entries(v.comparables.relative_valuation.estimates).map(([k, e]: any) => `${k} ${e.value_per_share ? num(e.value_per_share) : "n/a"}`).join(" · ")}
                {" "}→ median <b className="num">{num(v.comparables.relative_valuation.median_value_per_share)}</b></div>
            )}
          </>
        ) : <Empty>No peers in the database for this sector.</Empty>}
      </Card>
      <Card title="Limitations"><ul className="text-sm list-disc pl-5 text-muted">{v.limitations.map((l: string) => <li key={l}>{l}</li>)}</ul></Card>
    </div>
  );
}

function TradingTab({ o, symbol }: { o: any; symbol: string }) {
  const bars = useBars(symbol, 365);
  const t = o.technicals;
  const levels = useMemo(() => [
    ...(t.support_resistance?.support || []).slice(0, 2).map((l: any) => ({ price: l.level, label: `S (${l.touches})`, tone: "up" as const })),
    ...(t.support_resistance?.resistance || []).slice(0, 2).map((l: any) => ({ price: l.level, label: `R (${l.touches})`, tone: "down" as const })),
  ], [t]);
  return (
    <div className="space-y-4">
      <Card title="Technical picture" subtitle={t.support_resistance_note} pad={false}>
        {bars.data?.bars?.length ? <div className="p-2"><PriceChart bars={bars.data.bars} levels={levels} height={320} /></div> : <Spinner />}
        <div className="grid grid-cols-3 md:grid-cols-6 gap-3 p-4 border-t border-line">
          <Stat label="RSI(14)" value={num(t.rsi14, 1)} /><Stat label="SMA50" value={num(t.sma50)} /><Stat label="SMA200" value={num(t.sma200)} />
          <Stat label="ATR(14)" value={num(t.atr14)} /><Stat label="MACD" value={num(t.macd, 3)} /><Stat label="Signal" value={num(t.macd_signal, 3)} />
        </div>
      </Card>
      {o.setups.length ? <div className="grid gap-4 lg:grid-cols-2">{o.setups.map((s: any, i: number) => <SetupCard key={i} s={{ ...s, is_sample: o.security.is_sample }} />)}</div>
        : <Card><Empty>No active short-term setup on {symbol}. No trade is generated to fill the page.</Empty></Card>}
    </div>
  );
}

function FilingsNews({ o }: { o: any }) {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Filings" pad={false}>
        {o.filings.length ? <Table dense head={["Filed", "Form", "Period", "Document"]} rows={o.filings.map((f: any) => [dt(f.filed_date), f.form, f.period || "—",
          f.url ? <a href={f.url} target="_blank" rel="noopener noreferrer" className="text-accent inline-flex items-center gap-1">{f.accession}<ExternalLink className="h-3 w-3" /></a> : <span className="text-muted">{f.accession}</span>])} />
          : <Empty>No filings from configured sources.</Empty>}
      </Card>
      <Card title="News" subtitle="Third-party content — treated as untrusted data">
        {o.news.length ? (
          <ul className="space-y-3 text-sm">
            {o.news.map((n: any) => (
              <li key={n.url}>
                <div className="flex items-start gap-2">
                  {(n.title.suspected_injection || n.summary.suspected_injection) && <span title={`Instruction-like text detected: ${[...n.title.matched_patterns, ...n.summary.matched_patterns].join(", ")}`}><AlertTriangle className="h-4 w-4 text-down shrink-0 mt-0.5" /></span>}
                  <div className="min-w-0">
                    <a href={n.url} target="_blank" rel="noopener noreferrer" className="hover:text-accent">{n.title.text}</a>
                    <div className="text-xs text-muted">{n.publisher} · {dt(n.published_at, true)} {n.sentiment !== null && `· sentiment ${num(n.sentiment, 2)}`} <QualityBadge q={n.quality} /></div>
                    {n.summary.text && <p className="text-xs text-muted mt-1">{n.summary.text}</p>}
                    {(n.title.suspected_injection || n.summary.suspected_injection) && <p className="text-xs text-down mt-1">Possible prompt injection detected; the agent treats this text strictly as data.</p>}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        ) : <Empty>No news configured (Alpha Vantage news requires ALPHAVANTAGE_API_KEY).</Empty>}
      </Card>
    </div>
  );
}

function ReportsTab({ symbol }: { symbol: string }) {
  const qc = useQueryClient();
  const reps = useQuery({ queryKey: ["reports", symbol], queryFn: () => api.get(`/api/research/reports?symbol=${symbol}`) });
  const preds = useQuery({ queryKey: ["predictions"], queryFn: () => api.get("/api/learning/predictions") });
  const [p, setP] = useState({ direction: "up", probability: "0.6", horizon_days: "90", kind: "direction", rationale: "" });
  const create = useMutation({ mutationFn: () => api.post("/api/learning/predictions", { symbol, ...p, probability: Number(p.probability), horizon_days: Number(p.horizon_days) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["predictions"] }) });
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Research reports" pad={false}>
        {reps.data?.length ? <Table dense head={["Date", "Conclusion", "Horizon", "By"]} rows={reps.data.map((r: any) => [
          <Link to={`/research/reports/${r.id}`} className="text-accent">{dt(r.created_at, true)}</Link>, <ConclusionBadge c={r.conclusion} />, r.horizon, r.created_by])} /> : <Empty>No reports yet.</Empty>}
      </Card>
      <Card title="Record a prediction" subtitle="Registered before the window starts; evaluated automatically when it ends.">
        <div className="grid grid-cols-2 gap-2">
          <Select label="Direction" value={p.direction} onChange={(v) => setP({ ...p, direction: v })} options={[{ value: "up", label: "Up" }, { value: "down", label: "Down" }]} />
          <Select label="Type" value={p.kind} onChange={(v) => setP({ ...p, kind: v })} options={[{ value: "direction", label: "Absolute direction" }, { value: "outperform", label: "Relative to benchmark" }]} />
          <Input label="Probability correct (0.5–0.99)" value={p.probability} onChange={(e) => setP({ ...p, probability: e.target.value })} />
          <Input label="Horizon (days)" value={p.horizon_days} onChange={(e) => setP({ ...p, horizon_days: e.target.value })} />
        </div>
        <Input label="Rationale" value={p.rationale} onChange={(e) => setP({ ...p, rationale: e.target.value })} className="mt-2" />
        <Button variant="primary" className="mt-2" onClick={() => create.mutate()} disabled={create.isPending}>Register prediction</Button>
        <ErrorBox error={create.error} />
        <div className="mt-3">
          <Table dense head={["Created", "Call", "p", "Evaluate", "Status"]} rows={(preds.data || []).filter((x: any) => x.symbol === symbol).slice(0, 8).map((x: any) => [
            dt(x.created_at), `${x.kind} ${x.direction}`, num(x.probability, 2), dt(x.evaluate_on), x.status === "evaluated" ? <Badge tone={x.outcome?.correct ? "up" : "down"}>{x.outcome?.correct ? "correct" : "wrong"}</Badge> : <Badge>open</Badge>])} />
        </div>
      </Card>
    </div>
  );
}
