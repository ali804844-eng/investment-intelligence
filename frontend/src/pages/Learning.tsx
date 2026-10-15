import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { CalibrationChart, TimeSeriesChart } from "../components/charts";
import { Badge, Button, Card, Empty, ErrorBox, Input, KV, Notice, QualityBadge, Select, Spinner, Stat, Table, Tabs } from "../components/ui";
import { ago, dt, num, pct, signClass } from "../format";

export default function Learning() {
  const [tab, setTab] = useState("predictions");
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Learning lab</h1>
        <p className="text-sm text-muted">Prediction registry, outcome evaluation, backtesting with bias controls, and human-approved strategy improvement. The system never rewrites code, limits or permissions.</p>
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[{ key: "predictions", label: "Predictions & calibration" }, { key: "backtests", label: "Backtests" }, { key: "strategies", label: "Strategy versions & experiments" }]} />
      {tab === "predictions" && <Predictions />}
      {tab === "backtests" && <Backtests />}
      {tab === "strategies" && <Strategies />}
    </div>
  );
}

function Predictions() {
  const qc = useQueryClient();
  const sc = useQuery({ queryKey: ["scorecard"], queryFn: () => api.get("/api/learning/scorecard") });
  const list = useQuery({ queryKey: ["predictions"], queryFn: () => api.get("/api/learning/predictions") });
  const ev = useMutation({ mutationFn: () => api.post("/api/learning/predictions/evaluate"), onSuccess: () => qc.invalidateQueries() });
  if (sc.isLoading) return <Spinner />;
  const o = sc.data.overall;
  return (
    <div className="space-y-4">
      {sc.data.includes_sample_data && <Notice tone="sample">Some evaluated predictions concern SAMPLE instruments; their accuracy says nothing about real markets.</Notice>}
      <div className="grid gap-3 grid-cols-2 md:grid-cols-5">
        <Card><Stat label="Evaluated" value={o.n || 0} sub={`${sc.data.n_open} open`} /></Card>
        <Card><Stat label="Hit rate" value={pct(o.hit_rate, 0)} sub={o.hit_rate_ci95 ? `95% CI ${pct(o.hit_rate_ci95[0], 0)}–${pct(o.hit_rate_ci95[1], 0)}` : "—"} /></Card>
        <Card><Stat label="Brier score" value={num(o.brier, 3)} sub="0.25 = coin flip; lower is better" /></Card>
        <Card><Stat label="Avg stated p" value={pct(o.avg_stated_probability, 0)} /></Card>
        <Card><Stat label="Avg excess measure" value={pct(o.avg_excess_measure, 1, true)} tone={signClass(o.avg_excess_measure)} /></Card>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Calibration" subtitle="Stated probability vs realized hit rate per bucket (dot size = n). Well-calibrated forecasts sit on the diagonal.">
          <CalibrationChart rows={o.calibration || []} />
        </Card>
        <Card title="By source & model version">
          <Table dense head={["Group", "n", "Hit rate", "Brier"]} rows={[
            ...Object.entries<any>(sc.data.by_source).map(([k, v]) => [`source: ${k}`, v.n, pct(v.hit_rate, 0), num(v.brier, 3)]),
            ...Object.entries<any>(sc.data.by_model_version).map(([k, v]) => [`model: ${k}`, v.n, pct(v.hit_rate, 0), num(v.brier, 3)]),
          ]} />
          <p className="text-xs text-muted mt-2">{sc.data.methodology}</p>
          <Button className="mt-2" onClick={() => ev.mutate()} disabled={ev.isPending}>Evaluate due predictions now</Button>
          {ev.data && <p className="text-xs text-muted mt-1">{ev.data.evaluated.length} evaluated, {ev.data.skipped.length} skipped.</p>}
        </Card>
      </div>
      <Card title="Prediction registry" pad={false}>
        {list.data?.length ? <Table dense head={["Created", "Symbol", "Call", "p", "Horizon", "Evaluate on", "Baseline", "Source", "Status", "Outcome"]} rows={list.data.map((p: any) => [
          dt(p.created_at), <Link to={`/research/${p.symbol}`} className="num font-semibold">{p.symbol}</Link>, `${p.kind} ${p.direction}${p.benchmark ? ` vs ${p.benchmark}` : ""}`,
          num(p.probability, 2), `${p.horizon_days}d`, dt(p.evaluate_on), num(p.baseline_price), <Badge>{p.source}</Badge>,
          p.status === "evaluated" ? <Badge tone={p.outcome.correct ? "up" : "down"}>{p.outcome.correct ? "correct" : "incorrect"}</Badge> : <Badge>{p.status}</Badge>,
          p.outcome ? <span className={`num ${signClass(p.outcome.measure)}`}>{pct(p.outcome.measure, 1, true)}</span> : "—",
        ])} /> : <Empty>No predictions yet. Reports with a material conclusion register one automatically; you can add your own from any security page.</Empty>}
      </Card>
    </div>
  );
}

