import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pause, Play, Square, Trash2 } from "lucide-react";
import { useState } from "react";
import { api } from "../api";
import PendingActionCard from "../components/PendingActionCard";
import { Badge, Button, Card, Empty, ErrorBox, Input, Modal, Notice, Select, Spinner, Stat, Table, Tabs } from "../components/ui";
import { ago, dt, money, num } from "../format";
import { ProvStatus } from "./Dashboard";

export default function Control() {
  const [tab, setTab] = useState("status");
  const st = useQuery({ queryKey: ["agent-status"], queryFn: () => api.get("/api/agent/status"), refetchInterval: 15_000 });
  const qc = useQueryClient();
  const setState = useMutation({ mutationFn: (state: string) => api.put("/api/agent/settings", { state }), onSuccess: () => qc.invalidateQueries() });
  if (st.isLoading) return <Spinner />;
  if (st.error) return <ErrorBox error={st.error} />;
  const s = st.data;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-xl font-semibold">Agent control center</h1><p className="text-sm text-muted">Background research service: scheduling, jobs, data health, costs, budgets and memory.</p></div>
        <div className="flex gap-2">
          <Button onClick={() => setState.mutate("running")} disabled={s.state === "running"}><Play className="h-4 w-4" />Resume</Button>
          <Button onClick={() => setState.mutate("paused")} disabled={s.state === "paused"}><Pause className="h-4 w-4" />Pause</Button>
          <Button variant="danger" onClick={() => confirm("Stop the agent? Queued scheduled jobs will be cancelled.") && setState.mutate("stopped")} disabled={s.state === "stopped"}><Square className="h-4 w-4" />Stop</Button>
        </div>
      </div>
      {!s.continuous_operation.verified && <Notice><b>Continuous operation not verified.</b> {s.continuous_operation.explanation} (Run <code>python -m app.worker.runner</code> or the <code>worker</code> service in docker-compose.)</Notice>}
      <div className="grid gap-3 grid-cols-2 md:grid-cols-3 xl:grid-cols-6">
        <Card><Stat label="Agent state" value={<Badge tone={s.state === "running" ? "up" : s.state === "paused" ? "warn" : "down"}>{s.state}</Badge>} /></Card>
        <Card><Stat label="Worker" value={<Badge tone={s.continuous_operation.verified ? "up" : "down"}>{s.continuous_operation.verified ? "alive" : "offline"}</Badge>} sub={s.workers[0] ? `seen ${ago(s.workers[0].last_seen_at)}` : "never seen"} /></Card>
        <Card><Stat label="Jobs (7d)" value={Object.values<number>(s.job_counts_7d).reduce((a, b) => a + b, 0)} sub={Object.entries(s.job_counts_7d).map(([k, v]) => `${k} ${v}`).join(" · ")} /></Card>
        <Card><Stat label="LLM" value={s.llm.configured ? "On" : "Off"} sub={s.llm.configured ? "tool-using agent" : "no API key · deterministic mode"} /></Card>
        <Card><Stat label="AI spend today" value={money(s.llm.spent_today_usd, "USD")} sub={`budget ${money(s.llm.daily_budget_usd, "USD")}/day`} /></Card>
        <Card><Stat label="AI spend month" value={money(s.llm.spent_month_usd, "USD")} sub={`budget ${money(s.llm.monthly_budget_usd, "USD")}/mo`} /></Card>
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[{ key: "status", label: "Status & data health" }, { key: "jobs", label: "Jobs" }, { key: "schedules", label: "Schedules" },
        { key: "proposals", label: "Proposals" }, { key: "settings", label: "Hours & budgets" }, { key: "memory", label: "Memory" }]} />
      {tab === "status" && <Status s={s} />}
      {tab === "jobs" && <Jobs />}
      {tab === "schedules" && <Schedules />}
      {tab === "proposals" && <Proposals />}
      {tab === "settings" && <SettingsForm s={s.settings} />}
      {tab === "memory" && <Memory />}
    </div>
  );
}

