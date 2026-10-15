"""Deterministic fallback agent used when no LLM is configured (or the AI budget is exhausted).

It maps common requests to the same tools the LLM uses and renders the results as Markdown. It does not
reason or write prose beyond templated summaries, and says so.
"""
from __future__ import annotations

import re

from sqlalchemy import select

from app.agent.tools import AgentContext, execute_tool
from app.core.screening.strategies import STRATEGIES
from app.models import Security

BANNER = ("> **Deterministic mode** — the AI language model is not configured or its budget is exhausted, so this answer "
          "was assembled from tool outputs without free-form reasoning. Set `ANTHROPIC_API_KEY` to enable conversational analysis.\n\n")


def _symbols(ctx: AgentContext, text: str) -> list[str]:
    cands = set(re.findall(r"\b[A-Z][A-Z0-9.\-]{0,9}\b", text))
    if not cands:
        cands = {t.upper() for t in re.findall(r"\b[a-zA-Z]{2,6}\b", text)}
    known = set(ctx.db.scalars(select(Security.symbol).where(Security.symbol.in_(list(cands)))).all()) if cands else set()
    # keep the user's order
    out = []
    for tok in re.findall(r"[A-Za-z0-9.\-]+", text):
        t = tok.upper()
        if t in known and t not in out:
            out.append(t)
    return out


def _pct(v):
    return "n/a" if v is None else f"{v:.1%}"


def _num(v, nd=2):
    return "n/a" if v is None else f"{v:,.{nd}f}"


