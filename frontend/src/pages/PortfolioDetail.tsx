import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldAlert, ShieldCheck, Trash2, Upload } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { BarList, CorrelationHeatmap, TimeSeriesChart } from "../components/charts";
import { Badge, Button, Card, Empty, ErrorBox, Input, KV, Notice, QualityBadge, Select, Spinner, Stat, Table, Tabs } from "../components/ui";
import { dt, money, num, pct, signClass } from "../format";

export default function PortfolioDetail() {
  const { id } = useParams();
  const [tab, setTab] = useState("overview");
  const q = useQuery({ queryKey: ["portfolio", id], queryFn: () => api.get(`/api/portfolios/${id}`) });
  const nav = useNavigate();
  const qc = useQueryClient();
  const del = useMutation({ mutationFn: () => api.del(`/api/portfolios/${id}`), onSuccess: () => { qc.invalidateQueries(); nav("/portfolios"); } });
  if (q.isLoading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const { portfolio: p, state: s } = q.data;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2 flex-wrap"><h1 className="text-xl font-semibold">{p.name}</h1>
            <QualityBadge q={p.is_hypothetical ? "hypothetical" : "actual"} /><QualityBadge q={s.data_quality} /></div>
          <p className="text-sm text-muted">{p.kind} · {p.base_currency} · benchmark {p.benchmark?.symbol || "none"} · cost basis {p.cost_basis_method}</p>
        </div>
        <Button variant="danger" size="sm" onClick={() => confirm(`Delete portfolio "${p.name}" and all its transactions?`) && del.mutate()}>Delete</Button>
      </div>
      {s.has_sample_positions && <Notice tone="sample">This portfolio holds SAMPLE instruments; all values are synthetic and hypothetical.</Notice>}
      {s.warnings.length > 0 && <Notice>{s.warnings.join(" · ")}</Notice>}
      <div className="grid gap-3 grid-cols-2 md:grid-cols-4 xl:grid-cols-8">
        <Card><Stat label="Total value" value={money(s.total_value, undefined, 0)} sub={p.base_currency} /></Card>
        <Card><Stat label="Market value" value={money(s.market_value, undefined, 0)} /></Card>
        <Card><Stat label="Cash" value={money(s.cash, undefined, 0)} /></Card>
        <Card><Stat label="Contributions" value={money(s.net_contributions, undefined, 0)} /></Card>
        <Card><Stat label="Unrealized P&L" value={money(s.unrealized_pnl, undefined, 0)} tone={signClass(s.unrealized_pnl)} /></Card>
        <Card><Stat label="Realized P&L" value={money(s.realized_pnl, undefined, 0)} tone={signClass(s.realized_pnl)} /></Card>
        <Card><Stat label="Income (net)" value={money(s.income)} /></Card>
        <Card><Stat label="Fees" value={money(s.fees)} /></Card>
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[
        { key: "overview", label: "Performance" }, { key: "holdings", label: `Holdings (${s.positions.length})` }, { key: "txns", label: `Transactions (${s.n_transactions})` },
        { key: "risk", label: "Risk" }, { key: "rebalance", label: "Rebalance" }, { key: "stress", label: "Stress tests" }, { key: "sizing", label: "Sizing & pre-trade" },
      ]} />
      {tab === "overview" && <Performance id={id!} s={s} />}
      {tab === "holdings" && <Holdings s={s} />}
      {tab === "txns" && <Transactions id={id!} p={p} />}
      {tab === "risk" && <Risk id={id!} />}
      {tab === "rebalance" && <Rebalance id={id!} s={s} />}
      {tab === "stress" && <Stress id={id!} />}
      {tab === "sizing" && <Sizing id={id!} />}
    </div>
  );
}

