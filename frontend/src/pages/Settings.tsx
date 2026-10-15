import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Button, Card, ErrorBox, Input, Notice, Select, Spinner, Table, Tabs } from "../components/ui";
import { dt } from "../format";

export default function Settings() {
  const [tab, setTab] = useState("sources");
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Settings</h1>
      <Tabs value={tab} onChange={setTab} tabs={[{ key: "sources", label: "Data sources" }, { key: "risk", label: "Risk limits" }, { key: "profile", label: "Profile & security" }, { key: "system", label: "System" }, { key: "audit", label: "Audit log" }]} />
      {tab === "sources" && <Sources />}
      {tab === "risk" && <RiskLimits />}
      {tab === "profile" && <Profile />}
      {tab === "system" && <System />}
      {tab === "audit" && <Audit />}
    </div>
  );
}

function Sources() {
  const q = useQuery({ queryKey: ["providers"], queryFn: () => api.get("/api/settings/providers") });
  if (q.isLoading) return <Spinner />;
  return (
    <div className="space-y-4">
      <Notice tone="info">API keys are configured as <b>server environment variables</b> (see <code>.env.example</code>) and are never sent to the browser, stored in the database, or written to logs. Restart the API and worker after changing them.</Notice>
      <div className="grid gap-4 lg:grid-cols-2">
        {q.data.providers.map((p: any) => (
          <Card key={p.name} title={p.title} actions={<Badge tone={p.configured ? "up" : "neutral"}>{p.configured ? "configured" : "not configured"}</Badge>}>
            <div className="text-sm space-y-1.5">
              <div><span className="text-muted">Capabilities:</span> {p.capabilities.join(", ")}</div>
              <div><span className="text-muted">Data quality label:</span> {p.quality}{p.daily_limit ? ` · daily call cap ${p.daily_limit}` : ""}</div>
              <div><span className="text-muted">Licensing:</span> {p.licensing}</div>
              <div><span className="text-muted">Setup:</span> {p.setup}</div>
              {p.homepage && <a href={p.homepage} target="_blank" rel="noopener noreferrer" className="text-accent inline-flex items-center gap-1 text-xs">Documentation <ExternalLink className="h-3 w-3" /></a>}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}

const FIELDS: [string, string, "pct" | "num"][] = [
  ["max_position_pct", "Max single position", "pct"], ["max_sector_pct", "Max sector exposure", "pct"], ["max_country_pct", "Max country exposure", "pct"],
  ["max_drawdown_pct", "Max drawdown", "pct"], ["max_annual_vol", "Max annualized volatility", "pct"], ["min_avg_dollar_volume", "Min avg $ volume/day", "num"],
  ["max_pct_of_adv", "Max order as % of ADV", "pct"], ["stale_price_days", "Stale price after (business days)", "num"],
  ["stale_fundamentals_days", "Stale fundamentals after (days)", "num"], ["max_pairwise_corr", "Max pairwise correlation", "num"],
];

function RiskLimits() {
  const pfs = useQuery({ queryKey: ["portfolios"], queryFn: () => api.get("/api/portfolios") });
  const [pid, setPid] = useState("");
  const q = useQuery({ queryKey: ["policy", pid], queryFn: () => api.get(`/api/risk/policy${pid ? `?portfolio_id=${pid}` : ""}`) });
  const [f, setF] = useState<any>(null);
  const qc = useQueryClient();
  useEffect(() => { if (q.data) setF(q.data); }, [q.data]);
  const save = useMutation({ mutationFn: () => api.put("/api/risk/policy", { ...Object.fromEntries(FIELDS.map(([k]) => [k, Number(f[k])])), portfolio_id: pid ? Number(pid) : null }),
    onSuccess: () => qc.invalidateQueries() });
  return (
    <Card title="Risk limits" subtitle="Enforced by the deterministic risk engine for portfolio checks and every pre-trade check. The AI agent has no tool to change them. Changes are audited.">
      <div className="w-72 mb-3"><Select label="Scope" value={pid} onChange={setPid} options={[{ value: "", label: "Default (all portfolios)" }, ...(pfs.data || []).map((p: any) => ({ value: String(p.id), label: `Override: ${p.name}` }))]} /></div>
      {f && (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
          {FIELDS.map(([k, label, kind]) => <Input key={k} label={`${label}${kind === "pct" ? " (0-1)" : ""}`} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} />)}
        </div>
      )}
      <Button className="mt-3" variant="primary" onClick={() => save.mutate()} disabled={save.isPending}>Save limits</Button>
      {save.isSuccess && <span className="ml-2 text-sm text-up">Saved.</span>}
      <ErrorBox error={save.error} />
    </Card>
  );
}

function Profile() {
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get("/api/auth/me") });
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [ccy, setCcy] = useState("");
  const [pw, setPw] = useState({ current_password: "", new_password: "" });
  useEffect(() => { if (me.data) { setName(me.data.display_name); setCcy(me.data.base_currency); } }, [me.data]);
  const save = useMutation({ mutationFn: () => api.put("/api/auth/me", { display_name: name, base_currency: ccy }), onSuccess: () => qc.invalidateQueries({ queryKey: ["me"] }) });
  const chpw = useMutation({ mutationFn: () => api.post("/api/auth/change-password", pw), onSuccess: () => setPw({ current_password: "", new_password: "" }) });
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Profile">
        <div className="space-y-2">
          <Input label="Email" value={me.data?.email || ""} disabled />
          <Input label="Display name" value={name} onChange={(e) => setName(e.target.value)} />
          <Input label="Reporting currency" value={ccy} maxLength={3} onChange={(e) => setCcy(e.target.value.toUpperCase())} />
          <Button variant="primary" onClick={() => save.mutate()}>Save</Button>
          <ErrorBox error={save.error} />
        </div>
      </Card>
      <Card title="Change password">
        <div className="space-y-2">
          <Input label="Current password" type="password" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} />
          <Input label="New password" type="password" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} />
          <Button variant="primary" onClick={() => chpw.mutate()}>Change</Button>
          {chpw.isSuccess && <span className="ml-2 text-sm text-up">Changed.</span>}
          <ErrorBox error={chpw.error} />
        </div>
      </Card>
    </div>
  );
}

