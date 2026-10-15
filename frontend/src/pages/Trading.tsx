import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Badge, Card, Empty, ErrorBox, Input, Notice, QualityBadge, Spinner } from "../components/ui";
import { num, pct } from "../format";

export default function Trading() {
  const [costs, setCosts] = useState("15");
  const q = useQuery({ queryKey: ["setups", costs], queryFn: () => api.get(`/api/screener/setups?costs_bps=${Number(costs) || 0}`) });
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Short-term trading research</h1>
          <p className="text-sm text-muted">Swing / position setups from daily bars. Intraday analysis requires an intraday data licence (not configured).</p>
        </div>
        <div className="w-48"><Input label="Cost per side (bps)" value={costs} onChange={(e) => setCosts(e.target.value)} /></div>
      </div>
      <Notice>Each setup is a hypothesis with explicit entry, invalidation and exit rules. The historical record shows what followed past occurrences
        <b> on the same instrument</b> — a descriptive statistic, not a validated forward-looking edge. Nothing here is an order.</Notice>
      {q.isLoading ? <Spinner label="Scanning universe…" /> : q.error ? <ErrorBox error={q.error} /> : (
        <>
          <p className="text-sm text-muted">Scanned {q.data.scanned} instruments ({q.data.universe} universe): {q.data.setups.length} active setup(s).</p>
          {!q.data.setups.length && <Card><Empty>No active setups today. That is a valid outcome; no trade is generated to fill the list.</Empty></Card>}
          <div className="grid gap-4 lg:grid-cols-2">
            {q.data.setups.map((s: any, i: number) => <SetupCard key={i} s={s} />)}
          </div>
        </>
      )}
    </div>
  );
}

export function SetupCard({ s }: { s: any }) {
  const h = s.historical;
  return (
    <Card title={<span className="normal-case tracking-normal text-fg text-sm">{s.symbol ? <><Link to={`/research/${s.symbol}`} className="num font-semibold hover:text-accent">{s.symbol}</Link> · </> : null}{s.title}</span>}
      actions={<>{s.is_sample && <QualityBadge q="sample" />}<Badge tone={h.n >= 30 && (h.t_stat ?? 0) > 2 ? "up" : "warn"}>{s.evidence_label.split(" (")[0].split(":")[0]}</Badge></>}>
      <div className="space-y-3 text-sm">
        <p className="text-muted">{s.hypothesis}</p>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div><div className="text-[0.7rem] uppercase text-muted">Entry ref.</div><div className="num font-semibold">{num(s.entry_reference)}</div></div>
          <div><div className="text-[0.7rem] uppercase text-muted">Stop (invalidation)</div><div className="num font-semibold text-down">{num(s.invalidation.stop)}</div></div>
          <div><div className="text-[0.7rem] uppercase text-muted">Target</div><div className="num font-semibold text-up">{num(s.exits[0].price)}</div></div>
          <div><div className="text-[0.7rem] uppercase text-muted">Reward / risk</div><div className="num font-semibold">{num(s.risk_reward.ratio, 1)}R</div></div>
        </div>
        <div className="grid sm:grid-cols-2 gap-3 text-xs">
          <div>
            <div className="font-medium mb-1">Entry conditions</div>
            <ul className="list-disc pl-4 text-muted">{s.entry_conditions.map((x: string) => <li key={x}>{x}</li>)}</ul>
            <div className="font-medium mt-2 mb-1">Exits & holding period</div>
            <ul className="list-disc pl-4 text-muted"><li>{s.invalidation.rule}</li>{s.exits.map((e: any) => <li key={e.rule}>{e.rule}</li>)}</ul>
          </div>
          <div>
            <div className="font-medium mb-1">Historical record on this instrument</div>
            {h.n ? (
              <ul className="text-muted space-y-0.5 num">
                <li>Occurrences: {h.n} (non-overlapping)</li>
                <li>Win rate: {pct(h.win_rate, 0)} (break-even at {pct(s.risk_reward.breakeven_win_rate, 0)})</li>
                <li>Avg / median return: {pct(h.avg_return, 2)} / {pct(h.median_return, 2)}</li>
                <li>Avg R: {num(h.avg_r_multiple, 2)} · worst {pct(h.worst_return, 1)}</li>
                <li>Exits: {h.exit_mix.stop} stop / {h.exit_mix.target} target / {h.exit_mix.time} time</li>
                <li>t-stat: {num(h.t_stat, 2)}</li>
              </ul>
            ) : <p className="text-muted">No prior occurrences.</p>}
            <p className="mt-1 text-muted">{s.evidence_label}</p>
          </div>
        </div>
        <div className="text-xs text-muted">Costs: {s.cost_assumptions.round_trip_bps} bps round trip included. {s.cost_assumptions.note}</div>
        {s.event_risk.length > 0 && <Notice>{s.event_risk.join("; ")}</Notice>}
        <p className="text-[0.7rem] text-muted">{s.caveat}</p>
      </div>
    </Card>
  );
}
