import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Badge, Button, Card, Empty, ErrorBox, Input, Notice, QualityBadge, Select, Table } from "../components/ui";
import { ago, dt } from "../format";

const PARAM_HINT: Record<string, Record<string, string>> = {
  price_above: { threshold: "150" }, price_below: { threshold: "100" }, pct_move: { pct: "0.05" }, valuation: { feature: "pe", op: "<=", value: "15" },
  filing: { forms: "10-K,10-Q,8-K" }, earnings: { days_before: "7" }, dividend: { days_before: "7" }, news: { min_abs_sentiment: "0.3" }, drawdown: { max_drawdown: "0.1" },
};

export default function Alerts() {
  const qc = useQueryClient();
  const kinds = useQuery({ queryKey: ["alert-kinds"], queryFn: () => api.get("/api/alerts/kinds") });
  const alerts = useQuery({ queryKey: ["alerts"], queryFn: () => api.get("/api/alerts") });
  const notifs = useQuery({ queryKey: ["notifications"], queryFn: () => api.get("/api/notifications?limit=200") });
  const pfs = useQuery({ queryKey: ["portfolios"], queryFn: () => api.get("/api/portfolios") });
  const [f, setF] = useState<any>({ kind: "price_above", symbol: "", portfolio_id: "", params: { threshold: "150" }, email: false, cooldown_minutes: "1440" });
  const inval = () => { qc.invalidateQueries({ queryKey: ["alerts"] }); qc.invalidateQueries({ queryKey: ["notifications"] }); qc.invalidateQueries({ queryKey: ["notif-count"] }); };
  const create = useMutation({
    mutationFn: () => {
      const params: any = { ...f.params };
      Object.keys(params).forEach((k) => {
        if (k === "forms") params[k] = String(params[k]).split(",").map((x: string) => x.trim()).filter(Boolean);
        else if (k !== "feature" && k !== "op") params[k] = Number(params[k]);
      });
      return api.post("/api/alerts", { kind: f.kind, symbol: f.symbol || null, portfolio_id: f.portfolio_id ? Number(f.portfolio_id) : null, params,
        channels: f.email ? ["in_app", "email"] : ["in_app"], cooldown_minutes: Number(f.cooldown_minutes) || 1440 });
    },
    onSuccess: inval,
  });
  const evalNow = useMutation({ mutationFn: () => api.post("/api/alerts/evaluate"), onSuccess: inval });
  const del = useMutation({ mutationFn: (id: number) => api.del(`/api/alerts/${id}`), onSuccess: inval });
  const toggle = useMutation({ mutationFn: (a: any) => api.patch(`/api/alerts/${a.id}`, { is_active: !a.is_active }), onSuccess: inval });
  const read = useMutation({ mutationFn: (id: number) => api.post(`/api/notifications/${id}/read`), onSuccess: inval });
  const readAll = useMutation({ mutationFn: () => api.post("/api/notifications/read-all"), onSuccess: inval });
  const needs = kinds.data?.kinds[f.kind]?.needs;
  return (
    <div className="space-y-4">
      <div><h1 className="text-xl font-semibold">Alerts & notifications</h1><p className="text-sm text-muted">Alerts are evaluated by the background agent; cooldowns and de-duplication prevent repetitive notifications. Prices that are stale never trigger price alerts.</p></div>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Create alert">
          <div className="grid grid-cols-2 gap-2">
            <Select label="Type" value={f.kind} onChange={(v) => setF({ ...f, kind: v, params: { ...(PARAM_HINT[v] || {}) } })} options={Object.keys(kinds.data?.kinds || {}).map((k) => ({ value: k, label: k.replace(/_/g, " ") }))} />
            {needs === "security" && <Input label="Symbol" value={f.symbol} onChange={(e) => setF({ ...f, symbol: e.target.value.toUpperCase() })} />}
            {needs === "portfolio" && <Select label="Portfolio" value={f.portfolio_id} onChange={(v) => setF({ ...f, portfolio_id: v })} options={[{ value: "", label: "Select…" }, ...(pfs.data || []).map((p: any) => ({ value: String(p.id), label: p.name }))]} />}
            {Object.keys(kinds.data?.kinds[f.kind]?.params || {}).map((k) => (
              <Input key={k} label={`${k} — ${kinds.data.kinds[f.kind].params[k]}`} value={f.params[k] ?? ""} onChange={(e) => setF({ ...f, params: { ...f.params, [k]: e.target.value } })} />
            ))}
            <Input label="Cooldown (minutes)" value={f.cooldown_minutes} onChange={(e) => setF({ ...f, cooldown_minutes: e.target.value })} />
          </div>
          <label className="flex items-center gap-2 text-sm mt-2">
            <input type="checkbox" checked={f.email} disabled={!kinds.data?.email_configured} onChange={(e) => setF({ ...f, email: e.target.checked })} />
            Also email {!kinds.data?.email_configured && <span className="text-xs text-muted">(SMTP not configured)</span>}
          </label>
          <div className="flex gap-2 mt-2"><Button variant="primary" onClick={() => create.mutate()} disabled={create.isPending}>Create</Button>
            <Button onClick={() => evalNow.mutate()} disabled={evalNow.isPending}>Evaluate all now</Button></div>
          <ErrorBox error={create.error || evalNow.error} />
          {evalNow.data && <Notice tone="info">{evalNow.data.fired.length} alert(s) fired{evalNow.data.errors.length ? `, ${evalNow.data.errors.length} error(s)` : ""}.</Notice>}
        </Card>
        <Card title={`Alerts (${alerts.data?.length || 0})`} pad={false}>
          {alerts.data?.length ? <Table dense head={["Type", "Target", "Params", "Last fired", "Active", ""]} rows={alerts.data.map((a: any) => [
            a.kind, a.symbol ? <span className="num">{a.symbol} {a.is_sample && <QualityBadge q="sample" />}</span> : a.portfolio_id ? `portfolio #${a.portfolio_id}` : "global",
            <span className="text-xs num">{JSON.stringify(a.params)}</span>, ago(a.last_triggered_at),
            <button onClick={() => toggle.mutate(a)}><Badge tone={a.is_active ? "up" : "neutral"}>{a.is_active ? "on" : "off"}</Badge></button>,
            <button onClick={() => del.mutate(a.id)} className="text-muted hover:text-down" aria-label="Delete"><Trash2 className="h-3.5 w-3.5" /></button>,
          ])} /> : <Empty>No alerts.</Empty>}
        </Card>
      </div>
      <Card title={`Notifications (${notifs.data?.unread || 0} unread)`} actions={<Button size="sm" onClick={() => readAll.mutate()}>Mark all read</Button>} pad={false}>
        {notifs.data?.items.length ? (
          <ul className="divide-y divide-line">{notifs.data.items.map((n: any) => (
            <li key={n.id} className={`p-3 text-sm ${n.read ? "opacity-70" : ""}`}>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={n.severity === "critical" ? "down" : n.severity === "warning" ? "warn" : "accent"}>{n.severity}</Badge>
                <b className="flex-1 min-w-0">{n.link ? <Link to={n.link} className="hover:text-accent">{n.title}</Link> : n.title}</b>
                <span className="text-xs text-muted">{n.category} · {dt(n.created_at, true)} · {n.channels.join("+")}</span>
                {!n.read && <Button size="sm" variant="ghost" onClick={() => read.mutate(n.id)}>Mark read</Button>}
              </div>
              {n.body && <p className="text-xs text-muted whitespace-pre-wrap mt-1">{n.body}</p>}
            </li>
          ))}</ul>
        ) : <Empty>No notifications.</Empty>}
      </Card>
    </div>
  );
}
