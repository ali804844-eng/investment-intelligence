import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  Activity, Bell, Bot, BriefcaseBusiness, ChartCandlestick, FlaskConical, Gauge, Globe, LineChart, LogOut, Menu, Moon, Radar,
  ScrollText, Search, Settings as SettingsIcon, Sun, Telescope, X,
} from "lucide-react";
import { lazy, ReactNode, Suspense, useEffect, useRef, useState } from "react";
import { Link, NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api } from "./api";
import { QualityBadge, Spinner, useLocal } from "./components/ui";
import { ago } from "./format";
import Login from "./pages/Login";

const Dashboard = lazy(() => import("./pages/Dashboard"));
const Agent = lazy(() => import("./pages/Agent"));
const Markets = lazy(() => import("./pages/Markets"));
const Discover = lazy(() => import("./pages/Discover"));
const Trading = lazy(() => import("./pages/Trading"));
const SecurityPage = lazy(() => import("./pages/Security"));
const Portfolios = lazy(() => import("./pages/Portfolios"));
const PortfolioDetail = lazy(() => import("./pages/PortfolioDetail"));
const Reports = lazy(() => import("./pages/Reports"));
const ReportView = lazy(() => import("./pages/ReportView"));
const Lab = lazy(() => import("./pages/Lab"));
const Learning = lazy(() => import("./pages/Learning"));
const Alerts = lazy(() => import("./pages/Alerts"));
const Control = lazy(() => import("./pages/Control"));
const Settings = lazy(() => import("./pages/Settings"));

const NAV = [
  { to: "/", label: "Dashboard", icon: Gauge, end: true },
  { to: "/agent", label: "AI Agent", icon: Bot },
  { to: "/markets", label: "Markets", icon: Globe },
  { to: "/discover", label: "Discovery", icon: Radar },
  { to: "/trading", label: "Short-term", icon: ChartCandlestick },
  { to: "/research", label: "Research", icon: Telescope },
  { to: "/portfolios", label: "Portfolios", icon: BriefcaseBusiness },
  { to: "/lab", label: "Financial Lab", icon: FlaskConical },
  { to: "/learning", label: "Learning Lab", icon: LineChart },
  { to: "/alerts", label: "Alerts", icon: Bell },
  { to: "/control", label: "Agent Control", icon: Activity },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

export default function App() {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get("/api/auth/me"), retry: false });
  useEffect(() => {
    const f = () => qc.setQueryData(["me"], null);
    window.addEventListener("ii:unauthorized", f);
    return () => window.removeEventListener("ii:unauthorized", f);
  }, [qc]);
  if (me.isLoading) return <Spinner label="Starting…" />;
  if (!me.data) return <Login onDone={() => { qc.clear(); me.refetch(); }} />;
  return (
    <Shell user={me.data}>
      <Suspense fallback={<Spinner />}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/agent" element={<Agent />} />
          <Route path="/markets" element={<Markets />} />
          <Route path="/discover" element={<Discover />} />
          <Route path="/trading" element={<Trading />} />
          <Route path="/research" element={<Reports />} />
          <Route path="/research/reports/:id" element={<ReportView />} />
          <Route path="/research/:symbol" element={<SecurityPage />} />
          <Route path="/portfolios" element={<Portfolios />} />
          <Route path="/portfolios/:id" element={<PortfolioDetail />} />
          <Route path="/lab" element={<Lab />} />
          <Route path="/learning" element={<Learning />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/control" element={<Control />} />
          <Route path="/agent-control" element={<Navigate to="/control" />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<div className="p-6 text-muted">Page not found. <Link className="text-accent" to="/">Go to dashboard</Link></div>} />
        </Routes>
      </Suspense>
    </Shell>
  );
}