function Performance({ id, s }: { id: string; s: any }) {
  const h = useQuery({ queryKey: ["history", id], queryFn: () => api.get(`/api/portfolios/${id}/history`) });
  if (h.isLoading) return <Spinner label="Computing performance…" />;
  if (h.error) return <ErrorBox error={h.error} />;
  const d = h.data;
  if (!d.dates?.length) return <Card><Empty>No transactions yet.</Empty></Card>;
  const m = d.metrics;
  const data = d.dates.map((date: string, i: number) => ({ date, pf: d.twr_index[i] - 1, bm: d.benchmark ? d.benchmark.values[i] - 1 : null, dd: d.drawdown[i] }));
  return (
    <div className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="Cumulative time-weighted return" className="xl:col-span-2" subtitle={d.is_hypothetical ? "Hypothetical performance" : "Actual account (as recorded)"}>
          <TimeSeriesChart data={data} refY={0} series={[{ key: "pf", label: "Portfolio (TWR)", slot: 0 }, ...(d.benchmark ? [{ key: "bm", label: `${d.benchmark.symbol}${d.benchmark.is_sample ? " (sample)" : ""}`, slot: 1 }] : [])]} />
        </Card>
        <Card title="Statistics">
          <KV items={[
            ["TWR since inception", pct(m.twr_total, 2)], ["TWR annualized", pct(m.twr_annualized, 2)], ["MWR (XIRR) annualized", pct(m.mwr_annualized, 2)],
            ["Volatility (ann.)", pct(m.volatility)], ["Sharpe (rf=0)", num(m.sharpe)], ["Sortino", num(m.sortino)], ["Max drawdown", pct(m.max_drawdown)],
            ["Current drawdown", pct(m.current_drawdown)], ["Beta vs benchmark", num(m.beta)], ["Tracking error", pct(m.tracking_error)],
            ["Excess return (total)", pct(m.excess_return_total, 2)], ["1-day VaR 95% / CVaR", `${pct(m.var_95_1d?.var)} / ${pct(m.var_95_1d?.cvar)}`],
            ["Days with missing prices", m.days_with_missing_prices],
          ]} />
        </Card>
      </div>
      <Card title="Period returns">
        <Table dense head={["", ...Object.keys(d.periods)]} rows={[
          ["Portfolio", ...Object.values(d.periods).map((v: any) => <span className={`num ${signClass(v)}`}>{pct(v, 2, true)}</span>)],
          ...(d.benchmark ? [[d.benchmark.symbol, ...Object.keys(d.periods).map((k) => <span className={`num ${signClass(d.benchmark.periods[k])}`}>{k === "Inception" ? pct(d.benchmark.total_return, 2, true) : pct(d.benchmark.periods[k], 2, true)}</span>)]] : []),
        ]} />
      </Card>
      <Card title="Drawdown"><TimeSeriesChart height={160} data={data} series={[{ key: "dd", label: "Drawdown", slot: 7 }]} /></Card>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {Object.entries(s.exposures).map(([k, v]: any) => (
          <Card key={k} title={`${k.replace("_", " ")} exposure`}><BarList data={Object.entries(v).map(([label, value]) => ({ label, value: value as number }))} /></Card>
        ))}
      </div>
      <p className="text-xs text-muted">{d.methodology}</p>
    </div>
  );
}

function Holdings({ s }: { s: any }) {
  return (
    <Card pad={false}>
      {s.positions.length ? (
        <Table dense head={["Symbol", "Qty", "Avg cost", "Price", "Market value", "Weight", "Unrealized", "%", "Realized", "Dividends", "1D", "Data"]}
          rows={s.positions.map((p: any) => [
            <Link to={`/research/${p.symbol}`} className="num font-semibold hover:text-accent">{p.symbol}</Link>, <span className="num">{num(p.quantity, 4)}</span>,
            <span className="num">{num(p.avg_cost_base)}</span>, <span className="num" title={`${p.price_local} ${p.currency} × fx ${num(p.fx_rate, 4)}`}>{num(p.price_base)}</span>,
            <span className="num">{money(p.market_value)}</span>, <span className="num">{pct(p.weight)}</span>,
            <span className={`num ${signClass(p.unrealized_pnl)}`}>{money(p.unrealized_pnl)}</span>, <span className={`num ${signClass(p.unrealized_pct)}`}>{pct(p.unrealized_pct)}</span>,
            <span className={`num ${signClass(p.realized_pnl)}`}>{money(p.realized_pnl)}</span>, <span className="num">{money(p.dividends)}</span>,
            <span className={`num ${signClass(p.day_change_pct)}`}>{pct(p.day_change_pct, 2, true)}</span>,
            <span title={`${p.price_source} ${dt(p.price_as_of, true)}`}><QualityBadge q={p.quality} fresh={p.freshness} /></span>,
          ])} />
      ) : <Empty>No open positions.</Empty>}
      {s.closed_trades?.length > 0 && (
        <details className="p-3 border-t border-line"><summary className="text-sm text-muted cursor-pointer">Closed lots ({s.closed_trades.length})</summary>
          <Table dense head={["Date", "Qty", "Proceeds", "Cost", "P&L"]} rows={s.closed_trades.map((c: any) => [c.date, num(Number(c.quantity), 4), money(Number(c.proceeds)), money(Number(c.cost)), <span className={signClass(Number(c.pnl))}>{money(Number(c.pnl))}</span>])} />
        </details>
      )}
    </Card>
  );
}

