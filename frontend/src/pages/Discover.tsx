import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ChevronDown, ChevronRight, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { Badge, Button, Card, Empty, ErrorBox, Input, Notice, QualityBadge, Select, Spinner, Table, Tabs } from "../components/ui";
import { ago, fmtFeature, money, num, pct } from "../format";

export default function Discover() {
  const [tab, setTab] = useState("screen");
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Opportunity discovery</h1>
        <p className="text-sm text-muted">Screens find securities that deserve research. A high score is a research priority, not a forecast or a recommendation.</p>
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[{ key: "screen", label: "Strategy screens" }, { key: "custom", label: "Custom screen" }, { key: "queue", label: "Agent research queue" }]} />
      {tab === "screen" && <StrategyScreens />}
      {tab === "custom" && <CustomScreen />}
      {tab === "queue" && <Queue />}
    </div>
  );
}

function StrategyScreens() {
  const st = useQuery({ queryKey: ["strategies"], queryFn: () => api.get("/api/screener/strategies") });
  const [key, setKey] = useState("quality");
  const run = useQuery({ queryKey: ["screen", key], queryFn: () => api.post("/api/screener/run", { strategy_key: key, top_n: 15 }) });
  if (st.isLoading) return <Spinner />;
  const s = st.data?.strategies.find((x: any) => x.key === key);
  return (
    <div className="space-y-4">
      <div className="grid gap-2 grid-cols-2 md:grid-cols-3 xl:grid-cols-6">
        {st.data?.strategies.map((x: any) => (
          <button key={x.key} onClick={() => setKey(x.key)}
            className={clsx("text-left rounded-xl border p-3 transition", x.key === key ? "border-accent bg-panel" : "border-line bg-panel hover:border-muted")}>
            <div className="text-sm font-semibold">{x.title}</div>
            <div className="text-xs text-muted mt-1 line-clamp-2">{x.description}</div>
            <div className="text-[0.65rem] text-muted mt-1">v{x.version} · {x.horizon}-term</div>
          </button>
        ))}
      </div>
      {s && (
        <Card title="Criteria" subtitle={`Production version ${s.version}. Change parameters through a tested proposal in the Learning Lab.`}>
          <div className="flex flex-wrap gap-2 text-xs">
            {s.params.criteria.map((c: any, i: number) => (
              <Badge key={i} tone={c.required ? "accent" : "neutral"} title={st.data.features[c.feature]?.label}>
                {st.data.features[c.feature]?.label || c.feature} {c.op} {fmtFeature(c.value, st.data.features[c.feature]?.fmt)}{c.required ? " (required)" : ""}
              </Badge>
            ))}
            <span className="text-muted">Min pass ratio {pct(s.params.min_pass_ratio, 0)} · universe: liquidity ≥ {money(s.params.universe?.min_adv_usd, "USD", 0)}/day</span>
          </div>
        </Card>
      )}
      <Results r={run.data} loading={run.isFetching} error={run.error} />
    </div>
  );
}

export function Results({ r, loading, error }: { r: any; loading?: boolean; error?: unknown }) {
  if (loading) return <Spinner label="Screening…" />;
  if (error) return <ErrorBox error={error} />;
  if (!r) return null;
  return (
    <Card title={`${r.n_candidates} candidates`} subtitle={`${r.n_evaluated} evaluated of ${r.n_universe} in the ${r.universe} universe · as of ${r.as_of}`}>
      {r.universe === "SAMPLE" && <div className="mb-3"><Notice tone="sample">Screening the SAMPLE universe (fictional companies). Results demonstrate the workflow only.</Notice></div>}
      {r.candidates.length ? <div className="space-y-2">{r.candidates.map((c: any) => <CandidateRow key={c.security_id} c={c} />)}</div>
        : <Empty>No security meets all required criteria. That is a valid result — nothing is forced onto the list.</Empty>}
      {r.near_misses?.length > 0 && (
        <details className="mt-4">
          <summary className="cursor-pointer text-sm text-muted">Near misses ({r.near_misses.length})</summary>
          <Table dense head={["Symbol", "Pass ratio", "Failed", "Missing data"]} rows={r.near_misses.map((n: any) => [
            <Link to={`/research/${n.symbol}`} className="num font-semibold">{n.symbol}</Link>, pct(n.pass_ratio, 0),
            <span className="text-xs whitespace-normal">{n.failed.join("; ") || "—"}</span>, <span className="text-xs">{n.missing.join(", ") || "—"}</span>,
          ])} />
        </details>
      )}
    </Card>
  );
}