function System() {
  const q = useQuery({ queryKey: ["system"], queryFn: () => api.get("/api/settings/system") });
  if (q.isLoading) return <Spinner />;
  const s = q.data;
  return (
    <Card title="System">
      <Table dense head={["Setting", "Value"]} rows={[
        ["Environment", s.environment], ["AI language model", s.llm_configured ? s.llm_model : "not configured (deterministic mode)"],
        ["Email notifications", s.email_configured ? "configured" : "not configured"], ["Sample data", s.sample_data_enabled ? "enabled" : "disabled"],
        ["Live trading", <Badge tone="neutral">disabled — {s.live_trading_note}</Badge>],
        ...s.production_warnings.map((w: string) => ["Production warning", <span className="text-down">{w}</span>]),
      ]} />
    </Card>
  );
}

function Audit() {
  const q = useQuery({ queryKey: ["audit"], queryFn: () => api.get("/api/audit?limit=500") });
  return (
    <Card title="Audit log" subtitle="Material actions by you, the agent and the worker." pad={false}>
      {q.isLoading ? <Spinner /> : <Table dense head={["When", "Actor", "Action", "Entity", "Details", "IP"]} rows={(q.data || []).map((a: any) => [
        dt(a.created_at, true), <Badge tone={a.actor === "agent" ? "accent" : "neutral"}>{a.actor}</Badge>, a.action, `${a.entity_type || ""} ${a.entity_id || ""}`,
        <span className="text-xs num truncate max-w-[420px] inline-block align-bottom" title={JSON.stringify(a.details)}>{JSON.stringify(a.details)}</span>, a.ip || "",
      ])} />}
    </Card>
  );
}