const TXN_TYPES = ["buy", "sell", "dividend", "interest", "fee", "deposit", "withdrawal", "split"];

function Transactions({ id, p }: { id: string; p: any }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["txns", id], queryFn: () => api.get(`/api/portfolios/${id}/transactions`) });
  const today = new Date().toISOString().slice(0, 10);
  const [f, setF] = useState<any>({ type: "buy", trade_date: today, symbol: "", quantity: "", price: "", amount: "", fees: "0", currency: "", fx_rate: "", split_ratio: "", notes: "" });
  const inval = () => { qc.invalidateQueries({ queryKey: ["txns", id] }); qc.invalidateQueries({ queryKey: ["portfolio", id] }); qc.invalidateQueries({ queryKey: ["history", id] }); };
  const add = useMutation({
    mutationFn: () => api.post(`/api/portfolios/${id}/transactions`, Object.fromEntries(Object.entries(f).filter(([, v]) => v !== "").map(([k, v]) => [k, k === "symbol" || k === "currency" ? String(v).toUpperCase() : v]))),
    onSuccess: () => { inval(); setF({ ...f, quantity: "", price: "", amount: "", notes: "" }); },
  });
  const del = useMutation({ mutationFn: (tid: number) => api.del(`/api/portfolios/${id}/transactions/${tid}`), onSuccess: inval });
  const imp = useMutation({ mutationFn: (file: File) => { const fd = new FormData(); fd.append("file", file); return api.upload(`/api/portfolios/${id}/import`, fd); }, onSuccess: inval });
  const needsSec = ["buy", "sell", "dividend", "split"].includes(f.type);
  const trade = ["buy", "sell"].includes(f.type);
  return (
    <div className="space-y-4">
      {p.kind !== "live" && (
        <Card title="Record transaction" subtitle="The ledger is replayed on every entry; inconsistent entries (e.g. selling more than held) are rejected.">
          <div className="grid gap-2 grid-cols-2 md:grid-cols-4 xl:grid-cols-8">
            <Select label="Type" value={f.type} onChange={(v) => setF({ ...f, type: v })} options={TXN_TYPES.map((t) => ({ value: t, label: t }))} />
            <Input label="Trade date" type="date" max={today} value={f.trade_date} onChange={(e) => setF({ ...f, trade_date: e.target.value })} />
            {needsSec && <Input label="Symbol" value={f.symbol} onChange={(e) => setF({ ...f, symbol: e.target.value })} />}
            {trade && <Input label="Quantity" value={f.quantity} onChange={(e) => setF({ ...f, quantity: e.target.value })} />}
            {trade && <Input label="Price" value={f.price} onChange={(e) => setF({ ...f, price: e.target.value })} />}
            {!trade && f.type !== "split" && <Input label="Amount" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} />}
            {f.type === "split" && <Input label="Split ratio (new/old)" value={f.split_ratio} onChange={(e) => setF({ ...f, split_ratio: e.target.value })} />}
            <Input label={f.type === "dividend" ? "Withholding tax/fees" : "Fees"} value={f.fees} onChange={(e) => setF({ ...f, fees: e.target.value })} />
            <Input label="Currency (opt.)" placeholder="auto" value={f.currency} onChange={(e) => setF({ ...f, currency: e.target.value })} />
            <Input label="FX to base (opt.)" placeholder="auto" value={f.fx_rate} onChange={(e) => setF({ ...f, fx_rate: e.target.value })} />
          </div>
          <div className="mt-2 flex flex-wrap gap-2 items-end">
            <div className="flex-1 min-w-[200px]"><Input label="Notes" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></div>
            <Button variant="primary" onClick={() => add.mutate()} disabled={add.isPending}>Add</Button>
            <label className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-panel2 px-3 py-1.5 text-sm cursor-pointer hover:bg-line/60">
              <Upload className="h-4 w-4" />Import CSV
              <input type="file" accept=".csv,text/csv" className="hidden" onChange={(e) => e.target.files?.[0] && imp.mutate(e.target.files[0])} />
            </label>
          </div>
          <p className="text-xs text-muted mt-1">CSV columns: type,trade_date,symbol,quantity,price,amount,fees,currency,fx_rate,notes. Imports are all-or-nothing.</p>
          <ErrorBox error={add.error || imp.error || del.error} />
          {imp.data && <Notice tone="info">Imported {imp.data.imported} transactions.</Notice>}
        </Card>
      )}
      <Card pad={false} title="History (audit-logged)">
        {q.data?.length ? (
          <Table dense head={["Date", "Type", "Symbol", "Qty", "Price", "Amount", "Fees", "Ccy", "FX", "Origin", "Notes", ""]} rows={q.data.map((t: any) => [
            t.trade_date, <Badge>{t.type}</Badge>, t.symbol || "—", num(t.quantity, 4), num(t.price), num(t.amount), num(t.fees), t.currency, num(t.fx_rate, 4),
            <Badge tone={t.origin === "agent_paper" ? "accent" : "neutral"}>{t.origin}</Badge>, <span className="text-xs text-muted truncate max-w-[180px] inline-block align-bottom">{t.notes}</span>,
            <button onClick={() => confirm("Delete this transaction? (kept in the audit log)") && del.mutate(t.id)} className="text-muted hover:text-down" aria-label="Delete"><Trash2 className="h-3.5 w-3.5" /></button>,
          ])} />
        ) : <Empty>No transactions.</Empty>}
      </Card>
    </div>
  );
}

