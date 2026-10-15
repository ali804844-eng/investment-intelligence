import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Bot, ChevronDown, ChevronRight, Plus, Send, Trash2, User, Wrench } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import Markdown from "../components/Markdown";
import PendingActionCard from "../components/PendingActionCard";
import { Badge, Button, ErrorBox, Notice } from "../components/ui";
import { ago, dt } from "../format";

const SUGGESTIONS = [
  "What is the current market regime and what is driving it?",
  "Run a quality screen and explain the top three candidates",
  "Compare NWND vs LITW on valuation, quality and risk",
  "How is my portfolio performing against its benchmark? Any risk limit breaches?",
  "Value GRPH with a DCF and tell me what would invalidate the thesis",
  "Are there any active swing-trading setups on FABR?",
  "What did you conclude in previous research, and how accurate have your predictions been?",
];

export default function Agent() {
  const qc = useQueryClient();
  const [sid, setSid] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [local, setLocal] = useState<any[]>([]);
  const [pending, setPending] = useState<any[]>([]);
  const end = useRef<HTMLDivElement>(null);
  const sys = useQuery({ queryKey: ["system"], queryFn: () => api.get("/api/settings/system") });
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () => api.get("/api/agent/sessions") });
  const sess = useQuery({ queryKey: ["session", sid], queryFn: () => api.get(`/api/agent/sessions/${sid}`), enabled: !!sid });
  const send = useMutation({
    mutationFn: (msg: string) => api.post("/api/agent/chat", { message: msg, session_id: sid }),
    onMutate: (msg) => setLocal((l) => [...l, { id: "tmp" + Date.now(), role: "user", content: msg }]),
    onSuccess: (r) => {
      setSid(r.session_id);
      setLocal([]);
      setPending((p) => [...p, ...r.pending_actions]);
      qc.invalidateQueries({ queryKey: ["session", r.session_id] });
      qc.invalidateQueries({ queryKey: ["sessions"] });
    },
  });
  const msgs = [...(sess.data?.messages || []), ...local];
  useEffect(() => end.current?.scrollIntoView({ behavior: "smooth" }), [msgs.length, send.isPending]);
  const submit = () => {
    const t = text.trim();
    if (!t || send.isPending) return;
    setText("");
    send.mutate(t);
  };
  const del = async (id: number) => {
    await api.del(`/api/agent/sessions/${id}`);
    if (id === sid) setSid(null);
    qc.invalidateQueries({ queryKey: ["sessions"] });
  };
  return (
    <div className="grid gap-4 lg:grid-cols-[240px_1fr] h-[calc(100vh-8.5rem)] min-h-[520px]">
      <aside className="hidden lg:flex flex-col rounded-xl border border-line bg-panel min-h-0">
        <div className="p-2 border-b border-line">
          <Button className="w-full" onClick={() => { setSid(null); setLocal([]); setPending([]); }}><Plus className="h-4 w-4" />New conversation</Button>
        </div>
        <div className="flex-1 overflow-y-auto p-1">
          {sessions.data?.map((s: any) => (
            <div key={s.id} className={clsx("group flex items-center rounded-lg", s.id === sid ? "bg-panel2" : "hover:bg-panel2/60")}>
              <button onClick={() => { setSid(s.id); setPending([]); }} className="flex-1 min-w-0 text-left px-2 py-1.5">
                <div className="truncate text-sm">{s.title}</div>
                <div className="text-[0.65rem] text-muted">{ago(s.updated_at)}</div>
              </button>
              <button onClick={() => del(s.id)} className="opacity-0 group-hover:opacity-100 p-1.5 text-muted hover:text-down" aria-label="Delete conversation"><Trash2 className="h-3.5 w-3.5" /></button>
            </div>
          ))}
        </div>
      </aside>
      <section className="flex flex-col rounded-xl border border-line bg-panel min-h-0">
        <header className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5">
          <Bot className="h-4 w-4 text-accent" /><h1 className="font-semibold text-sm">AI investment agent</h1>
          {sys.data && (sys.data.llm_configured
            ? <Badge tone="up">{sys.data.llm_model}</Badge>
            : <Badge tone="warn" title="Set ANTHROPIC_API_KEY on the server">Deterministic mode (no LLM key)</Badge>)}
          <span className="text-xs text-muted ml-auto">Tool-using agent · calculations run in code · proposals need your approval</span>
        </header>
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {!msgs.length && (
            <div className="max-w-2xl mx-auto space-y-4 pt-4">
              {sys.data && !sys.data.llm_configured && (
                <Notice>The language model is not configured, so the agent answers in <b>deterministic mode</b>: it runs the same research tools and
                  formats their results, without free-form reasoning. Add <code>ANTHROPIC_API_KEY</code> to the server environment to enable conversational analysis.</Notice>
              )}
              <p className="text-sm text-muted">Ask about markets, companies, valuations, your portfolios or past research. Try:</p>
              <div className="grid gap-2 sm:grid-cols-2">
                {SUGGESTIONS.map((s) => <button key={s} onClick={() => send.mutate(s)} className="text-left rounded-lg border border-line bg-panel2 p-3 text-sm hover:border-accent">{s}</button>)}
              </div>
            </div>
          )}
          {msgs.map((m: any) => <Message key={m.id} m={m} />)}
          {send.isPending && <div className="flex gap-3 text-sm text-muted"><Bot className="h-5 w-5 animate-pulse" />Researching — running tools…</div>}
          {pending.map((p) => <div key={p.id} className="max-w-3xl ml-8"><PendingActionCard action={p} /></div>)}
          <ErrorBox error={send.error} />
          <div ref={end} />
        </div>
        <div className="border-t border-line p-3">
          <div className="flex gap-2 items-end">
            <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} placeholder="Ask the agent… (Enter to send, Shift+Enter for newline)"
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }} aria-label="Message"
              className="flex-1 resize-none rounded-lg border border-line bg-panel2 px-3 py-2 text-sm outline-none focus:border-accent" />
            <Button variant="primary" onClick={submit} disabled={send.isPending || !text.trim()} title="Send"><Send className="h-4 w-4" /></Button>
          </div>
        </div>
      </section>
    </div>
  );
}