function Status({ s }: { s: any }) {
  const usage = s.usage_30d as any[];
  const byProv: Record<string, { calls: number; cost: number }> = {};
  usage.forEach((u) => { byProv[u.provider] ||= { calls: 0, cost: 0 }; byProv[u.provider].calls += u.calls; byProv[u.provider].cost += u.est_cost_usd; });
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card title="Data sources" pad={false}>
        <Table dense head={["Provider", "Status", "Last success", "Last error", "Calls today"]} rows={s.providers.map((p: any) => [
          <span title={p.licensing}>{p.title}</span>, <ProvStatus s={p.status} />, ago(p.last_success_at),
          p.last_error ? <span className="text-xs text-down truncate max-w-[220px] inline-block align-bottom" title={p.last_error}>{ago(p.last_error_at)}: {p.last_error}</span> : "—",
          `${p.calls_today}${p.daily_limit ? ` / ${p.daily_limit}` : ""}`,
        ])} />
      </Card>
      <Card title="Last successful run by job type">
        <Table dense head={["Job", "Last success"]} rows={Object.entries(s.last_success_by_job).map(([k, v]: any) => [k, dt(v, true)])} />
        {!Object.keys(s.last_success_by_job).length && <Empty>No successful runs yet.</Empty>}
      </Card>
      <Card title="Workers" pad={false}>
        {s.workers.length ? <Table dense head={["Worker", "Alive", "Last seen", "Started", "Jobs"]} rows={s.workers.map((w: any) => [w.worker_id, <Badge tone={w.alive ? "up" : "down"}>{w.alive ? "yes" : "no"}</Badge>, ago(w.last_seen_at), dt(w.started_at, true), w.jobs_processed])} /> : <Empty>No worker has ever connected.</Empty>}
      </Card>
      <Card title="API usage & estimated cost (30 days)">
        <Table dense head={["Provider", "Calls", "Est. cost (USD)"]} rows={Object.entries(byProv).map(([k, v]) => [k, v.calls, k === "anthropic" ? num(v.cost, 4) : "subscription / free tier"])} />
        <p className="text-xs text-muted mt-2">LLM cost is estimated from token usage at configured prices. Data-provider costs depend on your plan.</p>
      </Card>
    </div>
  );
}

function Jobs() {
  const qc = useQueryClient();
  const [status, setStatus] = useState("");
  const jobs = useQuery({ queryKey: ["jobs", status], queryFn: () => api.get(`/api/agent/jobs?limit=200${status ? `&status=${status}` : ""}`), refetchInterval: 5_000 });
  const [sel, setSel] = useState<number | null>(null);
  const [jt, setJt] = useState("research_cycle");
  const enqueue = useMutation({ mutationFn: () => api.post("/api/agent/jobs", { job_type: jt }), onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }) });
  const retry = useMutation({ mutationFn: (id: number) => api.post(`/api/agent/jobs/${id}/retry`), onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }) });
  const cancel = useMutation({ mutationFn: (id: number) => api.post(`/api/agent/jobs/${id}/cancel`), onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }) });
  return (
    <div className="space-y-4">
      <Card title="Run a job now">
        <div className="flex flex-wrap gap-2 items-end">
          <div className="w-64"><Select value={jt} onChange={setJt} options={["research_cycle", "data_refresh", "market_review", "watchlist_review", "screening", "filings_monitor", "prediction_evaluation", "performance_review", "strategy_validation", "data_health"].map((k) => ({ value: k, label: k.replace(/_/g, " ") }))} /></div>
          <Button variant="primary" onClick={() => enqueue.mutate()} disabled={enqueue.isPending}>Queue</Button>
          <div className="w-44 ml-auto"><Select value={status} onChange={setStatus} options={[{ value: "", label: "All statuses" }, ...["queued", "running", "succeeded", "failed", "cancelled"].map((v) => ({ value: v, label: v }))]} /></div>
        </div>
        <ErrorBox error={enqueue.error || retry.error || cancel.error} />
      </Card>
      <Card pad={false}>
        {jobs.data?.length ? <Table dense head={["#", "Type", "Status", "Trigger", "Attempts", "Created", "Duration", "Error", ""]} rows={jobs.data.map((j: any) => [
          <button className="text-accent" onClick={() => setSel(j.id)}>{j.id}</button>, j.job_type, <JobBadge s={j.status} />, j.trigger, `${j.attempts}/${j.max_attempts}`, ago(j.created_at),
          j.duration_s ? `${num(j.duration_s, 1)}s` : "—", <span className="text-xs text-down truncate max-w-[220px] inline-block align-bottom" title={j.error || ""}>{j.error || ""}</span>,
          <span className="flex gap-1">{["failed", "cancelled"].includes(j.status) && <Button size="sm" onClick={() => retry.mutate(j.id)}>Retry</Button>}
            {["queued", "running"].includes(j.status) && <Button size="sm" variant="ghost" onClick={() => cancel.mutate(j.id)}>Cancel</Button>}</span>,
        ])} /> : <Empty>No jobs.</Empty>}
      </Card>
      {sel && <JobModal id={sel} onClose={() => setSel(null)} />}
    </div>
  );
}

