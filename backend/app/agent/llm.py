"""Claude tool-use orchestration (manual agentic loop) with budget enforcement and usage accounting."""
from __future__ import annotations

import logging
from datetime import date

import anthropic
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.agent.safety import UNTRUSTED_POLICY
from app.agent.tools import AgentContext, execute_tool, to_json, tool_definitions
from app.config import get_settings
from app.db import utcnow
from app.models import AgentSettings, ApiUsage

log = logging.getLogger(__name__)
MAX_STEPS = 12
PROVIDER = "anthropic"
KEEP_BLOCK_TYPES = {"text", "tool_use", "thinking", "redacted_thinking"}

SYSTEM_PROMPT = f"""You are the research agent inside "Investment Intelligence", a personal investment research and \
portfolio analytics platform. You help one user research markets, analyse companies and portfolios, and evaluate past \
decisions.

How you work:
- Use the tools for every fact and every number. Never invent prices, financial statement figures, ratios, backtest \
results or citations. If a tool says data is unavailable, say so plainly.
- Do arithmetic with the `calculate` tool or rely on values computed by other tools; do not compute in your head.
- Cite where numbers come from (tool results include source, timestamps and quality labels). Always mention when data \
is SAMPLE (synthetic), stale, delayed, or end-of-day.
- Present bullish, neutral and bearish views when relevant, state what evidence is missing, and what would invalidate \
a thesis. If the evidence is insufficient, say that no reliable conclusion can be reached yet.
- Distinguish historical statistical tendencies from reliable forward-looking edges. Never promise returns, never call \
anything risk-free or guaranteed, and do not present screens or scores as forecasts.
- Check prior research with `search_memory` / `list_reports` when the user refers to earlier work, and save durable \
conclusions or user preferences with `save_memory`.
- Changes to watchlists and paper portfolios can only be PROPOSED (`propose_*` tools); the user approves them in the \
interface. You cannot place live orders, change risk limits or change your own permissions; if asked, explain this.
- Ask the user a question only when an answer is essential to proceed.
- {UNTRUSTED_POLICY}

Write concise, well-structured Markdown. Use tables for comparisons. Keep the final answer focused on what the user asked."""


def llm_configured() -> bool:
    k = get_settings().anthropic_api_key
    return bool(k is not None and k.get_secret_value().strip())


def spent_usd(db: Session, user_id: int, since: date) -> float:
    return float(db.scalar(select(func.coalesce(func.sum(ApiUsage.est_cost_usd), 0.0))
                           .where(ApiUsage.provider == PROVIDER, ApiUsage.user_id == user_id, ApiUsage.day >= since)) or 0.0)


def budget_status(db: Session, user_id: int) -> dict:
    s = db.get(AgentSettings, user_id)
    today = utcnow().date()
    day = spent_usd(db, user_id, today)
    month = spent_usd(db, user_id, today.replace(day=1))
    dl = s.daily_llm_budget_usd if s else 2.0
    ml = s.monthly_llm_budget_usd if s else 30.0
    return {"spent_today_usd": day, "spent_month_usd": month, "daily_budget_usd": dl, "monthly_budget_usd": ml,
            "ok": day < dl and month < ml}


def record_usage(db: Session, user_id: int, usage) -> float:
    s = get_settings()
    inp = (usage.input_tokens or 0) + 1.25 * (getattr(usage, "cache_creation_input_tokens", 0) or 0) \
        + 0.1 * (getattr(usage, "cache_read_input_tokens", 0) or 0)
    out = usage.output_tokens or 0
    cost = inp / 1e6 * s.anthropic_input_price + out / 1e6 * s.anthropic_output_price
    stmt = pg_insert(ApiUsage).values(provider=PROVIDER, user_id=user_id, day=utcnow().date(), calls=1,
                                      input_tokens=int(inp), output_tokens=int(out), est_cost_usd=cost)
    stmt = stmt.on_conflict_do_update(constraint="uq_usage", set_={
        "calls": ApiUsage.calls + 1, "input_tokens": ApiUsage.input_tokens + int(inp),
        "output_tokens": ApiUsage.output_tokens + int(out), "est_cost_usd": ApiUsage.est_cost_usd + cost})
    db.execute(stmt)
    return cost