function Backtests() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["backtests"], queryFn: () => api.get("/api/learning/backtests"), refetchInterval: 10_000 });
  const today = new Date();
  const [f, setF] = useState({ strategy_key: "quality", method: "single", start: `${today.getFullYear() - 8}-01-01`, end: today.toISOString().slice(0, 10), commission_bps: "1", spread_bps: "10", slippage_bps: "5" });
  const [sel, setSel] = useState<number | null>(null);
  const run = useMutation({ mutationFn: () => api.post("/api/learning/backtests", { ...f, commission_bps: Number(f.commission_bps), spread_bps: Number(f.spread_bps), slippage_bps: Number(f.slippage_bps) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["backtests"] }) });
  return (
    <div className="space-y-4">
      <Card title="Run a backtest" subtitle="Runs in the background worker. Point-in-time fundamentals (filing dates), next-day execution, explicit costs, benchmark-relative stats.">
        <div className="grid gap-2 grid-cols-2 md:grid-cols-4 xl:grid-cols-8 items-end">
          <Select label="Strategy" value={f.strategy_key} onChange={(v) => setF({ ...f, strategy_key: v })} options={["quality", "value", "growth", "momentum", "income", "defensive"].map((k) => ({ value: k, label: k }))} />
          <Select label="Method" value={f.method} onChange={(v) => setF({ ...f, method: v })} options={[{ value: "single", label: "Single period + sensitivity" }, { value: "walk_forward", label: "Walk-forward (OOS)" }]} />
          <Input label="Start" type="date" value={f.start} onChange={(e) => setF({ ...f, start: e.target.value })} />
          <Input label="End" type="date" value={f.end} onChange={(e) => setF({ ...f, end: e.target.value })} />
          <Input label="Commission bps" value={f.commission_bps} onChange={(e) => setF({ ...f, commission_bps: e.target.value })} />
          <Input label="Spread bps" value={f.spread_bps} onChange={(e) => setF({ ...f, spread_bps: e.target.value })} />
          <Input label="Slippage bps" value={f.slippage_bps} onChange={(e) => setF({ ...f, slippage_bps: e.target.value })} />
          <Button variant="primary" onClick={() => run.mutate()} disabled={run.isPending}>Queue</Button>
        </div>
        <ErrorBox error={run.error} />
        {run.data && <p className="text-xs text-muted mt-2">Queued job #{run.data.job_run_id}. It appears below when the worker finishes (see Agent Control for progress).</p>}
      </Card>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,420px)_1fr]">
        <Card title="History" pad={false}>
          {list.data?.length ? <Table dense head={["#", "Strategy", "Method", "CAGR", "Excess", "Sharpe", "Data"]} rows={list.data.map((b: any) => [
            <button className="text-accent" onClick={() => setSel(b.id)}>{b.id}</button>, `${b.strategy_key} v${b.config.version}`, b.config.method,
            pct(b.metrics.cagr), <span className={signClass(b.metrics.excess_cagr)}>{pct(b.metrics.excess_cagr, 1, true)}</span>, num(b.metrics.sharpe), <QualityBadge q={b.data_quality} />,
          ])} /> : <Empty>No backtests yet.</Empty>}
        </Card>
        {sel ? <BacktestDetail id={sel} /> : <Card><Empty>Select a backtest.</Empty></Card>}
      </div>
    </div>
  );
}