function Risk({ id }: { id: string }) {
  const q = useQuery({ queryKey: ["risk", id], queryFn: () => api.get(`/api/portfolios/${id}/risk`) });
  if (q.isLoading) return <Spinner label="Running risk engine…" />;
  if (q.error) return <ErrorBox error={q.error} />;
  const r = q.data;
  const nonpass = r.checks.filter((c: any) => c.status !== "pass");
  return (
    <div className="space-y-4">
      <div className="grid gap-3 grid-cols-2 md:grid-cols-6">
        <Card><Stat label="Breaches" value={r.breaches.length} tone={r.breaches.length ? "text-down" : "text-up"} /></Card>
        <Card><Stat label="Warnings" value={r.warnings.length} tone={r.warnings.length ? "text-warn" : ""} /></Card>
        <Card><Stat label="Effective # positions" value={num(r.concentration.effective_n, 1)} sub={`HHI ${num(r.concentration.hhi, 3)}`} /></Card>
        <Card><Stat label="Top / top-5 weight" value={`${pct(r.concentration.top_weight, 0)} / ${pct(r.concentration.top5_weight, 0)}`} /></Card>
        <Card><Stat label="Ex-ante vol (1y cov)" value={pct(r.ex_ante_volatility)} /></Card>
        <Card><Stat label="Realized vol / max DD" value={`${pct(r.realized.volatility, 0)} / ${pct(r.realized.max_drawdown, 0)}`} /></Card>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Limit checks" subtitle={r.engine} actions={<Link to="/settings" className="text-xs text-accent">Edit limits →</Link>}>
          {nonpass.length ? (
            <ul className="space-y-1.5 text-sm">{nonpass.map((c: any, i: number) => (
              <li key={i} className="flex gap-2">{c.status === "breach" ? <ShieldAlert className="h-4 w-4 text-down shrink-0" /> : <ShieldAlert className="h-4 w-4 text-warn shrink-0" />}<span><Badge tone={c.status === "breach" ? "down" : "warn"}>{c.status}</Badge> {c.message}</span></li>
            ))}</ul>
          ) : <div className="flex items-center gap-2 text-sm text-up"><ShieldCheck className="h-4 w-4" />All {r.checks.length} checks pass.</div>}
          <details className="mt-3"><summary className="text-xs text-muted cursor-pointer">Active limits</summary>
            <KV items={Object.entries(r.limits).map(([k, v]: any) => [k.replace(/_/g, " "), typeof v === "number" && v < 1 && v > 0 ? pct(v, 0) : String(v)])} /></details>
        </Card>
        <Card title="Correlation (1y daily returns)"><CorrelationHeatmap symbols={r.correlation.symbols} matrix={r.correlation.matrix} /></Card>
      </div>
    </div>
  );
}

