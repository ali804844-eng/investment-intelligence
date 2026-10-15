import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Clock, Database } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { BarList, TimeSeriesChart } from "../components/charts";
import { Badge, Card, Empty, ErrorBox, Notice, QualityBadge, Spinner, Stat, Table } from "../components/ui";
import { ago, dt, money, num, pct, signClass } from "../format";

export default function Dashboard() {
  const q = useQuery({ queryKey: ["dashboard"], queryFn: () => api.get("/api/dashboard"), refetchInterval: 120_000 });
  if (q.isLoading) return <Spinner label="Loading dashboard…" />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data;
  const pf = d.portfolios.find((p: any) => !p.error && p.n_positions) || d.portfolios[0];
  const ccy = d.user.base_currency;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">Executive dashboard</h1>
          <p className="text-sm text-muted">Portfolio, markets and research activity at a glance.</p>
        </div>
        <AgentPill a={d.agent} />
      </div>
      {d.sample_mode && (
        <Notice tone="sample">
          <b>Sample mode.</b> No real market data provider is configured yet, so the platform runs on the synthetic SAMPLE universe
          (fictional companies on a fictional exchange). Every SAMPLE figure is labelled. Configure providers in <Link className="underline" to="/settings">Settings → Data sources</Link>.
        </Notice>
      )}

      <div className="grid gap-3 grid-cols-2 md:grid-cols-3 xl:grid-cols-6">
        <Card><Stat label="Total value" value={money(d.combined.total_value)} sub={`${ccy} · ${d.portfolios.length} portfolio(s)`} /></Card>
        <Card><Stat label="Invested capital" value={money(d.combined.net_contributions)} sub={`${ccy} · net deposits`} /></Card>
        <Card><Stat label="Cash" value={money(d.combined.cash)} sub="Paper/manual cash (no broker link)" /></Card>
        <Card><Stat label="Unrealized P&L" value={money(d.combined.unrealized_pnl)} tone={signClass(d.combined.unrealized_pnl)} /></Card>
        <Card><Stat label="Realized P&L" value={money(d.combined.realized_pnl)} tone={signClass(d.combined.realized_pnl)} /></Card>
        <Card><Stat label="Market regime" value={<span className="text-sm whitespace-normal">{d.regime.label}</span>} sub={<span className="inline-flex gap-1 items-center">{d.regime.benchmark?.symbol} <QualityBadge q={d.regime.data_quality} /></span>} /></Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2" title={pf ? `${pf.name} vs ${pf.benchmark?.symbol || "no benchmark"}` : "Performance"}
          subtitle={pf && <span className="inline-flex gap-1.5 items-center"><QualityBadge q={pf.is_hypothetical ? "hypothetical" : "actual"} /><QualityBadge q={pf.data_quality} /> Time-weighted, last 12 months</span>}
          actions={pf && <Link to={`/portfolios/${pf.id}`} className="text-xs text-accent">Open →</Link>}>
          {pf && pf.spark?.dates?.length ? (
            <>
              <TimeSeriesChart height={240} refY={0}
                data={pf.spark.dates.map((dte: string, i: number) => ({
                  date: dte,
                  pf: pf.spark.twr_index[i] / pf.spark.twr_index[0] - 1,
                  bm: pf.spark.benchmark ? pf.spark.benchmark[i] / pf.spark.benchmark[0] - 1 : null,
                }))}
                series={[{ key: "pf", label: pf.name, slot: 0 }, ...(pf.spark.benchmark ? [{ key: "bm", label: pf.benchmark.symbol, slot: 1 }] : [])]} />
              <div className="mt-3 overflow-x-auto">
                <Table dense head={["Period", ...Object.keys(pf.periods)]} rows={[
                  ["Portfolio (TWR)", ...Object.values(pf.periods).map((v: any) => <span className={signClass(v)}>{pct(v, 1, true)}</span>)],
                  ...(pf.benchmark ? [["Benchmark", ...Object.keys(pf.periods).map((k) => { const bv = k === "Inception" ? pf.benchmark.total_return : pf.benchmark.periods[k]; return <span className={signClass(bv)}>{pct(bv, 1, true)}</span>; })]] : []),
                ]} />
              </div>
            </>
          ) : <Empty>No portfolio history yet. <Link to="/portfolios" className="text-accent">Create a portfolio</Link>.</Empty>}
        </Card>
        <Card title="Risk snapshot" subtitle={pf?.name}>
          {pf ? (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <Stat label="Volatility (ann.)" value={pct(pf.metrics.volatility)} />
                <Stat label="Max drawdown" value={pct(pf.metrics.max_drawdown)} tone="text-down" />
                <Stat label="Current drawdown" value={pct(pf.metrics.current_drawdown)} />
                <Stat label="Sharpe" value={num(pf.metrics.sharpe)} hint="Risk-free rate assumed 0" />
                <Stat label="Beta" value={num(pf.metrics.beta)} />
                <Stat label="1-day VaR 95%" value={pct(pf.metrics.var_95_1d?.var)} hint="Historical simulation" />
              </div>
              <div>
                <div className="text-xs text-muted mb-1.5">Sector exposure</div>
                <BarList data={Object.entries(pf.exposures.sector).slice(0, 6).map(([label, value]) => ({ label, value: value as number }))} />
              </div>
            </div>
          ) : <Empty>No portfolio.</Empty>}
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
        <Card title="Research queue" subtitle={d.research_run ? `From research cycle ${ago(d.research_run.created_at)}` : "No research cycle has run yet"}
          actions={<Link to="/discover" className="text-xs text-accent">Discovery →</Link>}>
          {d.research_queue.length ? (
            <ol className="space-y-1.5 text-sm">
              {d.research_queue.slice(0, 8).map((r: any, i: number) => (
                <li key={i} className="flex items-center gap-2">
                  <span className="num w-5 text-muted">{i + 1}</span>
                  <Link to={`/research/${r.symbol}`} className="num font-semibold hover:text-accent w-16">{r.symbol}</Link>
                  <Badge>{r.strategy}</Badge>
                  {r.report && <Link to={`/research/reports/${r.report.report_id}`}><ConclusionBadge c={r.report.conclusion} /></Link>}
                  <span className="ml-auto num text-xs text-muted">{num(r.score, 2)}</span>
                </li>
              ))}
            </ol>
          ) : <Empty>Run a research cycle from <Link className="text-accent" to="/control">Agent Control</Link> or wait for the scheduled run.</Empty>}
        </Card>
        <Card title="Watchlist" actions={<Link to="/markets" className="text-xs text-accent">Markets →</Link>} pad={false}>
          {d.watchlist.length ? (
            <Table dense head={["Symbol", "Price", "1D", "Data"]} rows={d.watchlist.slice(0, 10).map((w: any) => [
              <Link to={`/research/${w.symbol}`} className="num font-semibold hover:text-accent">{w.symbol}</Link>,
              <span className="num">{num(w.price)}</span>,
              <span className={`num ${signClass(w.change_pct)}`}>{pct(w.change_pct, 2, true)}</span>,
              <QualityBadge q={w.quality} fresh={w.freshness} />,
            ])} />
          ) : <Empty>Watchlist is empty.</Empty>}
        </Card>
        <Card title="Macro & regime" subtitle={d.regime.explanation?.[0]}>
          <div className="space-y-1.5 text-sm">
            {d.regime.indicators?.map((i: any) => (
              <div key={i.name} className="flex justify-between gap-2">
                <span className="text-muted truncate">{i.name}</span>
                <span className="num">{i.fmt === "pct" ? pct(i.value, 2) : num(i.value, 1)} {i.quality === "sample" && <QualityBadge q="sample" />}</span>
              </div>
            ))}
            <p className="text-xs text-muted pt-1">{d.regime.caveat}</p>
          </div>
        </Card>
        <Card title="Notifications" actions={<Link to="/alerts" className="text-xs text-accent">All →</Link>}>
          {d.notifications.length ? (
            <ul className="space-y-2 text-sm">
              {d.notifications.map((n: any) => (
                <li key={n.id} className="flex gap-2">
                  <SevDot s={n.severity} />
                  <div className="min-w-0"><div className={n.read ? "text-muted truncate" : "truncate"}>{n.title}</div><div className="text-xs text-muted">{ago(n.created_at)} · {n.category}</div></div>
                </li>
              ))}
            </ul>
          ) : <Empty>No notifications.</Empty>}
          {!!d.pending_actions && <Notice tone="info"><Link to="/control" className="underline">{d.pending_actions} agent proposal(s)</Link> awaiting your decision.</Notice>}
        </Card>
        <Card title="Upcoming events" subtitle="Watched and held securities, next 45 days">
          {d.events.length ? (
            <Table dense head={["Date", "Symbol", "Event", ""]} rows={d.events.map((e: any) => [dt(e.date), <Link to={`/research/${e.symbol}`} className="num font-semibold">{e.symbol}</Link>, e.kind, <QualityBadge q={e.quality} />])} />
          ) : <Empty>No scheduled events from configured sources.</Empty>}
        </Card>
        <Card title="Data sources & freshness" actions={<Link to="/settings" className="text-xs text-accent">Configure →</Link>}>
          <ul className="space-y-1.5 text-sm">
            {d.providers.map((p: any) => (
              <li key={p.name} className="flex items-center gap-2">
                <Database className="h-3.5 w-3.5 text-muted" />
                <span className="truncate flex-1" title={p.title}>{p.title}</span>
                <ProvStatus s={p.status} />
              </li>
            ))}
          </ul>
        </Card>
      </div>
      {pf?.stale_positions?.length > 0 && <Notice>Stale prices in {pf.name}: {pf.stale_positions.join(", ")}. Valuations using them may be wrong.</Notice>}
    </div>
  );
}

