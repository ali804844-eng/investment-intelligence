import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { Button, Card, Empty, ErrorBox, Input, Modal, Notice, QualityBadge, Select, Spinner } from "../components/ui";
import { money, pct, signClass } from "../format";

const KIND_LABEL: Record<string, string> = { long_term: "Long-term investment", trading: "Short-term trading", research: "Watchlist / research", paper: "Paper trading", manual: "Imported / manual" };

export default function Portfolios() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const list = useQuery({ queryKey: ["portfolios"], queryFn: () => api.get("/api/portfolios") });
  const dash = useQuery({ queryKey: ["dashboard"], queryFn: () => api.get("/api/dashboard") });
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ name: "", kind: "long_term", base_currency: "USD", benchmark_symbol: "", is_hypothetical: true, cost_basis_method: "FIFO" });
  const create = useMutation({
    mutationFn: () => api.post("/api/portfolios", { ...f, benchmark_symbol: f.benchmark_symbol || null }),
    onSuccess: (p) => { qc.invalidateQueries(); setOpen(false); nav(`/portfolios/${p.id}`); },
  });
  if (list.isLoading) return <Spinner />;
  const stats = Object.fromEntries((dash.data?.portfolios || []).map((p: any) => [p.id, p]));
  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between gap-2">
        <div><h1 className="text-xl font-semibold">Portfolio studio</h1><p className="text-sm text-muted">Hypothetical and actual (manually recorded) portfolios are always labelled and kept separate.</p></div>
        <Button variant="primary" onClick={() => setOpen(true)}>New portfolio</Button>
      </div>
      {!list.data.length && <Card><Empty>No portfolios yet.</Empty></Card>}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {list.data.map((p: any) => {
          const s = stats[p.id];
          return (
            <Link key={p.id} to={`/portfolios/${p.id}`} className="rounded-xl border border-line bg-panel p-4 hover:border-accent transition block">
              <div className="flex items-center gap-2 flex-wrap">
                <h2 className="font-semibold">{p.name}</h2>
                <QualityBadge q={p.is_hypothetical ? "hypothetical" : "actual"} />
                {s && <QualityBadge q={s.data_quality} />}
              </div>
              <div className="text-xs text-muted mt-0.5">{KIND_LABEL[p.kind] || p.kind} · {p.base_currency} · benchmark {p.benchmark?.symbol || "none"} · {p.cost_basis_method}</div>
              {s && !s.error && (
                <div className="mt-3 grid grid-cols-3 gap-2 text-sm">
                  <div><div className="text-[0.65rem] uppercase text-muted">Value</div><div className="num font-semibold">{money(s.total_value)}</div></div>
                  <div><div className="text-[0.65rem] uppercase text-muted">Unrealized</div><div className={`num ${signClass(s.unrealized_pnl)}`}>{money(s.unrealized_pnl)}</div></div>
                  <div><div className="text-[0.65rem] uppercase text-muted">TWR 1Y</div><div className={`num ${signClass(s.periods?.["1Y"])}`}>{pct(s.periods?.["1Y"], 1, true)}</div></div>
                </div>
              )}
            </Link>
          );
        })}
      </div>
      <Modal open={open} onClose={() => setOpen(false)} title="New portfolio">
        <div className="space-y-3">
          <Input label="Name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
          <Select label="Type" value={f.kind} onChange={(v) => setF({ ...f, kind: v, is_hypothetical: v === "paper" ? true : f.is_hypothetical })}
            options={Object.entries(KIND_LABEL).map(([value, label]) => ({ value, label }))} />
          <div className="grid grid-cols-2 gap-2">
            <Input label="Base currency" value={f.base_currency} maxLength={3} onChange={(e) => setF({ ...f, base_currency: e.target.value.toUpperCase() })} />
            <Input label="Benchmark symbol" placeholder="e.g. SPY or SMBR" value={f.benchmark_symbol} onChange={(e) => setF({ ...f, benchmark_symbol: e.target.value.toUpperCase() })} />
          </div>
          <Select label="Cost basis method" value={f.cost_basis_method} onChange={(v) => setF({ ...f, cost_basis_method: v })} options={[{ value: "FIFO", label: "FIFO" }, { value: "AVG", label: "Average cost" }]} />
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={f.is_hypothetical} disabled={f.kind === "paper"} onChange={(e) => setF({ ...f, is_hypothetical: e.target.checked })} />
            Hypothetical (uncheck to record an actual account by entering its transactions)
          </label>
          <Notice tone="info">Connecting a live brokerage account is not supported. Actual accounts are tracked from transactions you enter or import (CSV).</Notice>
          <ErrorBox error={create.error} />
          <Button variant="primary" onClick={() => create.mutate()} disabled={!f.name || create.isPending}>Create</Button>
        </div>
      </Modal>
    </div>
  );
}
