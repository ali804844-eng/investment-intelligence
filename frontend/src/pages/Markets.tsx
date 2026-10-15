import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Spark, TimeSeriesChart } from "../components/charts";
import { Button, Card, ErrorBox, Input, Notice, QualityBadge, Select, Spinner, Table, Tabs } from "../components/ui";
import { dt, money, num, pct, signClass } from "../format";

const GROUP_LABEL: Record<string, string> = { index: "Indices", etf: "ETFs", bond: "Fixed income", commodity: "Commodities", fx: "Currencies", crypto: "Digital assets (research only)" };

export default function Markets() {
  const [tab, setTab] = useState("overview");
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Global market intelligence</h1>
        <p className="text-sm text-muted">Coverage is limited to configured data sources. Every quote shows its source, timestamp and quality.</p>
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[{ key: "overview", label: "Overview" }, { key: "explorer", label: "Securities explorer" }, { key: "macro", label: "Macro & rates" }, { key: "add", label: "Add security" }]} />
      {tab === "overview" && <Overview />}
      {tab === "explorer" && <Explorer />}
      {tab === "macro" && <Macro />}
      {tab === "add" && <AddSecurity />}
    </div>
  );
}

function Overview() {
  const q = useQuery({ queryKey: ["market-overview"], queryFn: () => api.get("/api/market/overview") });
  if (q.isLoading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const groups = q.data.groups;
  return (
    <div className="space-y-4">
      <Card title={`Regime: ${q.data.regime.label}`} subtitle={q.data.regime.explanation?.join(" ")}>
        <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
          {q.data.regime.indicators?.map((i: any) => <div key={i.name}><span className="text-muted">{i.name}: </span><span className="num">{i.fmt === "pct" ? pct(i.value, 2) : num(i.value, 1)}</span></div>)}
        </div>
      </Card>
      <div className="grid gap-4 xl:grid-cols-2">
        {Object.entries(groups).map(([k, rows]: [string, any]) => (
          <Card key={k} title={GROUP_LABEL[k] || k} pad={false}>
            <Table dense head={["Instrument", "Last", "1D", "YTD", "1Y", "60D", "Data"]} rows={rows.map((r: any) => [
              <Link to={`/research/${r.symbol}`} className="hover:text-accent"><span className="num font-semibold">{r.symbol}</span> <span className="text-muted text-xs hidden sm:inline">{r.name}</span></Link>,
              <span className="num">{num(r.price, r.asset_class === "fx" ? 4 : 2)}</span>,
              <span className={`num ${signClass(r.change_pct)}`}>{pct(r.change_pct, 2, true)}</span>,
              <span className={`num ${signClass(r.ret_ytd)}`}>{pct(r.ret_ytd, 1, true)}</span>,
              <span className={`num ${signClass(r.ret_1y)}`}>{pct(r.ret_1y, 1, true)}</span>,
              <Spark values={r.spark} />,
              <span title={`${r.source || "no source"} · as of ${dt(r.as_of, true)}`}><QualityBadge q={r.quality} fresh={r.freshness} /></span>,
            ])} />
          </Card>
        ))}
      </div>
      <p className="text-xs text-muted">{q.data.notes.join(" ")}</p>
    </div>
  );
}

function Explorer() {
  const [f, setF] = useState({ asset_class: "", country: "", sector: "", currency: "", q: "", min_mcap_usd: "", min_adv_usd: "" });
  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v) as [string, string][]).toString();
  const q = useQuery({ queryKey: ["explorer", qs], queryFn: () => api.get(`/api/market/securities?${qs}`) });
  const facets = q.data?.facets;
  const opt = (arr: string[] = [], all = "All") => [{ value: "", label: all }, ...arr.map((v) => ({ value: v, label: v }))];
  return (
    <Card title="Securities explorer" subtitle={q.data && `${q.data.rows.length} instruments · features as of ${q.data.as_of}`}>
      <div className="grid gap-2 grid-cols-2 md:grid-cols-4 xl:grid-cols-7 mb-3">
        <Input placeholder="Symbol or name" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} />
        <Select value={f.asset_class} onChange={(v) => setF({ ...f, asset_class: v })} options={opt(facets?.asset_class, "All asset classes")} />
        <Select value={f.country} onChange={(v) => setF({ ...f, country: v })} options={opt(facets?.country, "All countries")} />
        <Select value={f.sector} onChange={(v) => setF({ ...f, sector: v })} options={opt(facets?.sector, "All sectors")} />
        <Select value={f.currency} onChange={(v) => setF({ ...f, currency: v })} options={opt(facets?.currency, "All currencies")} />
        <Input placeholder="Min mkt cap USD" inputMode="numeric" value={f.min_mcap_usd} onChange={(e) => setF({ ...f, min_mcap_usd: e.target.value.replace(/[^0-9.]/g, "") })} />
        <Input placeholder="Min $ volume/day" inputMode="numeric" value={f.min_adv_usd} onChange={(e) => setF({ ...f, min_adv_usd: e.target.value.replace(/[^0-9.]/g, "") })} />
      </div>
      {q.isLoading ? <Spinner /> : q.error ? <ErrorBox error={q.error} /> : (
        <Table dense head={["Symbol", "Name", "Class", "Country", "Sector", "Ccy", "Price", "1D", "Mkt cap", "P/E", "Div yld", "Mom 12-1", "Vol 1y", "$ADV", "Data"]}
          rows={q.data.rows.map((r: any) => [
            <Link to={`/research/${r.symbol}`} className="num font-semibold hover:text-accent">{r.symbol}</Link>,
            <span className="truncate max-w-[200px] inline-block align-bottom">{r.name}</span>, r.asset_class, r.country || "—", r.sector || "—", r.currency,
            <span className="num">{num(r.price)}</span>, <span className={`num ${signClass(r.change_pct)}`}>{pct(r.change_pct, 2, true)}</span>,
            <span className="num">{money(r.market_cap_usd, undefined, 0)}</span>, <span className="num">{num(r.pe, 1)}</span>,
            <span className="num">{pct(r.dividend_yield)}</span>, <span className={`num ${signClass(r.mom_12_1)}`}>{pct(r.mom_12_1)}</span>,
            <span className="num">{pct(r.vol_252d)}</span>, <span className="num">{money(r.adv_usd_20d, undefined, 0)}</span>,
            <QualityBadge q={r.quality} />,
          ])} />
      )}
    </Card>
  );
}