function JobBadge({ s }: { s: string }) {
  return <Badge tone={s === "succeeded" ? "up" : s === "failed" ? "down" : s === "running" ? "accent" : s === "queued" ? "warn" : "neutral"}>{s}</Badge>;
}

function JobModal({ id, onClose }: { id: number; onClose: () => void }) {
  const q = useQuery({ queryKey: ["job", id], queryFn: () => api.get(`/api/agent/jobs/${id}`), refetchInterval: 3000 });
  return (
    <Modal open onClose={onClose} title={`Job #${id}`}>
      {q.isLoading ? <Spinner /> : q.data && (
        <div className="space-y-3 text-sm">
          <div className="flex gap-2 items-center"><b>{q.data.job_type}</b><JobBadge s={q.data.status} /><span className="text-xs text-muted">{q.data.worker_id}</span></div>
          <div>
            <div className="text-xs font-medium text-muted mb-1">Execution log</div>
            <ol className="space-y-1 max-h-72 overflow-y-auto">{(q.data.log || []).map((l: any, i: number) => (
              <li key={i} className="text-xs"><span className="num text-muted">{l.t?.slice(11, 19)}</span> <Badge tone={l.level === "error" ? "down" : l.level === "warning" ? "warn" : "neutral"}>{l.step}</Badge> {l.message}
                {l.data && <details><summary className="cursor-pointer text-muted">data</summary><pre className="overflow-x-auto bg-panel2 p-1 rounded text-[0.65rem]">{typeof l.data === "string" ? l.data : JSON.stringify(l.data, null, 1).slice(0, 3000)}</pre></details>}</li>
            ))}</ol>
          </div>
          {q.data.error_history?.length > 0 && <div><div className="text-xs font-medium text-muted mb-1">Retry history</div>
            <ul className="text-xs space-y-0.5">{q.data.error_history.map((e: any) => <li key={e.attempt}>Attempt {e.attempt} ({dt(e.at, true)}): <span className="text-down">{e.error}</span></li>)}</ul></div>}
          {q.data.result && <details><summary className="text-xs text-muted cursor-pointer">Result</summary><pre className="text-[0.65rem] bg-panel2 p-2 rounded overflow-x-auto">{JSON.stringify(q.data.result, null, 1).slice(0, 5000)}</pre></details>}
        </div>
      )}
    </Modal>
  );
}