function Shell({ user, children }: { user: any; children: ReactNode }) {
  const [theme, setTheme] = useLocal<string>("ii-theme", "dark");
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  const nav = useNavigate();
  const qc = useQueryClient();
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  useEffect(() => setOpen(false), [loc.pathname]);
  const notif = useQuery({ queryKey: ["notif-count"], queryFn: () => api.get("/api/notifications?unread_only=true&limit=5"), refetchInterval: 60_000 });
  const health = useQuery({ queryKey: ["health"], queryFn: () => api.get("/api/health"), refetchInterval: 60_000 });
  const logout = async () => { await api.post("/api/auth/logout"); qc.clear(); window.location.href = "/"; };
  return (
    <div className="flex h-full">
      <aside className={clsx("fixed inset-y-0 left-0 z-40 w-60 shrink-0 border-r border-line bg-panel transition-transform lg:static lg:translate-x-0",
        open ? "translate-x-0" : "-translate-x-full")}>
        <div className="flex items-center justify-between px-4 h-14 border-b border-line">
          <Link to="/" className="flex items-center gap-2 font-semibold tracking-tight">
            <svg viewBox="0 0 32 32" className="h-6 w-6"><rect width="32" height="32" rx="7" fill="var(--panel2)" /><path d="M6 22l6-7 5 4 9-11" stroke="var(--accent)" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
            <span className="text-sm leading-tight">Investment<br /><span className="text-muted font-normal">Intelligence</span></span>
          </Link>
          <button className="lg:hidden text-muted" onClick={() => setOpen(false)} aria-label="Close menu"><X className="h-5 w-5" /></button>
        </div>
        <nav className="p-2 space-y-0.5 overflow-y-auto h-[calc(100%-7.5rem)]">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}
              className={({ isActive }) => clsx("flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition",
                isActive ? "bg-panel2 text-fg font-medium" : "text-muted hover:text-fg hover:bg-panel2/60")}>
              <n.icon className="h-4 w-4" />{n.label}
            </NavLink>
          ))}
        </nav>
        <div className="absolute bottom-0 inset-x-0 border-t border-line p-3 text-xs text-muted">
          <div className="flex items-center gap-1.5" title="Background research worker heartbeat">
            <span className={clsx("h-2 w-2 rounded-full", health.data?.worker_alive ? "bg-up" : "bg-down")} />
            Agent worker {health.data?.worker_alive ? "running" : "offline"}
          </div>
          <div className="mt-0.5">last seen {ago(health.data?.worker_last_seen)}</div>
        </div>
      </aside>
      {open && <div className="fixed inset-0 z-30 bg-black/40 lg:hidden" onClick={() => setOpen(false)} />}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-line bg-panel/90 px-3 backdrop-blur">
          <button className="lg:hidden text-muted" onClick={() => setOpen(true)} aria-label="Open menu"><Menu className="h-5 w-5" /></button>
          <GlobalSearch onPick={(s) => nav(`/research/${s}`)} />
          <div className="ml-auto flex items-center gap-1">
            <Link to="/alerts" className="relative rounded-lg p-2 text-muted hover:text-fg hover:bg-panel2" aria-label="Notifications">
              <Bell className="h-4 w-4" />
              {!!notif.data?.unread && <span className="absolute -top-0.5 -right-0.5 min-w-4 rounded-full bg-down px-1 text-[0.6rem] font-bold text-white">{notif.data.unread}</span>}
            </Link>
            <button onClick={() => setTheme(theme === "dark" ? "light" : "dark")} className="rounded-lg p-2 text-muted hover:text-fg hover:bg-panel2" aria-label="Toggle theme">
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
            <span className="hidden sm:inline text-xs text-muted px-2">{user.display_name || user.email}</span>
            <button onClick={logout} className="rounded-lg p-2 text-muted hover:text-fg hover:bg-panel2" aria-label="Log out"><LogOut className="h-4 w-4" /></button>
          </div>
        </header>
        <main className="min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-[1600px] p-3 sm:p-5">{children}</div>
          <footer className="px-5 pb-6 text-[0.7rem] text-muted max-w-[1600px] mx-auto">
            <ScrollText className="inline h-3 w-3 mr-1" />Research and analytics tool. Not investment advice. Outputs are estimates under stated assumptions;
            past or simulated performance does not guarantee future results. Live trading is not available.
          </footer>
        </main>
      </div>
    </div>
  );
}

function GlobalSearch({ onPick }: { onPick: (s: string) => void }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const res = useQuery({ queryKey: ["search", q], queryFn: () => api.get(`/api/securities/search?q=${encodeURIComponent(q)}&remote=false`), enabled: q.length >= 1 });
  useEffect(() => {
    const f = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", f);
    return () => document.removeEventListener("mousedown", f);
  }, []);
  return (
    <div ref={box} className="relative w-full max-w-md">
      <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted" />
      <input value={q} onChange={(e) => { setQ(e.target.value); setOpen(true); }} onFocus={() => setOpen(true)}
        onKeyDown={(e) => { if (e.key === "Enter" && res.data?.results?.[0]) { onPick(res.data.results[0].symbol); setOpen(false); setQ(""); } }}
        placeholder="Search symbol or company…" aria-label="Search securities"
        className="w-full rounded-lg border border-line bg-panel2 pl-8 pr-3 py-1.5 text-sm outline-none focus:border-accent" />
      {open && q && (
        <div className="absolute mt-1 w-full rounded-lg border border-line bg-panel shadow-xl max-h-80 overflow-y-auto z-50">
          {(res.data?.results || []).map((r: any) => (
            <button key={r.symbol + r.exchange} onClick={() => { onPick(r.symbol); setOpen(false); setQ(""); }}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-panel2">
              <span className="num font-semibold w-20">{r.symbol}</span>
              <span className="truncate text-muted flex-1">{r.name}</span>
              {r.is_sample && <QualityBadge q="sample" />}
            </button>
          ))}
          {res.data && !res.data.results.length && <div className="px-3 py-2 text-sm text-muted">No match in database. Add real securities in Markets → Add security.</div>}
        </div>
      )}
    </div>
  );
}