function Message({ m }: { m: any }) {
  const [open, setOpen] = useState(false);
  const user = m.role === "user";
  return (
    <div className={clsx("flex gap-3", user && "flex-row-reverse")}>
      <div className={clsx("h-7 w-7 shrink-0 rounded-full flex items-center justify-center", user ? "bg-panel2" : "bg-accent/15 text-accent")}>
        {user ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
      </div>
      <div className={clsx("min-w-0 max-w-3xl rounded-xl px-3.5 py-2.5", user ? "bg-panel2" : "border border-line")}>
        {user ? <div className="whitespace-pre-wrap text-sm">{m.content}</div> : <Markdown>{m.content}</Markdown>}
        {!user && m.tool_calls?.length > 0 && (
          <div className="mt-2 border-t border-line pt-2">
            <button onClick={() => setOpen(!open)} className="flex items-center gap-1 text-xs text-muted hover:text-fg">
              {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}<Wrench className="h-3 w-3" />
              {m.tool_calls.length} tool call(s) · {m.engine}
            </button>
            {open && (
              <ul className="mt-1 space-y-1 text-xs">
                {m.tool_calls.map((t: any, i: number) => (
                  <li key={i} className="rounded bg-panel2 px-2 py-1 num">
                    <span className={t.ok ? "text-up" : "text-down"}>{t.ok ? "✓" : "✗"}</span> {t.name}({JSON.stringify(t.input)}){t.error ? ` — ${t.error}` : ""}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        {m.created_at && <div className="mt-1 text-[0.65rem] text-muted">{dt(m.created_at, true)}</div>}
      </div>
    </div>
  );
}