function Rebalance({ id, s }: { id: string; s: any }) {
  const [t, setT] = useState<Record<string, string>>(() => Object.fromEntries(s.positions.map((p: any) => [p.symbol, (p.weight * 100).toFixed(1)])));
  const [newSym, setNewSym] = useState("");
  const [cost, setCost] = useState("15");
  const sim = useMutation({ mutationFn: () => api.post(`/api/portfolios/${id}/rebalance-simulation`, { targets: Object.fromEntries(Object.entries(t).map(([k, v]) => [k, (Number(v) || 0) / 100])), cost_bps: Number(cost) || 0 }) });
  const total = Object.values(t).reduce((a, v) => a + (Number(v) || 0), 0);
  const equal = () => setT(Object.fromEntries(Object.keys(t).map((k) => [k, (95 / Object.keys(t).length).toFixed(1)])));
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Target weights (%)" subtitle={`Sum ${total.toFixed(1)}% · remainder stays in cash`} actions={<Button size="sm" onClick={equal}>Equal weight (95%)</Button>}>
        <div className="space-y-1.5">
          {Object.entries(t).map(([k, v]) => (
            <div key={k} className="grid grid-cols-[80px_1fr_auto] gap-2 items-center">
              <span className="num font-semibold">{k}</span><Input value={v} onChange={(e) => setT({ ...t, [k]: e.target.value })} />
              <button onClick={() => { const n = { ...t }; delete n[k]; setT(n); }} className="text-muted hover:text-down" aria-label="Remove"><Trash2 className="h-4 w-4" /></button>
            </div>
          ))}
          <div className="flex gap-2 pt-2"><Input placeholder="Add symbol" value={newSym} onChange={(e) => setNewSym(e.target.value.toUpperCase())} /><Button onClick={() => { if (newSym) { setT({ ...t, [newSym]: "0" }); setNewSym(""); } }}>Add</Button></div>
          <div className="flex gap-2 items-end pt-2"><div className="w-40"><Input label="Cost (bps/unit traded)" value={cost} onChange={(e) => setCost(e.target.value)} /></div>
            <Button variant="primary" onClick={() => sim.mutate()} disabled={total > 100}>Simulate</Button></div>
          {total > 100 && <p className="text-xs text-down">Weights exceed 100%.</p>}
        </div>
      </Card>
      <Card title="Simulation result">
        <ErrorBox error={sim.error} />
        {sim.data ? (
          <div className="space-y-3">
            <Table dense head={["Symbol", "Current", "Target", "Trade value", "≈ Qty", "Side"]} rows={sim.data.trades.map((r: any) => [r.symbol, pct(r.current_weight), pct(r.target_weight),
              <span className={`num ${signClass(r.trade_value)}`}>{money(r.trade_value)}</span>, num(r.approx_quantity, 2), <Badge tone={r.side === "buy" ? "up" : r.side === "sell" ? "down" : "neutral"}>{r.side}</Badge>])} />
            <KV items={[["Turnover", `${money(sim.data.turnover_value)} (${pct(sim.data.turnover_pct)})`], ["Estimated costs", money(sim.data.estimated_costs)], ["Post-trade cash weight", pct(sim.data.post_trade_cash_weight)]]} />
            {sim.data.limit_violations.map((v: string) => <p key={v} className="text-sm text-down">⚠ {v}</p>)}
            <p className="text-xs text-muted">{sim.data.note}</p>
          </div>
        ) : <Empty>Adjust weights and simulate. Nothing is traded.</Empty>}
      </Card>
    </div>
  );
}

