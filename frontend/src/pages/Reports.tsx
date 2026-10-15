import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { Button, Card, Empty, ErrorBox, Input, QualityBadge, Select, Spinner, Table } from "../components/ui";
import { dt } from "../format";
import { ConclusionBadge } from "./Dashboard";

export default function Reports() {
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["reports"], queryFn: () => api.get("/api/research/reports") });
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api.get("/api/research/runs") });
  const [sym, setSym] = useState("");
  const [horizon, setHorizon] = useState("long");
  const [filter, setFilter] = useState("");
  const gen = useMutation({ mutationFn: () => api.post("/api/research/reports", { symbol: sym.toUpperCase(), horizon }), onSuccess: (r) => nav(`/research/reports/${r.id}`) });
  const rows = (q.data || []).filter((r: any) => !filter || r.conclusion === filter);
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Research</h1>
        <p className="text-sm text-muted">Standardized, source-linked reports. Every report is stored and searchable; material conclusions enter the prediction registry.</p>
      </div>
      <Card title="Generate a report">
        <div className="flex flex-wrap gap-2 items-end">
          <div className="w-40"><Input label="Symbol" value={sym} onChange={(e) => setSym(e.target.value)} /></div>
          <div className="w-44"><Select label="Horizon" value={horizon} onChange={setHorizon} options={[{ value: "long", label: "Long-term (3-5y)" }, { value: "short", label: "Short-term (weeks)" }]} /></div>
          <Button variant="primary" onClick={() => gen.mutate()} disabled={!sym || gen.isPending}>{gen.isPending ? "Generating…" : "Generate"}</Button>
          <span className="text-xs text-muted">Or open any security via the search bar to explore it interactively.</span>
        </div>
        <ErrorBox error={gen.error} />
      </Card>
      <div className="grid gap-4 xl:grid-cols-3">
        <Card title={`Report history (${rows.length})`} className="xl:col-span-2" pad={false}
          actions={<Select className="w-48" value={filter} onChange={setFilter} options={[{ value: "", label: "All conclusions" }, { value: "consider", label: "Consider" }, { value: "monitor", label: "Monitor" }, { value: "reject", label: "Reject" }, { value: "insufficient_evidence", label: "Insufficient evidence" }]} />}>
          {q.isLoading ? <Spinner /> : rows.length ? (
            <Table dense head={["Date", "Symbol", "Conclusion", "Horizon", "Data", "By", "Summary"]} rows={rows.map((r: any) => [
              <Link to={`/research/reports/${r.id}`} className="text-accent">{dt(r.created_at, true)}</Link>, <Link to={`/research/${r.symbol}`} className="num font-semibold">{r.symbol}</Link>,
              <ConclusionBadge c={r.conclusion} />, r.horizon, <QualityBadge q={r.data_quality} />, r.created_by,
              <span className="text-xs text-muted truncate max-w-[380px] inline-block align-bottom">{r.summary}</span>,
            ])} />
          ) : <Empty>No reports yet.</Empty>}
        </Card>
        <Card title="Research cycles" pad={false}>
          {runs.data?.length ? <Table dense head={["When", "Kind", "Reports", "Top"]} rows={runs.data.slice(0, 20).map((r: any) => [dt(r.created_at, true), r.kind, r.n_reports, r.top.join(", ")])} /> : <Empty>None yet.</Empty>}
        </Card>
      </div>
    </div>
  );
}