function Schedules() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["schedules"], queryFn: () => api.get("/api/agent/schedules"), refetchInterval: 30_000 });
  const patch = useMutation({ mutationFn: ({ id, body }: { id: number; body: any }) => api.patch(`/api/agent/schedules/${id}`, body), onSuccess: () => qc.invalidateQueries({ queryKey: ["schedules"] }) });
  const del = useMutation({ mutationFn: (id: number) => api.del(`/api/agent/schedules/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["schedules"] }) });
  const [f, setF] = useState({ name: "", job_type: "watchlist_review", cron: "0 14 * * 1-5" });
  const create = useMutation({ mutationFn: () => api.post("/api/agent/schedules", f), onSuccess: () => qc.invalidateQueries({ queryKey: ["schedules"] }) });
  return (
    <div className="space-y-4">
      <Card pad={false} title="Research schedules (cron, UTC)">
        <Table dense head={["Name", "Job", "Cron", "Next run", "Last run", "Last status", "Enabled", ""]} rows={(q.data || []).map((s: any) => [
          <span title={s.description}>{s.name}</span>, s.job_type,
          <input defaultValue={s.cron} className="num w-32 rounded border border-line bg-panel2 px-1.5 py-0.5 text-xs" onBlur={(e) => e.target.value !== s.cron && patch.mutate({ id: s.id, body: { cron: e.target.value } })} aria-label="cron" />,
          ago(s.next_run_at), ago(s.last_run_at), <span className="text-xs">{s.last_status || "—"}</span>,
          <button onClick={() => patch.mutate({ id: s.id, body: { enabled: !s.enabled } })}><Badge tone={s.enabled ? "up" : "neutral"}>{s.enabled ? "on" : "off"}</Badge></button>,
          <button onClick={() => confirm(`Delete schedule "${s.name}"?`) && del.mutate(s.id)} className="text-muted hover:text-down" aria-label="Delete"><Trash2 className="h-3.5 w-3.5" /></button>,
        ])} />
      </Card>
      <Card title="Add schedule">
        <div className="grid gap-2 md:grid-cols-4 items-end">
          <Input label="Name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
          <Select label="Job" value={f.job_type} onChange={(v) => setF({ ...f, job_type: v })} options={["research_cycle", "data_refresh", "market_review", "watchlist_review", "screening", "filings_monitor", "prediction_evaluation", "performance_review", "strategy_validation", "data_health"].map((k) => ({ value: k, label: k }))} />
          <Input label="Cron (UTC, min 5-minute interval)" value={f.cron} onChange={(e) => setF({ ...f, cron: e.target.value })} />
          <Button variant="primary" onClick={() => create.mutate()} disabled={!f.name}>Add</Button>
        </div>
        <ErrorBox error={create.error || patch.error} />
      </Card>
    </div>
  );
}

function Proposals() {
  const [status, setStatus] = useState("pending");
  const q = useQuery({ queryKey: ["pending", status], queryFn: () => api.get(`/api/agent/pending${status ? `?status=${status}` : "?status="}`) });
  return (
    <div className="space-y-3">
      <div className="w-48"><Select value={status} onChange={setStatus} options={[{ value: "pending", label: "Pending" }, { value: "executed", label: "Executed" }, { value: "rejected", label: "Rejected" }, { value: "failed", label: "Failed" }, { value: "", label: "All" }]} /></div>
      {q.data?.length ? q.data.map((a: any) => <PendingActionCard key={a.id} action={a} />) : <Card><Empty>No proposals.</Empty></Card>}
    </div>
  );
}

function SettingsForm({ s }: { s: any }) {
  const qc = useQueryClient();
  const [f, setF] = useState<any>({ ...s });
  const save = useMutation({ mutationFn: () => api.put("/api/agent/settings", {
    timezone: f.timezone, operating_start_hour: Number(f.operating_start_hour), operating_end_hour: Number(f.operating_end_hour), operating_weekdays_only: !!f.operating_weekdays_only,
    daily_llm_budget_usd: Number(f.daily_llm_budget_usd), monthly_llm_budget_usd: Number(f.monthly_llm_budget_usd), daily_provider_call_budget: Number(f.daily_provider_call_budget),
    max_reports_per_cycle: Number(f.max_reports_per_cycle), notify_min_severity: f.notify_min_severity }), onSuccess: () => qc.invalidateQueries({ queryKey: ["agent-status"] }) });
  return (
    <Card title="Operating hours, budgets & notification threshold">
      <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-5">
        <Input label="Timezone (IANA)" value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })} />
        <Input label="Operating start hour" value={f.operating_start_hour} onChange={(e) => setF({ ...f, operating_start_hour: e.target.value })} />
        <Input label="Operating end hour" value={f.operating_end_hour} onChange={(e) => setF({ ...f, operating_end_hour: e.target.value })} />
        <label className="flex items-center gap-2 text-sm mt-5"><input type="checkbox" checked={!!f.operating_weekdays_only} onChange={(e) => setF({ ...f, operating_weekdays_only: e.target.checked })} />Weekdays only</label>
        <Select label="Notify at or above" value={f.notify_min_severity} onChange={(v) => setF({ ...f, notify_min_severity: v })} options={["info", "warning", "critical"].map((v) => ({ value: v, label: v }))} />
        <Input label="Daily AI budget (USD)" value={f.daily_llm_budget_usd} onChange={(e) => setF({ ...f, daily_llm_budget_usd: e.target.value })} />
        <Input label="Monthly AI budget (USD)" value={f.monthly_llm_budget_usd} onChange={(e) => setF({ ...f, monthly_llm_budget_usd: e.target.value })} />
        <Input label="Daily data-provider calls" value={f.daily_provider_call_budget} onChange={(e) => setF({ ...f, daily_provider_call_budget: e.target.value })} />
        <Input label="Max reports per cycle" value={f.max_reports_per_cycle} onChange={(e) => setF({ ...f, max_reports_per_cycle: e.target.value })} />
      </div>
      <Button className="mt-3" variant="primary" onClick={() => save.mutate()} disabled={save.isPending}>Save</Button>
      {save.isSuccess && <span className="ml-2 text-sm text-up">Saved.</span>}
      <ErrorBox error={save.error} />
      <p className="text-xs text-muted mt-2">Scheduled jobs outside operating hours are skipped (recorded on the schedule). Budgets are hard stops: the agent falls back to deterministic mode when the AI budget is exhausted.</p>
    </Card>
  );
}

function Memory() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const mem = useQuery({ queryKey: ["memory", q, kind], queryFn: () => api.get(`/api/agent/memory?${new URLSearchParams({ ...(q && { q }), ...(kind && { kind }) })}`) });
  const [content, setContent] = useState("");
  const [nk, setNk] = useState("preference");
  const add = useMutation({ mutationFn: () => api.post("/api/agent/memory", { kind: nk, content }), onSuccess: () => { setContent(""); qc.invalidateQueries({ queryKey: ["memory"] }); } });
  const del = useMutation({ mutationFn: (id: number) => api.del(`/api/agent/memory/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["memory"] }) });
  return (
    <div className="space-y-4">
      <Card title="Teach the agent" subtitle="Preferences are included in every conversation's context (e.g. 'I avoid tobacco companies', 'My horizon is 10 years').">
        <div className="grid gap-2 md:grid-cols-[160px_1fr_auto] items-end">
          <Select value={nk} onChange={setNk} options={["preference", "fact", "decision", "lesson", "research"].map((v) => ({ value: v, label: v }))} />
          <Input value={content} onChange={(e) => setContent(e.target.value)} placeholder="Memory content" />
          <Button variant="primary" onClick={() => add.mutate()} disabled={!content}>Save</Button>
        </div>
      </Card>
      <Card title="Memory" actions={<div className="flex gap-2"><Input placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} />
        <Select value={kind} onChange={setKind} options={[{ value: "", label: "All" }, ...["research", "decision", "lesson", "preference", "fact"].map((v) => ({ value: v, label: v }))]} /></div>} pad={false}>
        {mem.data?.length ? <ul className="divide-y divide-line">{mem.data.map((m: any) => (
          <li key={m.id} className="p-3 text-sm flex gap-2">
            <Badge>{m.kind}</Badge><div className="flex-1 min-w-0"><p className="whitespace-pre-wrap">{m.content}</p><p className="text-xs text-muted">{dt(m.created_at, true)} {m.ref_type && `· ${m.ref_type}#${m.ref_id}`} · {m.tags.join(", ")}</p></div>
            <button onClick={() => del.mutate(m.id)} className="text-muted hover:text-down self-start" aria-label="Delete"><Trash2 className="h-3.5 w-3.5" /></button>
          </li>))}</ul> : <Empty>No memories yet.</Empty>}
      </Card>
    </div>
  );
}