export function ConclusionBadge({ c }: { c: string }) {
  const m: Record<string, [string, "up" | "down" | "warn" | "neutral" | "accent"]> = {
    consider: ["Consider", "up"], monitor: ["Monitor", "accent"], reject: ["Reject", "down"], insufficient_evidence: ["Insufficient evidence", "warn"],
  };
  const [l, t] = m[c] || [c, "neutral"];
  return <Badge tone={t}>{l}</Badge>;
}

function SevDot({ s }: { s: string }) {
  if (s === "critical") return <AlertTriangle className="h-4 w-4 text-down shrink-0 mt-0.5" aria-label="critical" />;
  if (s === "warning") return <AlertTriangle className="h-4 w-4 text-warn shrink-0 mt-0.5" aria-label="warning" />;
  return <CheckCircle2 className="h-4 w-4 text-accent shrink-0 mt-0.5" aria-label="info" />;
}

export function ProvStatus({ s }: { s: string }) {
  const m: Record<string, ["up" | "down" | "warn" | "neutral", string]> = {
    ok: ["up", "OK"], degraded: ["down", "Degraded"], unconfigured: ["neutral", "Not configured"], unknown: ["warn", "Unverified"],
  };
  const [t, l] = m[s] || ["neutral", s];
  return <Badge tone={t}>{l}</Badge>;
}

function AgentPill({ a }: { a: any }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <Badge tone={a.worker_alive ? "up" : "down"} title="Background worker heartbeat">{a.worker_alive ? "Agent worker running" : "Agent worker offline"}</Badge>
      <Badge tone={a.state === "running" ? "accent" : "warn"}>Agent {a.state}</Badge>
      <span className="text-muted inline-flex items-center gap-1"><Clock className="h-3 w-3" />Last success: {a.last_success ? `${a.last_success.job_type} ${ago(a.last_success.at)}` : "none"}</span>
      {a.next_run && <span className="text-muted">Next: {a.next_run.name} {ago(a.next_run.at)}</span>}
    </div>
  );
}