function Stress({ id }: { id: string }) {
  const sc = useQuery({ queryKey: ["scenarios"], queryFn: () => api.get("/api/stress-scenarios") });
  const [k, setK] = useState("equity_crash");
  const r = useQuery({ queryKey: ["stress", id, k], queryFn: () => api.post(`/api/portfolios/${id}/stress`, { scenario: k }) });
  return (
    <Card title="Scenario analysis" actions={<Select className="w-72" value={k} onChange={setK} options={Object.entries(sc.data || {}).map(([value, label]: any) => ({ value, label }))} />}>
      {r.isLoading ? <Spinner /> : r.data && (
        <div className="space-y-3">
          <div className="grid grid-cols-3 gap-3">
            <Stat label="Invested before" value={money(r.data.invested_before)} /><Stat label="After shock" value={money(r.data.invested_after)} />
            <Stat label="Scenario P&L" value={`${money(r.data.pnl)} (${pct(r.data.pct)})`} tone={signClass(r.data.pnl)} />
          </div>
          <Table dense head={["Symbol", "Market value", "Shock", "P&L"]} rows={r.data.positions.map((p: any) => [p.symbol, money(p.market_value), pct(p.shock, 0), <span className={signClass(p.pnl)}>{money(p.pnl)}</span>])} />
          <p className="text-xs text-muted">{r.data.note}</p>
        </div>
      )}
    </Card>
  );
}

function Sizing({ id }: { id: string }) {
  const [f, setF] = useState({ entry: "", stop: "", risk_per_trade_pct: "0.01" });
  const [t, setT] = useState({ symbol: "", side: "buy", quantity: "" });
  const size = useMutation({ mutationFn: () => api.post(`/api/portfolios/${id}/position-size`, { entry: Number(f.entry), stop: Number(f.stop), risk_per_trade_pct: Number(f.risk_per_trade_pct) }) });
  const chk = useMutation({ mutationFn: () => api.post(`/api/portfolios/${id}/pretrade-check`, { symbol: t.symbol.toUpperCase(), side: t.side, quantity: Number(t.quantity) }) });
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Position sizing (fixed-fractional)">
        <div className="grid grid-cols-3 gap-2">
          <Input label="Entry" value={f.entry} onChange={(e) => setF({ ...f, entry: e.target.value })} />
          <Input label="Stop" value={f.stop} onChange={(e) => setF({ ...f, stop: e.target.value })} />
          <Input label="Risk per trade" value={f.risk_per_trade_pct} onChange={(e) => setF({ ...f, risk_per_trade_pct: e.target.value })} />
        </div>
        <Button className="mt-2" variant="primary" onClick={() => size.mutate()}>Calculate</Button>
        <ErrorBox error={size.error} />
        {size.data && <div className="mt-3"><KV items={[["Shares", num(size.data.shares, 2)], ["Notional", money(size.data.notional)], ["Risk amount", money(size.data.risk_amount)], ["Binding constraint", size.data.binding_constraint]]} />
          <p className="text-xs text-muted mt-2 num">{size.data.formula}</p></div>}
      </Card>
      <Card title="Independent pre-trade risk check" subtitle="Runs the same deterministic engine the agent's proposals must pass. Does not place an order.">
        <div className="grid grid-cols-3 gap-2">
          <Input label="Symbol" value={t.symbol} onChange={(e) => setT({ ...t, symbol: e.target.value })} />
          <Select label="Side" value={t.side} onChange={(v) => setT({ ...t, side: v })} options={[{ value: "buy", label: "Buy" }, { value: "sell", label: "Sell" }]} />
          <Input label="Quantity" value={t.quantity} onChange={(e) => setT({ ...t, quantity: e.target.value })} />
        </div>
        <Button className="mt-2" variant="primary" onClick={() => chk.mutate()}>Check</Button>
        <ErrorBox error={chk.error} />
        {chk.data && (
          <div className="mt-3 space-y-1 text-sm">
            <div className="flex items-center gap-2">{chk.data.approved ? <ShieldCheck className="h-4 w-4 text-up" /> : <ShieldAlert className="h-4 w-4 text-down" />}<b>{chk.data.summary}</b></div>
            <ul className="text-xs space-y-0.5">{chk.data.checks.map((c: any, i: number) => <li key={i}><Badge tone={c.status === "pass" ? "up" : c.status === "breach" ? "down" : "warn"}>{c.status}</Badge> {c.message}</li>)}</ul>
          </div>
        )}
      </Card>
    </div>
  );
}