function Macro() {
  const q = useQuery({ queryKey: ["macro"], queryFn: () => api.get("/api/market/macro") });
  if (q.isLoading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {q.data.series.map((s: any) => (
        <Card key={s.series_id} title={s.series_id} subtitle={s.latest ? <span>Latest {num(s.latest.value, 2)} on {s.latest.date} · {s.latest.source} <QualityBadge q={s.quality} /></span> : "No data"}>
          {s.history.length ? <TimeSeriesChart height={160} yFormat={(v) => num(v, 1)} data={s.history.map(([date, v]: [string, number]) => ({ date, v }))} series={[{ key: "v", label: s.series_id }]} />
            : <div className="text-sm text-muted">Unavailable. Configure FRED_API_KEY for real macro data.</div>}
        </Card>
      ))}
    </div>
  );
}

function AddSecurity() {
  const [sym, setSym] = useState("");
  const [query, setQuery] = useState("");
  const search = useQuery({ queryKey: ["remote-search", query], queryFn: () => api.get(`/api/securities/search?q=${encodeURIComponent(query)}&remote=true`), enabled: !!query });
  const add = useMutation({ mutationFn: (s: string) => api.post("/api/securities", { symbol: s }) });
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Add a real security">
        <div className="space-y-3">
          <Notice tone="info">Real instruments are resolved through configured reference-data providers (SEC EDGAR for US filers, Tiingo for prices). Without them, adding fails with an explanation rather than inventing data.</Notice>
          <div className="flex gap-2">
            <Input placeholder="Ticker, e.g. AAPL" value={sym} onChange={(e) => setSym(e.target.value.toUpperCase())} />
            <Button variant="primary" disabled={!sym || add.isPending} onClick={() => add.mutate(sym)}>{add.isPending ? "Adding…" : "Add"}</Button>
          </div>
          <ErrorBox error={add.error} />
          {add.data && (
            <div className="text-sm space-y-1">
              <div>Added <Link className="text-accent" to={`/research/${add.data.security.symbol}`}>{add.data.security.symbol} — {add.data.security.name}</Link></div>
              <div className="text-muted">Prices: {add.data.prices?.status} {add.data.prices?.error || `(${add.data.prices?.inserted ?? 0} bars from ${add.data.prices?.source})`}</div>
              <div className="text-muted">Fundamentals: {add.data.fundamentals?.status} {add.data.fundamentals?.error || `(${add.data.fundamentals?.inserted ?? 0} facts from ${add.data.fundamentals?.source})`}</div>
            </div>
          )}
        </div>
      </Card>
      <Card title="Search providers">
        <div className="space-y-2">
          <Input placeholder="Company name or ticker" onKeyDown={(e) => e.key === "Enter" && setQuery((e.target as HTMLInputElement).value)} />
          {search.isFetching && <Spinner />}
          {search.data && (
            <Table dense head={["Symbol", "Name", "In DB", "Source"]} rows={search.data.results.map((r: any) => [
              <span className="num font-semibold">{r.symbol}</span>, r.name, r.in_database ? "yes" : <Button size="sm" onClick={() => add.mutate(r.symbol)}>Add</Button>, r.source || (r.is_sample ? <QualityBadge q="sample" /> : "db"),
            ])} />
          )}
        </div>
      </Card>
    </div>
  );
}