def respond(ctx: AgentContext, text: str) -> dict:
    t = text.lower()
    syms = _symbols(ctx, text)
    calls = []

    def call(name, **args):
        out = execute_tool(ctx, name, args)
        calls.append({"name": name, "input": args, "ok": out["ok"], "error": out.get("error")})
        return out

    md = BANNER
    strat = next((k for k in STRATEGIES if k in t), None)
    if ("compare" in t or " vs" in t or "versus" in t) and len(syms) >= 2:
        out = call("compare_securities", symbols=syms[:6])
        if out["ok"]:
            rows = out["result"]["comparison"]
            keys = ["price", "pe", "ev_ebitda", "fcf_yield", "revenue_cagr_3y", "operating_margin", "roic_avg",
                    "net_debt_to_ebitda", "mom_12_1", "vol_252d"]
            md += "| Metric | " + " | ".join(rows) + " |\n|---|" + "---|" * len(rows) + "\n"
            for k in keys:
                fmt = _num if k in ("price", "pe", "ev_ebitda", "net_debt_to_ebitda") else _pct
                md += f"| {k} | " + " | ".join(fmt(rows[s].get(k)) for s in rows) + " |\n"
            if any(r.get("is_sample") for r in rows.values()):
                md += "\n_Includes SAMPLE (synthetic) instruments._\n"
    elif strat or "screen" in t or "opportunit" in t or "idea" in t:
        out = call("run_screen", strategy=strat or "quality")
        if out["ok"]:
            r = out["result"]
            md += f"**{r['strategy']['title']}** (v{r['strategy']['version']}, {r['universe']} universe, as of {r['as_of']}): " \
                  f"{r['n_candidates']} candidates from {r['n_evaluated']} evaluated.\n\n"
            for c in r["candidates"]:
                md += f"{c['rank']}. **{c['symbol']}** {c['name']} — score {c['score']:+.2f}. {c['why_selected']}\n"
                if c["risks"]:
                    md += "   - Risks: " + "; ".join(x["message"] for x in c["risks"][:3]) + "\n"
            md += "\n_A screen ranking indicates what to research first; it is not a forecast._\n"
    elif "regime" in t or ("market" in t and not syms):
        out = call("market_regime")
        if out["ok"]:
            r = out["result"]
            md += f"**Market regime: {r['label']}** ({(r.get('benchmark') or {}).get('symbol')}, {r.get('data_quality')})\n\n"
            md += "\n".join(f"- {e}" for e in r["explanation"]) + "\n\n"
            for i in r["indicators"]:
                md += f"- {i['name']}: {_pct(i['value']) if i['fmt'] == 'pct' else _num(i['value'])} ({i['source']})\n"
    elif "risk" in t and ("portfolio" in t or not syms):
        out = call("get_portfolio_risk")
        if out["ok"]:
            r = out["result"]
            md += f"**Risk report** ({r['engine']})\n\n"
            md += f"- Breaches: {len(r['breaches'])}, warnings: {len(r['warnings'])}\n"
            for c in r["checks"][:10]:
                md += f"  - [{c['status']}] {c['message']}\n"
            cc = r["concentration"]
            md += f"- Effective number of positions: {_num(cc.get('effective_n'), 1)}; top weight {_pct(cc.get('top_weight'))}\n"
    elif "portfolio" in t or "holdings" in t or "performance" in t:
        out = call("get_portfolio")
        perf = call("get_portfolio_performance")
        if out["ok"]:
            s = out["result"]
            md += f"**{s['name']}** ({'hypothetical' if s['is_hypothetical'] else 'actual'}, data: {s['data_quality']})\n\n"
            md += f"- Total value {_num(s['total_value'])} {s['base_currency']}, cash {_num(s['cash'])}, unrealized P&L {_num(s['unrealized_pnl'])}\n"
            for p in s["positions"][:10]:
                md += f"  - {p['symbol']}: {_pct(p['weight'])} weight, unrealized {_pct(p['unrealized_pct'])}\n"
        if perf["ok"]:
            m = perf["result"]["metrics"]
            md += f"- TWR since inception {_pct(m.get('twr_total'))}, MWR (annualized) {_pct(m.get('mwr_annualized'))}, max drawdown {_pct(m.get('max_drawdown'))}\n"
    elif any(w in t for w in ("predict", "track record", "scorecard", "calibration")):
        out = call("get_prediction_record")
        if out["ok"]:
            sc = out["result"]["scorecard"]["overall"]
            md += f"**Prediction record:** {sc.get('n', 0)} evaluated, hit rate {_pct(sc.get('hit_rate'))}, Brier {_num(sc.get('brier'), 3)} (0.25 = coin flip).\n"
    elif any(w in t for w in ("remember", "earlier", "previous", "last time", "why did")):
        out = call("search_memory", query=text, **({"symbol": syms[0]} if syms else {}))
        reps = call("list_reports", **({"symbol": syms[0]} if syms else {}))
        if out["ok"]:
            for m in out["result"]["memories"][:8]:
                md += f"- ({m['kind']}, {m['created_at'][:10]}) {m['content'][:300]}\n"
        if reps["ok"]:
            for r in reps["result"]["reports"][:5]:
                md += f"- Report #{r['id']} {r['title']} ({r['created_at'][:10]}): {r['summary'][:200] if r['summary'] else ''}\n"
        if md == BANNER:
            md += "No matching memory or reports found."
    elif syms and any(w in t for w in ("value", "valuation", "dcf", "intrinsic", "worth")):
        out = call("run_valuation", symbol=syms[0])
        if out["ok"]:
            v = out["result"]
            if not v.get("available"):
                md += "Valuation unavailable: " + "; ".join(v.get("reasons", []))
            else:
                md += f"**{syms[0]} DCF** (price {_num(v['price'])} {v['currency']}, {v['price_quality']})\n\n"
                md += f"- Base value/share: **{_num(v['dcf']['value_per_share'])}**; scenarios: " + ", ".join(
                    f"{k} {_num(x['value_per_share'])}" for k, x in v["scenarios"]["scenarios"].items()) + "\n"
                if v.get("margin_of_safety"):
                    md += f"- Margin of safety: {_pct(v['margin_of_safety']['margin_of_safety'])}\n"
                md += "- Assumptions:\n" + "".join(f"  - {a['name']}: {a['value']:.4g} — {a['basis']}\n" for a in v["assumptions"])
                md += "\n" + "\n".join(f"_{x}_" for x in v["limitations"])
    elif syms and any(w in t for w in ("report", "research", "analy", "deep dive")):
        out = call("generate_research_report", symbol=syms[0], horizon="short" if "short" in t else "long")
        if out["ok"]:
            r = out["result"]
            md += f"Created report **#{r['report_id']} — {r['title']}**.\n\n{r['summary']}\n\n[Open report]({r['link']})"
    elif syms and any(w in t for w in ("setup", "swing", "short-term", "short term", "trade", "technical", "entry")):
        out = call("get_short_term_setups", symbol=syms[0])
        if out["ok"]:
            r = out["result"]
            if not r["setups"]:
                md += f"No active short-term setup on {syms[0]} right now. (No trade is generated to fill the list.)\n"
            for s in r["setups"]:
                md += (f"**{s['title']}** — {s['hypothesis']}\n- Entry ref {_num(s['entry_reference'])}, stop {_num(s['invalidation']['stop'])}, "
                       f"R/R {s['risk_reward']['ratio']:.1f}\n- History: n={s['historical'].get('n')}, win rate {_pct(s['historical'].get('win_rate'))} "
                       f"— {s['evidence_label']}\n")
    elif syms:
        out = call("get_security_overview", symbol=syms[0])
        if out["ok"]:
            o = out["result"]
            s, q, f = o["security"], o["quote"], o["features"]
            md += f"**{s['name']} ({s['symbol']})** — {s.get('sector') or s['asset_class']}{' — SAMPLE DATA' if s['is_sample'] else ''}\n\n"
            md += f"- Price {_num(q.get('price'))} {s['currency']} as of {q.get('as_of')} ({q.get('quality')}, {q['freshness']['status']})\n"
            for k in ("pe", "fcf_yield", "revenue_cagr_3y", "operating_margin", "roic_avg", "net_debt_to_ebitda", "mom_12_1", "vol_252d"):
                if k in f:
                    md += f"- {k}: {_num(f[k]) if k in ('pe', 'net_debt_to_ebitda') else _pct(f[k])}\n"
            if o["risk_flags"]:
                md += "\n**Risk flags:**\n" + "".join(f"- [{x['severity']}] {x['message']}\n" for x in o["risk_flags"])
            inj = [n for n in o["news"] if n["summary"]["suspected_injection"] or n["title"]["suspected_injection"]]
            if inj:
                md += "\n⚠️ A news item contains instruction-like text (possible prompt injection); it was treated as data and ignored.\n"
    else:
        md += ("I can: analyse a symbol (e.g. `NWND`), value it (`value NWND`), compare (`compare NWND vs LITW`), screen "
               "(`quality screen`, `value ideas`), show portfolio performance or risk, the market regime, short-term setups "
               "(`swing setups FABR`), write a report (`report on WWIM`), or review the prediction record.")
    return {"text": md, "tool_calls": calls, "engine": "deterministic"}