function CandidateRow({ c }: { c: any }) {
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  const qc = useQueryClient();
  const report = useMutation({ mutationFn: () => api.post("/api/research/reports", { symbol: c.symbol }), onSuccess: (r) => nav(`/research/reports/${r.id}`) });
  const wl = useQuery({ queryKey: ["watchlists"], queryFn: () => api.get("/api/watchlists") });
  const addWl = useMutation({ mutationFn: () => api.post(`/api/watchlists/${wl.data[0].id}/items`, { symbol: c.symbol, note: `From ${c.why_selected?.slice(0, 120)}` }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlists"] }) });
  const k = c.key_metrics;
  return (
    <div className="rounded-lg border border-line">
      <button onClick={() => setOpen(!open)} className="flex w-full flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 text-left hover:bg-panel2/60">
        {open ? <ChevronDown className="h-4 w-4 text-muted" /> : <ChevronRight className="h-4 w-4 text-muted" />}
        <span className="num w-6 text-muted">#{c.rank ?? "—"}</span>
        <span className="num font-semibold w-16">{c.symbol}</span>
        <span className="truncate text-sm flex-1 min-w-[140px]">{c.name}</span>
        {c.is_sample && <QualityBadge q="sample" />}
        <span className="text-xs text-muted hidden md:inline">{c.sector}</span>
        <span className="num text-xs">P/E {num(k.pe, 1)}</span>
        <span className="num text-xs">FCF yld {pct(k.fcf_yield)}</span>
        <span className="num text-xs">ROIC {pct(k.roic_avg)}</span>
        <span className="num text-xs">score {num(c.score, 2)}</span>
        {c.risks.filter((x: any) => x.severity === "high").length > 0 && <Badge tone="down">{c.risks.filter((x: any) => x.severity === "high").length} high risk</Badge>}
      </button>
      {open && (
        <div className="border-t border-line p-3 grid gap-4 md:grid-cols-2 xl:grid-cols-3 text-sm">
          <div>
            <h4 className="font-medium mb-1">1. Why selected</h4><p className="text-muted">{c.why_selected}</p>
            <h4 className="font-medium mt-3 mb-1">2. Criteria</h4>
            <ul className="space-y-0.5 text-xs">
              {c.criteria_passed.map((x: string) => <li key={x} className="text-up">✓ {x}</li>)}
              {c.criteria_failed.map((x: string) => <li key={x} className="text-down">✗ {x}</li>)}
              {c.criteria_missing.map((x: string) => <li key={x} className="text-warn">? {x} — data missing</li>)}
            </ul>
          </div>
          <div>
            <h4 className="font-medium mb-1">3. Valuation & fundamentals</h4>
            <dl className="grid grid-cols-2 gap-x-3 text-xs num">
              <dt className="text-muted">Price</dt><dd>{num(k.price)}</dd>
              <dt className="text-muted">Mkt cap (USD)</dt><dd>{money(k.market_cap_usd, undefined, 0)}</dd>
              <dt className="text-muted">EV/EBITDA</dt><dd>{num(k.ev_ebitda, 1)}</dd>
              <dt className="text-muted">Dividend yield</dt><dd>{pct(k.dividend_yield)}</dd>
              <dt className="text-muted">Op. margin</dt><dd>{pct(k.operating_margin)}</dd>
              <dt className="text-muted">Revenue CAGR 3y</dt><dd>{pct(k.revenue_cagr_3y)}</dd>
              <dt className="text-muted">Net debt/EBITDA</dt><dd>{num(k.net_debt_to_ebitda, 1)}</dd>
              <dt className="text-muted">Momentum 12-1</dt><dd>{pct(k.mom_12_1)}</dd>
              <dt className="text-muted">Volatility 1y</dt><dd>{pct(k.vol_252d)}</dd>
            </dl>
            <h4 className="font-medium mt-3 mb-1">4. Principal risks</h4>
            <ul className="text-xs space-y-0.5">{c.risks.length ? c.risks.map((r: any) => <li key={r.code}><Badge tone={r.severity === "high" ? "down" : r.severity === "medium" ? "warn" : "neutral"}>{r.severity}</Badge> {r.message}</li>) : <li className="text-muted">No rule-based flags.</li>}</ul>
          </div>
          <div>
            <h4 className="font-medium mb-1">5. What would invalidate the thesis</h4>
            <ul className="text-xs list-disc pl-4 text-muted">{c.invalidation.map((x: string) => <li key={x}>{x}</li>)}</ul>
            <h4 className="font-medium mt-3 mb-1">6. Missing or outdated information</h4>
            <p className="text-xs text-muted">{c.missing_information.length ? c.missing_information.join(", ") : "None detected"}</p>
            <h4 className="font-medium mt-3 mb-1">7. Further research required</h4>
            <ul className="text-xs list-disc pl-4 text-muted">{c.further_research.map((x: string) => <li key={x}>{x}</li>)}</ul>
            <div className="flex flex-wrap gap-2 mt-3">
              <Button size="sm" variant="primary" onClick={() => report.mutate()} disabled={report.isPending}>{report.isPending ? "Generating…" : "Full research report"}</Button>
              <Button size="sm" onClick={() => nav(`/research/${c.symbol}`)}>Open workspace</Button>
              {wl.data?.[0] && <Button size="sm" onClick={() => addWl.mutate()} disabled={addWl.isPending || addWl.isSuccess}>{addWl.isSuccess ? "Added" : "Add to watchlist"}</Button>}
            </div>
            <ErrorBox error={report.error || addWl.error} />
            <p className="text-[0.65rem] text-muted mt-2">{c.disclaimer}</p>
          </div>
        </div>
      )}
    </div>
  );
}

function CustomScreen() {
  const st = useQuery({ queryKey: ["strategies"], queryFn: () => api.get("/api/screener/strategies") });
  const [crit, setCrit] = useState<any[]>([{ feature: "pe", op: "<=", value: 20, required: true }, { feature: "roic_avg", op: ">=", value: 0.1, required: false }]);
  const [weights, setWeights] = useState<Record<string, number>>({ fcf_yield: 1 });
  const [filters, setFilters] = useState({ country: "", sector: "", min_adv_usd: "1000000", min_pass_ratio: "1" });
  const run = useMutation({
    mutationFn: () => api.post("/api/screener/run", {
      criteria: crit.map((c) => ({ ...c, value: Number(c.value) })), weights, top_n: 30, country: filters.country || null, sector: filters.sector || null,
      min_adv_usd: Number(filters.min_adv_usd) || 0, min_pass_ratio: Number(filters.min_pass_ratio) || 1,
    }),
  });
  const feats = st.data?.features || {};
  const fopts = Object.entries(feats).map(([k, v]: any) => ({ value: k, label: `${v.label} (${v.group})` }));
  return (
    <div className="space-y-4">
      <Card title="Build a screen" subtitle="Values for percentages are decimals (0.15 = 15%). Required criteria must pass; others count toward the pass ratio.">
        <div className="space-y-2">
          {crit.map((c, i) => (
            <div key={i} className="grid grid-cols-[1fr_80px_110px_auto_auto] gap-2 items-center">
              <Select value={c.feature} onChange={(v) => setCrit(crit.map((x, j) => (j === i ? { ...x, feature: v } : x)))} options={fopts} />
              <Select value={c.op} onChange={(v) => setCrit(crit.map((x, j) => (j === i ? { ...x, op: v } : x)))} options={[">=", "<=", ">", "<"].map((o) => ({ value: o, label: o }))} />
              <Input value={c.value} onChange={(e) => setCrit(crit.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} />
              <label className="text-xs flex items-center gap-1"><input type="checkbox" checked={c.required} onChange={(e) => setCrit(crit.map((x, j) => (j === i ? { ...x, required: e.target.checked } : x)))} />required</label>
              <button onClick={() => setCrit(crit.filter((_, j) => j !== i))} className="text-muted hover:text-down" aria-label="Remove"><Trash2 className="h-4 w-4" /></button>
            </div>
          ))}
          <Button size="sm" onClick={() => setCrit([...crit, { feature: "fcf_yield", op: ">=", value: 0.04, required: false }])}><Plus className="h-3 w-3" />Criterion</Button>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5 pt-3 border-t border-line">
            <Select label="Rank by (z-score)" value={Object.keys(weights)[0] || ""} onChange={(v) => setWeights({ [v]: weights[Object.keys(weights)[0]] ?? 1 })} options={fopts} />
            <Select label="Direction" value={String(Math.sign(Object.values(weights)[0] ?? 1))} onChange={(v) => setWeights({ [Object.keys(weights)[0]]: Number(v) })}
              options={[{ value: "1", label: "Higher is better" }, { value: "-1", label: "Lower is better" }]} />
            <Input label="Country (ISO2)" value={filters.country} onChange={(e) => setFilters({ ...filters, country: e.target.value.toUpperCase() })} />
            <Input label="Min $ volume/day" value={filters.min_adv_usd} onChange={(e) => setFilters({ ...filters, min_adv_usd: e.target.value })} />
            <Input label="Min pass ratio (0-1)" value={filters.min_pass_ratio} onChange={(e) => setFilters({ ...filters, min_pass_ratio: e.target.value })} />
          </div>
          <Button variant="primary" onClick={() => run.mutate()} disabled={run.isPending}>Run screen</Button>
        </div>
      </Card>
      <Results r={run.data} loading={run.isPending} error={run.error} />
    </div>
  );
}

function Queue() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["queue"], queryFn: () => api.get("/api/screener/queue") });
  const patch = useMutation({ mutationFn: ({ id, status }: { id: number; status: string }) => api.patch(`/api/screener/candidates/${id}`, { status }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["queue"] }) });
  if (q.isLoading) return <Spinner />;
  if (!q.data?.run) return <Card><Empty>No research run yet. Queue one from <Link to="/control" className="text-accent">Agent Control</Link>.</Empty></Card>;
  const queue = q.data.run.summary?.queue || [];
  return (
    <div className="space-y-4">
      <Card title="Ranked research queue" subtitle={`Run #${q.data.run.id} (${q.data.run.kind}) ${ago(q.data.run.created_at)} — priority = strategy score adjusted by report conclusion`}>
        {queue.length ? (
          <Table dense head={["#", "Symbol", "Strategy", "Score", "Priority", "Report"]} rows={queue.map((r: any, i: number) => [
            i + 1, <Link to={`/research/${r.symbol}`} className="num font-semibold">{r.symbol}</Link>, r.strategy, num(r.score, 2), num(r.priority, 2),
            r.report ? <Link to={`/research/reports/${r.report.report_id}`} className="text-accent">#{r.report.report_id} {r.report.conclusion}</Link> : "—",
          ])} />
        ) : <Empty>Empty queue.</Empty>}
      </Card>
      <Card title="Candidates by strategy" pad={false}>
        <Table dense head={["Strategy", "Rank", "Symbol", "Score", "Data", "Status", ""]} rows={q.data.candidates.map((c: any) => [
          c.strategy, c.rank, <Link to={`/research/${c.symbol}`} className="num font-semibold">{c.symbol}</Link>, num(c.score, 2), <QualityBadge q={c.data_quality} />,
          <Badge>{c.status}</Badge>,
          <span className="flex gap-1">{["reviewing", "dismissed", "promoted"].map((s) => <Button key={s} size="sm" variant="ghost" onClick={() => patch.mutate({ id: c.id, status: s })}>{s}</Button>)}</span>,
        ])} />
      </Card>
    </div>
  );
}
