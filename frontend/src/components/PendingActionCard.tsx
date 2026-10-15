import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ShieldAlert, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { api } from "../api";
import { Badge, Button, ErrorBox } from "./ui";

export default function PendingActionCard({ action }: { action: any }) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [state, setState] = useState(action);
  const m = useMutation({
    mutationFn: (decision: "approve" | "reject") => api.post(`/api/agent/pending/${state.id}`, { decision, confirm: decision === "approve" }),
    onSuccess: (r) => { setState(r); setConfirming(false); qc.invalidateQueries(); },
  });
  const p = state.payload;
  const title = state.action_type === "paper_trade"
    ? `Paper trade: ${p.side.toUpperCase()} ${p.quantity} ${p.symbol} in "${p.portfolio}"`
    : `${state.action_type === "watchlist_add" ? "Add" : "Remove"} ${p.symbol} ${state.action_type === "watchlist_add" ? "to" : "from"} watchlist "${p.watchlist}"`;
  const risk = state.risk_check;
  return (
    <div className="rounded-lg border border-line bg-panel2 p-3 text-sm space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <b>{title}</b>
        <Badge tone={state.status === "pending" ? "warn" : state.status === "executed" ? "up" : "down"}>{state.status}</Badge>
      </div>
      {state.rationale && <p className="text-muted">Agent rationale: {state.rationale}</p>}
      {risk && (
        <div className="space-y-1">
          <div className="flex items-center gap-1.5">
            {risk.approved ? <ShieldCheck className="h-4 w-4 text-up" /> : <ShieldAlert className="h-4 w-4 text-down" />}
            <span>Independent risk check: {risk.summary}</span>
          </div>
          <ul className="text-xs text-muted pl-5 list-disc">
            {risk.checks.filter((c: any) => c.status !== "pass").map((c: any, i: number) => <li key={i}>[{c.status}] {c.message}</li>)}
          </ul>
        </div>
      )}
      {state.result && <div className="text-xs text-muted">Result: {state.result.ok ? `executed${state.result.transaction_id ? ` (transaction #${state.result.transaction_id} at ${state.result.fill_price})` : ""}` : state.result.reason}</div>}
      {state.status === "pending" && (
        confirming ? (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs">Confirm this {state.action_type === "paper_trade" ? "hypothetical paper trade (no real order is sent)" : "watchlist change"}?</span>
            <Button size="sm" variant="primary" onClick={() => m.mutate("approve")} disabled={m.isPending}>Yes, approve</Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>Cancel</Button>
          </div>
        ) : (
          <div className="flex gap-2">
            <Button size="sm" variant="primary" onClick={() => setConfirming(true)}>Approve…</Button>
            <Button size="sm" variant="danger" onClick={() => m.mutate("reject")} disabled={m.isPending}>Reject</Button>
          </div>
        )
      )}
      <ErrorBox error={m.error} />
    </div>
  );
}