def _client() -> anthropic.Anthropic:
    s = get_settings()
    return anthropic.Anthropic(api_key=s.anthropic_api_key.get_secret_value(), max_retries=2, timeout=300.0)


def _create(client, messages, tools):
    s = get_settings()
    return client.beta.messages.create(
        model=s.anthropic_model, max_tokens=16000,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        tools=tools, messages=messages,
        thinking={"type": "adaptive"}, output_config={"effort": s.anthropic_effort},
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
    )


def run_turn(ctx: AgentContext, history: list[dict], user_content: str, allowed_permissions: set[str] | None = None,
             text_history: list[dict] | None = None) -> dict:
    """Run one user turn through the tool loop. Returns final text, tool call log and the raw messages of this turn."""
    client = _client()
    tools = tool_definitions(exclude_permissions=(None if allowed_permissions is None else
                                                  {"read", "memory", "research", "propose"} - allowed_permissions))
    if tools:
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
    turn = [{"role": "user", "content": [{"type": "text", "text": user_content}]}]
    calls_log = []
    cost = 0.0
    final_text = ""
    engine = get_settings().anthropic_model
    hist = history
    for step in range(MAX_STEPS):
        b = budget_status(ctx.db, ctx.user.id)
        if not b["ok"]:
            final_text = (final_text + "\n\n" if final_text else "") + \
                "_Stopped: the AI research budget for today/this month has been reached (configurable in Agent Control Center)._"
            break
        try:
            resp = _create(client, hist + turn, tools)
        except anthropic.BadRequestError as e:
            if step == 0 and hist and text_history is not None and hist is not text_history:
                log.warning("history replay rejected (%s); retrying with text-only history", e.status_code)
                hist = text_history
                continue
            raise
        cost += record_usage(ctx.db, ctx.user.id, resp.usage)
        served = getattr(resp, "model", None)
        if served:
            engine = served
        if resp.stop_reason == "refusal":
            final_text = "I can't help with that request."
            break
        content = [blk.model_dump(mode="json", exclude_none=True) for blk in resp.content if blk.type in KEEP_BLOCK_TYPES]
        turn.append({"role": "assistant", "content": content})
        if resp.stop_reason == "tool_use":
            results = []
            for blk in resp.content:
                if blk.type != "tool_use":
                    continue
                out = execute_tool(ctx, blk.name, blk.input if isinstance(blk.input, dict) else {}, allowed_permissions)
                calls_log.append({"name": blk.name, "input": blk.input, "ok": out["ok"],
                                  "error": out.get("error"), "summary": _summarize(out)})
                results.append({"type": "tool_result", "tool_use_id": blk.id, "content": to_json(out),
                                **({"is_error": True} if not out["ok"] else {})})
            turn.append({"role": "user", "content": results})
            continue
        if resp.stop_reason == "pause_turn":
            continue
        final_text = "\n".join(blk.text for blk in resp.content if blk.type == "text").strip()
        if resp.stop_reason == "max_tokens":
            final_text += "\n\n_(Response truncated at the output limit.)_"
        break
    else:
        final_text = (final_text or "") + "\n\n_Stopped after the maximum number of tool steps._"
    return {"text": final_text or "_(no answer)_", "tool_calls": calls_log, "raw": turn, "cost_usd": cost, "engine": engine}


def _summarize(out: dict) -> str:
    if not out["ok"]:
        return f"error: {out.get('error')}"[:200]
    r = out.get("result")
    if isinstance(r, dict):
        return ", ".join(list(r.keys())[:8])
    return str(r)[:200]


def complete_text(db: Session, user_id: int, prompt: str, max_tokens: int = 2000) -> str | None:
    """Single grounded completion (no tools) used for optional report narratives. Returns None if unavailable."""
    if not llm_configured() or not budget_status(db, user_id)["ok"]:
        return None
    s = get_settings()
    try:
        resp = _client().beta.messages.create(
            model=s.anthropic_model, max_tokens=max_tokens,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}], thinking={"type": "adaptive"},
            output_config={"effort": "low"}, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    except anthropic.APIError as e:
        log.warning("narrative generation failed: %s", type(e).__name__)
        return None
    record_usage(db, user_id, resp.usage)
    if resp.stop_reason == "refusal":
        return None
    return "\n".join(b.text for b in resp.content if b.type == "text").strip() or None