function BacktestDetail({ id }: { id: number }) {
  const q = useQuery({ queryKey: ["backtest", id], queryFn: () => api.get(`/api/learning/backtests/${id}`) });
  if (q.isLoading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const b = q.data;
  const m = b.metrics;
  const eq = b.equity_curve.equity || [];
  const bm = Object.fromEntries((b.equity_curve.benchmark || []).map(([d, v]: [string, number]) => [d, v]));
  const data = eq.map(([d, v]: [string, number]) => ({ date: d, s: v - 1, b: bm[d] !== undefined ? bm[d] - 1 : null }));
  return (
    <div className="space-y-4 min-w-0">
      <Card title={`Backtest #${b.id}: ${b.strategy_key} v${b.config.version} (${b.config.method})`} subtitle={`${b.config.start} → ${b.config.end} · costs ${JSON.stringify(b.config.costs)} · execution ${b.config.execution}`}>
        <div className="space-y-2 mb-3">{b.warnings.map((w: string) => <Notice key={w} tone={w.includes("SAMPLE") ? "sample" : "warn"}>{w}</Notice>)}</div>
        <TimeSeriesChart data={data} refY={0} series={[{ key: "s", label: "Strategy (net of costs)", slot: 0 }, { key: "b", label: "Benchmark", slot: 1 }]} />
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Metrics">
          <KV items={[["CAGR / benchmark", `${pct(m.cagr)} / ${pct(m.benchmark_cagr)}`], ["Excess CAGR", pct(m.excess_cagr, 2, true)], ["Volatility", pct(m.volatility)],
            ["Sharpe / benchmark", `${num(m.sharpe)} / ${num(m.benchmark_sharpe)}`], ["Sortino", num(m.sortino)], ["Max drawdown / benchmark", `${pct(m.max_drawdown)} / ${pct(m.benchmark_max_drawdown)}`],
            ["Information ratio", num(m.information_ratio)], ["Beta", num(m.beta)], ["Monthly hit rate vs bench", pct(m.monthly_hit_rate_vs_benchmark, 0)],
            ["Annual turnover", pct(m.annual_turnover, 0)], ["Total costs", pct(m.total_costs, 2)], ["Avg holdings", num(m.avg_holdings, 1)],
            ...(m.n_folds ? [["Folds beating benchmark", `${m.folds_beating_benchmark}/${m.n_folds}`]] as [string, string][] : [])]} />
        </Card>
        {m.sensitivity ? (
          <Card title="Robustness / sensitivity">
            <Table dense head={["Scenario", "CAGR", "Sharpe", "Max DD", "Excess"]} rows={m.sensitivity.map((r: any) => [r.scenario, pct(r.cagr), num(r.sharpe), pct(r.max_drawdown), <span className={signClass(r.excess_cagr)}>{pct(r.excess_cagr, 1, true)}</span>])} />
            <p className="text-xs text-muted mt-2">An edge that disappears with 2× costs or different breadth is fragile.</p>
          </Card>
        ) : b.folds?.length ? (
          <Card title="Walk-forward folds (out-of-sample)">
            <Table dense head={["Train", "Test", "Chosen", "Test return", "Excess"]} rows={b.folds.map((f: any) => [`${f.train[0].slice(0, 7)}→${f.train[1].slice(0, 7)}`, `${f.test[0].slice(0, 7)}→${f.test[1].slice(0, 7)}`,
              JSON.stringify(f.chosen_overrides), pct(f.test_metrics.total_return), <span className={signClass(f.test_metrics.excess_cagr)}>{pct(f.test_metrics.excess_cagr, 1, true)}</span>])} />
          </Card>
        ) : null}
      </div>
    </div>
  );
}

function Strategies() {
  const qc = useQueryClient();
  const vers = useQuery({ queryKey: ["versions"], queryFn: () => api.get("/api/learning/strategies") });
  const exps = useQuery({ queryKey: ["experiments"], queryFn: () => api.get("/api/learning/experiments"), refetchInterval: 15_000 });
  const [p, setP] = useState({ strategy_key: "quality", top_n: "", criteria: "", notes: "" });
  const today = new Date();
  const propose = useMutation({
    mutationFn: () => {
      const changes: any = {};
      if (p.top_n) changes.top_n = Number(p.top_n);
      if (p.criteria) changes.criteria_values = Object.fromEntries(p.criteria.split(",").map((kv) => kv.split("=").map((x) => x.trim())).filter((x) => x.length === 2).map(([k, v]) => [k, Number(v)]));
      return api.post("/api/learning/strategies/propose", { strategy_key: p.strategy_key, changes, notes: p.notes });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["versions"] }),
  });
  const test = useMutation({ mutationFn: (vid: number) => api.post("/api/learning/experiments", { candidate_version_id: vid, start: `${today.getFullYear() - 8}-01-01`, end: today.toISOString().slice(0, 10) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["experiments"] }) });
  const act = useMutation({ mutationFn: ({ id, a }: { id: number; a: string }) => api.post(`/api/learning/experiments/${id}/${a}`), onSuccess: () => qc.invalidateQueries() });
  const rb = useMutation({ mutationFn: (vid: number) => api.post(`/api/learning/strategies/${vid}/rollback`), onSuccess: () => qc.invalidateQueries() });
  return (
    <div className="space-y-4">
      <Notice tone="info">Workflow: propose a candidate version → run an experiment (walk-forward, same windows as production, acceptance criteria fixed before testing) → <b>you</b> approve promotion. Retired versions can be restored (rollback). All steps are audited.</Notice>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Propose a candidate version">
          <div className="grid grid-cols-2 gap-2">
            <Select label="Strategy" value={p.strategy_key} onChange={(v) => setP({ ...p, strategy_key: v })} options={["quality", "value", "growth", "momentum", "income", "defensive"].map((k) => ({ value: k, label: k }))} />
            <Input label="Top N holdings" value={p.top_n} onChange={(e) => setP({ ...p, top_n: e.target.value })} />
          </div>
          <Input className="mt-2" label="Threshold changes (feature=value, …)" placeholder="roic_avg=0.15, net_debt_to_ebitda=2" value={p.criteria} onChange={(e) => setP({ ...p, criteria: e.target.value })} />
          <Input className="mt-2" label="Hypothesis / notes" value={p.notes} onChange={(e) => setP({ ...p, notes: e.target.value })} />
          <Button className="mt-2" variant="primary" onClick={() => propose.mutate()} disabled={propose.isPending}>Propose</Button>
          <ErrorBox error={propose.error || test.error || act.error || rb.error} />
        </Card>
        <Card title="Experiments" pad={false}>
          {exps.data?.length ? (
            <div className="divide-y divide-line">{exps.data.map((e: any) => (
              <div key={e.id} className="p-3 text-sm space-y-1">
                <div className="flex flex-wrap items-center gap-2"><b>#{e.id} {e.strategy_key}</b> v{e.baseline.version} → v{e.candidate.version}
                  <Badge tone={e.status === "passed" || e.status === "promoted" ? "up" : e.status === "failed" || e.status === "rejected" ? "down" : "warn"}>{e.status}</Badge>
                  <span className="text-xs text-muted">{ago(e.created_at)}</span>{e.results?.data_quality && <QualityBadge q={e.results.data_quality} />}</div>
                {e.results?.verdict && <ul className="text-xs">{e.results.verdict.checks.map((c: any) => <li key={c.criterion} className={c.passed ? "text-up" : "text-down"}>{c.passed ? "✓" : "✗"} {c.criterion}: {c.detail}</li>)}</ul>}
                {e.results?.baseline && <div className="text-xs text-muted num">OOS Sharpe {num(e.results.baseline.oos_metrics.sharpe)} → {num(e.results.candidate.oos_metrics.sharpe)} · CAGR {pct(e.results.baseline.oos_metrics.cagr)} → {pct(e.results.candidate.oos_metrics.cagr)}</div>}
                {e.status === "passed" && <div className="flex gap-2"><Button size="sm" variant="primary" onClick={() => confirm(`Promote ${e.strategy_key} v${e.candidate.version} to production?`) && act.mutate({ id: e.id, a: "promote" })}>Approve & promote</Button>
                  <Button size="sm" variant="danger" onClick={() => act.mutate({ id: e.id, a: "reject" })}>Reject</Button></div>}
                {e.status === "failed" && <Button size="sm" variant="ghost" onClick={() => act.mutate({ id: e.id, a: "reject" })}>Archive as rejected</Button>}
              </div>
            ))}</div>
          ) : <Empty>No experiments yet.</Empty>}
        </Card>
      </div>
      <Card title="Versions" pad={false}>
        {vers.isLoading ? <Spinner /> : <Table dense head={["Strategy", "Version", "Status", "Proposed by", "Notes", "Created", ""]} rows={(vers.data || []).map((v: any) => [
          v.strategy_key, v.version, <Badge tone={v.status === "production" ? "up" : v.status === "candidate" ? "warn" : "neutral"}>{v.status}</Badge>, v.proposed_by,
          <span className="text-xs text-muted truncate max-w-[300px] inline-block align-bottom">{v.notes}</span>, dt(v.created_at),
          v.status === "candidate" ? <Button size="sm" onClick={() => test.mutate(v.id)} disabled={test.isPending}>Run experiment</Button>
            : v.status === "retired" ? <Button size="sm" variant="ghost" onClick={() => confirm(`Restore ${v.strategy_key} v${v.version} to production?`) && rb.mutate(v.id)}>Rollback to this</Button> : null,
        ])} />}
      </Card>
    </div>
  );
}
